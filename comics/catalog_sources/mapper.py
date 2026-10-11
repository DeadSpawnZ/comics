"""From a catalogue's issue to a proposal for the Gestión edition form.

Each client turns its JSON into a SourceIssue (the neutral input); build_proposal() matches it
against Comi's catalogue and returns a Proposal: the form values, what matched (ids), what did not
(names), warnings and the covers. Nothing is created or saved here: the form is where the user
reviews the proposal, and saving the form is what writes it.

Matching rules:

- Editorial: by name, case-insensitively.
- Publishing: by title, case-insensitively; then the language when the source knows it and the
  year the series began when it knows it (both strict: another language or year is another
  publishing); then the series number ("1st", "2nd"...) and the editorial, which only narrow the
  candidates when some of them fit. Only a single remaining publishing is selected; otherwise the
  title matches are offered as candidates.
- Issue: by the external ids first (Metron, then Comic Vine), else by publishing and number.
- Artists: by name, ignoring case, accents and repeated spaces.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from django.db.models import Q
from django.utils.translation import gettext as _

from comics.models import Artist, Edition, Editorial, Issue, Publishing

from .http import is_allowed_cover_url
from .types import SourceCredit, SourceIssue, SourceVariant

MAX_CANDIDATES = 10

# Series types (Metron) that clearly name an Edition format, by their letters only (lowercase).
FORMAT_BY_SERIES_TYPE = {
    "singleissue": Edition.FormatChoices.SINGLE_ISSUE,
    "ongoingseries": Edition.FormatChoices.SINGLE_ISSUE,
    "limitedseries": Edition.FormatChoices.SINGLE_ISSUE,
    "miniseries": Edition.FormatChoices.SINGLE_ISSUE,
    "maxiseries": Edition.FormatChoices.SINGLE_ISSUE,
    "oneshot": Edition.FormatChoices.SINGLE_ISSUE,
    "annual": Edition.FormatChoices.SINGLE_ISSUE,
    "annualseries": Edition.FormatChoices.SINGLE_ISSUE,
    "cancelledseries": Edition.FormatChoices.SINGLE_ISSUE,
    "tradepaperback": Edition.FormatChoices.TRADE_PAPERBACK,
    "hardcover": Edition.FormatChoices.HARDCOVER,
    "omnibus": Edition.FormatChoices.HARDCOVER,
    "digitalchapter": Edition.FormatChoices.DIGITAL,
    "digitalchapters": Edition.FormatChoices.DIGITAL,
    "magazine": Edition.FormatChoices.MAGAZINE,
}

RATIO_PATTERN = re.compile(r"(?<![\d:])(\d{1,3}):(\d{1,3})(?![\d:])")
PRINTING_PATTERN = re.compile(r"(?i)\b(\d{1,2})\s*(?:st|nd|rd|th)\s+print")


def ordinal(number: int) -> str:
    """1 -> "1st", 2 -> "2nd", 11 -> "11th" (how Publishing.serie names a volume)."""
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def name_key(name: str) -> str:
    """A name compared ignoring case, accents and repeated spaces."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


# ---------- Proposal ----------


@dataclass
class ProposedArtist:
    id: int
    name: str


@dataclass
class ProposedVariant:
    """Another cover of the issue, offered to fill the form with instead of the main one."""

    letter: str  # a suggestion (B, C...): the catalogues do not letter their variants
    name: str
    cover_url: str
    cover_price: str | None  # None: same as the main cover
    ratio: str
    printing: str  # an Edition.PrintingChoices value; "" when its name says one Comi does not have (7th...)
    cover_artists: list[ProposedArtist]
    unmatched_cover_artists: list[str]


@dataclass
class Proposal:
    source: str
    source_name: str
    source_id: int | None
    source_url: str
    identifiers: dict[str, int | None]
    edition: dict[str, str]  # Gestión edition form values (only what the source knows)
    synopsis: str
    publishing: dict[str, Any] | None  # {"id", "label"} of the matched publishing
    publishing_candidates: list[dict[str, Any]]
    issue: dict[str, Any] | None  # {"id", "label", "publishing_id", "number"} of the matched issue
    editorial: dict[str, Any] | None
    creators: list[ProposedArtist]
    cover_artists: list[ProposedArtist]
    cover_url: str
    variants: list[ProposedVariant]
    unmatched: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_proposal(source_issue: SourceIssue) -> Proposal:
    """Match `source_issue` against Comi's catalogue (a fixed number of queries)."""
    warnings: list[str] = []
    editorial = None
    if source_issue.publisher:
        editorial = Editorial.objects.filter(name__iexact=source_issue.publisher).first()
    publishing, candidates = match_publishing(source_issue, editorial)
    issue, conflicts = match_issue(source_issue, publishing)
    for other in conflicts:
        warnings.append(
            _("%(issue)s already has one of these ids: saving them on another issue will fail.") % {"issue": other}
        )

    artists = ArtistMatcher()
    creators, unmatched_creators = artists.match(credit.name for credit in source_issue.credits if credit.is_story)
    cover_credits = [credit for credit in source_issue.credits if credit.is_cover]
    if source_issue.variants:
        # The catalogue lists the artists of every cover together; the main cover is usually
        # drawn by the interior artists, so those are proposed for it.
        main_cover = [credit.name for credit in cover_credits if credit.is_story]
        warnings.append(
            _(
                "%(source)s lists the artists of all the covers together: check the cover artists of "
                "the cover you save."
            )
            % {"source": source_issue.source_name}
        )
    else:
        main_cover = [credit.name for credit in cover_credits]
    cover_artists, unmatched_cover_artists = artists.match(main_cover)

    variants = [
        _variant(variant, index, cover_credits, artists) for index, variant in enumerate(source_issue.variants)
    ]

    cover_url = source_issue.image if is_allowed_cover_url(source_issue.image) else ""
    if source_issue.image and not cover_url:
        warnings.append(_("The cover is on a server Comi does not download from: upload it yourself."))

    return Proposal(
        source=source_issue.source,
        source_name=source_issue.source_name,
        source_id=source_issue.source_id,
        source_url=source_issue.source_url if source_issue.source_url.startswith("https://") else "",
        identifiers={
            "metron_id": source_issue.metron_id,
            "comicvine_id": source_issue.comicvine_id,
            "gcd_id": source_issue.gcd_id,
        },
        edition=_edition_values(source_issue, warnings),
        synopsis=source_issue.description,
        publishing={"id": publishing.pk, "label": publishing.select_label} if publishing else None,
        publishing_candidates=[{"id": item.pk, "label": item.select_label} for item in candidates],
        issue=(
            {"id": issue.pk, "label": str(issue), "publishing_id": issue.publishing_id, "number": issue.number}
            if issue
            else None
        ),
        editorial={"id": editorial.pk, "name": editorial.name} if editorial else None,
        creators=creators,
        cover_artists=cover_artists,
        cover_url=cover_url,
        variants=variants,
        unmatched={
            "editorial": source_issue.publisher if source_issue.publisher and not editorial else "",
            "publishing": _series_description(source_issue) if not publishing else "",
            "creators": unmatched_creators,
            "cover_artists": unmatched_cover_artists,
        },
        warnings=warnings,
    )


def _series_description(source_issue: SourceIssue) -> str:
    """How the unmatched series is shown, e.g. "Batman (2016) 3rd · EN"."""
    text = source_issue.series_name
    if source_issue.series_year:
        text += f" ({source_issue.series_year})"
    if source_issue.series_volume:
        text += f" {ordinal(source_issue.series_volume)}"
    if source_issue.language:
        text += f" · {source_issue.language.upper()}"
    return text


def _edition_values(source_issue: SourceIssue, warnings: list[str]) -> dict[str, str]:
    # A catalogue's issue is the original publication: the main cover (A) of the first printing.
    values = {"variant": "A", "printing": str(Edition.PrintingChoices.FIRST)}
    if source_issue.number:
        values["number"] = source_issue.number
        if len(source_issue.number) > Edition._meta.get_field("number").max_length:
            warnings.append(
                _("The number “%(number)s” is longer than Comi allows.") % {"number": source_issue.number}
            )
    if source_issue.store_date:
        values["release_date"] = source_issue.store_date.isoformat()
    elif source_issue.cover_date:
        values["release_date"] = source_issue.cover_date.isoformat()
        warnings.append(_("Only the cover date is known: the release date is usually a few months earlier."))
    if source_issue.price is not None:
        values["cover_price"] = str(source_issue.price)
        if source_issue.price_currency and source_issue.price_currency.upper() != "USD":
            warnings.append(
                _("The cover price is in %(currency)s.") % {"currency": source_issue.price_currency.upper()}
            )
    if source_issue.series_type:
        format_value = FORMAT_BY_SERIES_TYPE.get(re.sub(r"[^a-z]", "", source_issue.series_type.lower()))
        if format_value:
            values["format"] = str(format_value)
        else:
            warnings.append(
                _("The series type “%(type)s” has no matching format: choose it yourself.")
                % {"type": source_issue.series_type}
            )
    return values


def match_publishing(
    source_issue: SourceIssue, editorial: Editorial | None
) -> tuple[Publishing | None, list[Publishing]]:
    """(the publishing, when exactly one fits; else None, and the title matches as candidates)."""
    if not source_issue.series_name:
        return None, []
    by_title = list(
        Publishing.objects.filter(publishing_title__iexact=source_issue.series_name)
        .prefetch_related("editorials")
        .order_by("year", "serie")[: MAX_CANDIDATES * 5]
    )
    remaining = by_title
    if source_issue.language:
        remaining = [item for item in remaining if item.language == source_issue.language]
    if source_issue.series_year:
        remaining = [item for item in remaining if item.year == source_issue.series_year]
    if source_issue.series_volume:
        serie = ordinal(source_issue.series_volume)
        remaining = [item for item in remaining if item.serie.strip().lower() == serie] or remaining
    if editorial is not None:
        remaining = [item for item in remaining if editorial in item.editorials.all()] or remaining
    if len(remaining) == 1:
        return remaining[0], []
    return None, (remaining or by_title)[:MAX_CANDIDATES]


def match_issue(source_issue: SourceIssue, publishing: Publishing | None) -> tuple[Issue | None, list[Issue]]:
    """(the issue, other issues that already hold one of the source's unique ids)."""
    lookups = Q()
    if source_issue.metron_id:
        lookups |= Q(metron_id=source_issue.metron_id)
    if source_issue.comicvine_id:
        lookups |= Q(comicvine_id=source_issue.comicvine_id)
    by_id = list(Issue.objects.filter(lookups).select_related("publishing")) if lookups else []
    by_id.sort(key=lambda item: item.metron_id != source_issue.metron_id)  # Metron's match first
    issue = by_id[0] if by_id else None
    if issue is None and publishing is not None and source_issue.number:
        issue = (
            Issue.objects.filter(publishing=publishing, number=source_issue.number).select_related("publishing").first()
        )
    conflicts = [item for item in by_id if issue is None or item.pk != issue.pk]
    return issue, conflicts


class ArtistMatcher:
    """Matches names against every artist, loaded once (one query, only ids and names)."""

    def __init__(self) -> None:
        self._by_key: dict[str, tuple[int, str]] | None = None

    def _artists(self) -> dict[str, tuple[int, str]]:
        if self._by_key is None:
            self._by_key = {}
            for pk, name in Artist.objects.order_by("pk").values_list("pk", "name"):
                self._by_key.setdefault(name_key(name), (pk, name))
        return self._by_key

    def match(self, names: Iterable[str]) -> tuple[list[ProposedArtist], list[str]]:
        """(the matched artists, the names that matched none), each once, in the given order."""
        matched: list[ProposedArtist] = []
        unmatched: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = name_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            found = self._artists().get(key)
            if found:
                matched.append(ProposedArtist(id=found[0], name=found[1]))
            else:
                unmatched.append(name)
        return matched, unmatched


def variant_letter(index: int) -> str:
    """0 -> "B", 24 -> "Z", 25 -> "AA"...: the main cover is A."""
    number = index + 2  # 1 = A
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _variant(
    variant: SourceVariant, index: int, cover_credits: list[SourceCredit], artists: ArtistMatcher
) -> ProposedVariant:
    """A variant's values. Its ratio and printing are read from its name when it says them (e.g.
    "2nd Printing 1:25 Nick Dragotta Virgin Variant"; a name without a printing is a first printing),
    and its cover artists are the cover credits named in it."""
    ratio_match = RATIO_PATTERN.search(variant.name)
    printing = str(Edition.PrintingChoices.FIRST)
    printing_match = PRINTING_PATTERN.search(variant.name)
    if printing_match:
        value = ordinal(int(printing_match.group(1)))
        printing = value if value in Edition.PrintingChoices.values else ""
    name = f" {name_key(variant.name)} "
    named = [credit.name for credit in cover_credits if f" {name_key(credit.name)} " in name]
    cover_artists, unmatched = artists.match(named)
    return ProposedVariant(
        letter=variant_letter(index),
        name=variant.name,
        cover_url=variant.image if is_allowed_cover_url(variant.image) else "",
        cover_price=str(variant.price) if variant.price is not None else None,
        ratio=f"{ratio_match.group(1)}:{ratio_match.group(2)}" if ratio_match else "",
        printing=printing,
        cover_artists=cover_artists,
        unmatched_cover_artists=unmatched,
    )
