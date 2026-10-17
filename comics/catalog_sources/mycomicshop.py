"""MyComicShop (https://www.mycomicshop.com), the catalogue of the variants Metron and Comic Vine
miss (retailer incentives, store and convention exclusives...).

It has no API: a series (TID) is one server-rendered HTML page with every issue and variant (IVID),
fetched with the same POST the site's own search form sends (a GET gets an Imperva JavaScript
challenge instead). Each variant has a free-text paragraph with its note ("Limited 1 for 25
Retailer Incentive Variant Cover."), the credits, the synopsis and the cover price, parsed here with
regular expressions. The credits come in two styles:

- sentences: "Written by X. Art by Y (pencils) and Z (inks). Cover by W.";
- solicitation codes: "(W) X (A) Y (CA) Z Synopsis…", where nothing separates the last name from the
  synopsis. The synopsis starts the same way in every variant of an issue, so the other variants
  tell where the name ends; when none does, the name is guessed (two words) and flagged uncertain.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag
from django.core.cache import cache
from django.utils.translation import gettext as _

from .errors import AuthFailed, CatalogError, InvalidLookup, NotFound, Unavailable, series_required_message
from .http import post_html, throttle
from .types import (
    PRINTING_PATTERN,
    RATIO_PATTERN,
    SourceCredit,
    SourceIssue,
    SourceSeries,
    parse_decimal,
    parse_int,
)

KEY = "mycomicshop"
NAME = "MyComicShop"
SEARCH_URL = "https://www.mycomicshop.com/search"
SERIES_CACHE_SECONDS = 60 * 60
REQUESTS_PER_MINUTE = 6  # no published limit: stay well below anything that looks like a crawler
# What only an Imperva challenge page has instead of the results (real pages load it too).
CHALLENGE_MARKER = "_Incapsula_Resource"

# ---------- Page structure ----------

NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
LABEL_PATTERN = re.compile(r"(?P<number>\d+(?:\.\d+)?)(?P<suffix>.*)")
TITLE_PATTERN = re.compile(r"(?P<title>.*?)\s*\((?P<year>\d{4})\s+(?P<publisher>[^()]+)\)")
IMAGE_PATTERN = re.compile(r"https://media\.mycomicshop\.com/n_iv/\d+/(?P<file>[\w-]+\.(?:jpe?g|png|gif|webp))", re.I)
IMAGE_URL = "https://media.mycomicshop.com/n_iv/{size}/{file}"
COVER_SIZE = 600
THUMB_SIZE = 120

# ---------- Free text ----------

PRICE_PATTERN = re.compile(r"(?i)\bcover\s+price\s*\$\s*(?P<price>\d[\d,]*(?:\.\d+)?)\.?")
# A full stop that may end a sentence; split_sentences decides whether it does.
FULL_STOP = re.compile(r"\.(?=\s|$)")
# Words that end with a full stop without ending the sentence when a lowercase word follows
# ("John Romita Jr. and Klaus Janson").
ABBREVIATIONS = frozenset({"Jr", "Sr", "Dr", "Mr", "Mrs", "Ms", "St", "Vol", "No", "vs"})

# Sentence style: "Written by X.", "Story and art by X.", "Art by X (pencils), Y (inks).". "Cover art"
# goes before "cover" and "art" so that it is read as one phrase (a cover credit, not a story one).
PHRASE_ROLES = {
    "cover art": "Cover",
    "written": "Writer",
    "drawn": "Artist",
    "story": "Story",
    "script": "Script",
    "plot": "Plot",
    "art": "Artist",
    "pencils": "Penciller",
    "inks": "Inker",
    "colors": "Colorist",
    "colours": "Colorist",
    "letters": "Letterer",
    "cover": "Cover",
}
_PHRASE = "(?:" + "|".join(phrase.replace(" ", r"\s+") for phrase in PHRASE_ROLES) + ")"
SENTENCE_CREDIT = re.compile(rf"(?i)(?P<phrases>{_PHRASE}(?:\s*(?:,|and|&)\s*{_PHRASE})*)\s+by\s+(?P<names>.+?)\.?")
# Roles in parentheses after a name ("Greg Capullo (pencils)"); others are kept as written.
PAREN_ROLES = {
    "pencils": "Penciller",
    "inks": "Inker",
    "colors": "Colorist",
    "colours": "Colorist",
    "letters": "Letterer",
    "script": "Script",
    "plot": "Plot",
    "layouts": "Layouts",
    "breakdowns": "Breakdowns",
    "finishes": "Finishes",
}
NAME_WITH_ROLE = re.compile(r"(?P<name>.+?)\s*\((?P<roles>[^()]+)\)")
# ", ", ", and ", " and ", " & " between names, but not inside parentheses ("X (pencils, inks)").
NAME_SEPARATOR = re.compile(r"(?:\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*)(?![^()]*\))")
ROLE_SEPARATOR = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*")

# Solicitation style: "(W) X (A) Y (CA) Z", also combined as "(A/CA) Y".
CODE_ROLES = {
    "W": "Writer",
    "A": "Artist",
    "CA": "Cover",
    "C": "Colorist",
    "L": "Letterer",
    "P": "Penciller",
    "I": "Inker",
}
_CODE = "(?:CA|W|A|C|L|P|I)"
CODE_PATTERN = re.compile(rf"\((?P<codes>{_CODE}(?:\s*/\s*{_CODE})*)\)")
# A text between two codes longer than this is not a list of names: the synopsis has begun.
MAX_NAMES_WORDS = 12
# How many words the last name may have, and how many words must match to recognise the synopsis.
MAX_NAME_WORDS = 6
MATCH_WORDS = 6
MIN_MATCH_WORDS = 3
GUESSED_NAME_WORDS = 2

# The variant note.
RATIO_NOTE_PATTERN = re.compile(r"(?i)\b1\s+(?:for|in|per)\s+(?P<count>\d[\d,]*)\b")
PRINT_RUN_PATTERN = re.compile(r"(?i)\blimited\s+to\s+(?P<count>\d[\d,]*)\s+(?:copies|copy|prints)\b")
# "<store(s)> [joint] Exclusive …" or "…, exclusive to <store>."
EXCLUSIVE_PATTERN = re.compile(r"(?i)(?P<name>.+?)\s+(?:joint\s+)?exclusive\b(?!\s+to\b)")
EXCLUSIVE_WORD = re.compile(r"(?i)\bexclusive\b")
EXCLUSIVE_TO_PATTERN = re.compile(r"(?i)\bexclusive\s+to\s+(?P<name>.+?)\.?$")
EVENT_PATTERN = re.compile(
    r"\b(?:Comic[\s-]?Con|Comicon|Con|Expo|Fest|Festival|Convention|NYCC|SDCC|ECCC|C2E2|WonderCon|MegaCon)\b"
)
# What the solicitation codes put where a cover artist goes on covers no artist drew ("(CA) Blank
# Authentix", "(CA) Blind Bag"): not a credit.
NOT_AN_ARTIST = re.compile(r"(?i)^(?:blank|blind\s+bag)\b|\b(?:authentix|sketch)\b")
NOTE_COVER_PATTERN = re.compile(r"(?i)\bcover\s+by\s+(?P<names>.+?)(?:\s+-\s+.*|,?\s+exclusive\b.*)?\.?$")
# A sentence before the first credit that is a variant note rather than the synopsis (used when the
# text has no credits, so nothing else tells the note from the synopsis): one with a ratio, print run,
# printing or exclusive, or a short one with one of NOTE_WORDS and one of NOTE_COVER_WORDS ("Foil
# cover."), so that "This special edition collects…" stays in the synopsis.
NOTE_WORDS = re.compile(
    r"(?i)\b(?:variant|cover|edition|printing|reprints?|exclusive|incentive|limited|foil|virgin|sketch)\b"
)
NOTE_COVER_WORDS = re.compile(r"(?i)\b(?:variant|cover|printing)\b")
MAX_NOTE_WORDS = 12
STORE_SEPARATOR = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+")


# ---------- Results ----------


@dataclass
class McsCredit(SourceCredit):
    """A credit; `uncertain` (SourceCredit) when its name was guessed (see _last_name_length)."""


@dataclass
class McsDetails:
    """What a variant's free text says. Empty strings and None mean it does not say it."""

    text: str  # the whole paragraph, whitespace collapsed
    note: str  # what comes before the credits, as written
    ratio: str = ""  # "1:25"
    print_run: int | None = None  # "Limited to 1,200 copies" -> 1200
    printing: int | None = None  # "2nd Printing" -> 2
    stores: list[str] = field(default_factory=list)  # store exclusive
    event: str = ""  # convention exclusive
    credits: list[McsCredit] = field(default_factory=list)
    synopsis: str = ""
    price: Decimal | None = None

    @property
    def uncertain(self) -> bool:
        return any(credit.uncertain for credit in self.credits)


@dataclass
class McsVariant:
    """An issue or variant (IVID) of a series page, with its parsed free text."""

    ivid: int
    label: str  # "1A", "1RI.A"
    number: str  # "1"
    suffix: str  # "A", "RI.A", "" when the label has no leading number
    year: int | None
    month: int | None  # MyComicShop only gives the month (sometimes only the year, or nothing)
    cover_url: str  # 600 px
    thumb_url: str  # 120 px
    details: McsDetails

    @property
    def cover_date(self) -> date | None:
        return date(self.year, self.month, 1) if self.year and self.month else None


@dataclass
class McsSeries:
    """A series page (TID): the series as its title says it, and every variant it lists."""

    tid: int | None
    title: str
    year: int | None
    publisher_short: str  # as in the title, e.g. "IDW"
    publisher: str  # full name, e.g. "IDW Publishing"
    variants: list[McsVariant]


# ---------- Download ----------


def is_configured() -> bool:
    return True  # no key needed


def series_url(tid: int) -> str:
    return f"{SEARCH_URL}?TID={int(tid)}"


def _not_found(tid: int) -> str:
    return _("%(source)s has no series with the id %(id)s.") % {"source": NAME, "id": tid}


def _blocked() -> str:
    return _("%(source)s is refusing automated requests right now. Try again later.") % {"source": NAME}


def fetch_series(tid: int) -> McsSeries:
    """The series page of `tid`, parsed. Cached for an hour per TID."""
    tid = int(tid)
    if tid <= 0:
        raise NotFound(_not_found(tid))
    cache_key = f"catalog:{KEY}:series:{tid}"
    series = cache.get(cache_key)
    if series is not None:
        return series
    throttle(KEY, REQUESTS_PER_MINUTE, NAME)
    try:
        page = post_html(series_url(tid), source=NAME, not_found=_not_found(tid))
    except AuthFailed:  # a 401/403 here is Imperva, not credentials
        raise Unavailable(_blocked()) from None
    series = parse_series(page)
    if not series.variants:
        if CHALLENGE_MARKER in page:
            raise Unavailable(_blocked())
        raise NotFound(_not_found(tid))
    series.tid = tid  # the page answered for the TID that was asked for, whatever its links say
    cache.set(cache_key, series, SERIES_CACHE_SECONDS)
    return series


# ---------- Import (the catalogue source, see comics.catalog_sources.service) ----------


def fetch_issue(ivid: int, tid: int | None) -> dict[str, Any]:
    """{"series": the McsSeries `tid`, "variant": its McsVariant `ivid`}. The IVID is only found
    through its series page, so the TID is required."""
    if not tid:
        raise InvalidLookup(series_required_message(NAME))
    series = fetch_series(tid)
    variant = next((item for item in series.variants if item.ivid == int(ivid)), None)
    if variant is None:
        raise NotFound(
            _("%(source)s has no issue with the id %(id)s in the series %(series)s.")
            % {"source": NAME, "id": ivid, "series": tid}
        )
    return {"series": series, "variant": variant}


def normalize(data: dict[str, Any]) -> SourceIssue:
    """fetch_issue's answer as the mapper's neutral input."""
    return to_source_issue(data["series"], data["variant"])


def to_source_issue(series: McsSeries, variant: McsVariant) -> SourceIssue:
    """One variant of a series page as the mapper's neutral input: one cover per import (no
    `variants`), with the edition-level values its note gives. MyComicShop only has the cover month,
    so the mapper warns that the release date is a cover date."""
    details = variant.details
    return SourceIssue(
        source=KEY,
        source_name=NAME,
        source_id=variant.ivid,
        source_url=f"{series_url(series.tid)}#{variant.ivid}" if series.tid else "",
        publisher=series.publisher or series.publisher_short,
        series_name=series.title,
        series_volume=None,
        series_year=series.year,
        series_type="",
        language="en",
        number=variant.number,
        store_date=None,
        cover_date=variant.cover_date,
        price=details.price,
        price_currency="USD",
        description=details.synopsis,
        image=variant.cover_url,
        credits=list(details.credits),
        variants=[],
        metron_id=None,
        comicvine_id=None,
        gcd_id=None,
        source_series_id=series.tid,
        variant=variant.suffix,
        printing=details.printing,
        ratio=details.ratio,
        limited_to=str(details.print_run) if details.print_run else "",
        retailer_exclusive=", ".join(details.stores),
        event_exclusive=details.event,
        mycomicshop_id=variant.ivid,
    )


def fetch_series_ids(source_issue: SourceIssue) -> SourceSeries:
    """MyComicShop does not say the series' ids in other catalogues."""
    return SourceSeries()


def fetch_series_details(source_issue: SourceIssue, with_publisher: bool) -> SourceSeries:
    """The first issue's cover date, from the same (cached) series page; the publisher's country is
    unknown. Optional: when the page cannot be read again it stays unknown."""
    if not source_issue.source_series_id:
        return SourceSeries()
    try:
        series = fetch_series(source_issue.source_series_id)
    except CatalogError:
        return SourceSeries()
    return SourceSeries(first_issue_cover_date=first_issue_date(series))


def first_issue_date(series: McsSeries) -> date | None:
    """The earliest cover date among the variants of the series' lowest issue number (None when
    none of them has a month)."""
    numbered = [(Decimal(item.number), item) for item in series.variants if NUMBER_PATTERN.fullmatch(item.number)]
    if not numbered:
        return None
    lowest = min(number for number, _item in numbered)
    dates = [item.cover_date for number, item in numbered if number == lowest and item.cover_date]
    return min(dates) if dates else None


# ---------- Page ----------


def parse_series(page: str) -> McsSeries:
    """The series and its variants as the page lists them. Stock tables, eBay listings and the
    "not in stock" paragraph are ignored. A page without variants gives an empty list."""
    soup = BeautifulSoup(page, "html.parser")
    items = [item for item in soup.select("#resultstab ul.results-detailed > li.issue") if _ivid(item)]
    texts = [" ".join(_free_text(item, _ivid(item) or 0).split()) for item in items]
    parts = [_split(text) for text in texts]
    labels = [_label(item) for item in items]
    numbers = [split_label(label)[0] for label in labels]
    by_number: dict[str, list[int]] = defaultdict(list)
    for index, number in enumerate(numbers):
        by_number[number].append(index)
    variants = []
    for index, item in enumerate(items):
        siblings = [parts[other] for other in by_number[numbers[index]] if other != index]
        variants.append(_variant(item, labels[index], _details(texts[index], parts[index], siblings)))

    title_link = soup.select_one("#resultstab li.issue .othercolleft .title a")
    title_text = _text(title_link) if title_link else _text(soup.select_one("h1.top")).removesuffix(" comic books")
    title_match = TITLE_PATTERN.fullmatch(title_text)
    tid = parse_int(_query_value(title_link.get("href", ""), "TID")) if title_link else None
    publisher = ""
    for link in soup.select("#resultstab li.issue .othercolright a[href]"):
        publisher = _query_value(link["href"], "pl")
        if publisher:
            break
    return McsSeries(
        tid=tid,
        title=title_match.group("title") if title_match else title_text,
        year=int(title_match.group("year")) if title_match else None,
        publisher_short=title_match.group("publisher").strip() if title_match else "",
        publisher=publisher,
        variants=variants,
    )


def split_label(label: str) -> tuple[str, str]:
    """("1", "RI.A") for "1RI.A": the leading number and the variant suffix. A label without a
    leading number is all number."""
    match = LABEL_PATTERN.fullmatch(label)
    if not match:
        return label, ""
    return match.group("number"), match.group("suffix").strip()


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag else ""


def _query_value(href: str, name: str) -> str:
    """A query-string value of a link, matching the name case-insensitively (the site mixes TID/tid)."""
    try:
        query = parse_qs(urlsplit(str(href)).query)
    except ValueError:
        return ""
    for key, values in query.items():
        if key.casefold() == name.casefold() and values:
            return values[0].strip()
    return ""


def _ivid(item: Tag) -> int | None:
    anchor = item.find("a", attrs={"name": True})
    return parse_int(anchor["name"]) if anchor else None


def _label(item: Tag) -> str:
    number = item.select_one(".othercolleft .issuenum strong")
    label = _text(number) or _text(item.select_one(".imgcol")).removeprefix("Issue")
    return label.strip().lstrip("#").strip()


def _free_text(item: Tag, ivid: int) -> str:
    """The free-text paragraphs of a variant's stock tab, without the stock table and the "not in
    stock" paragraph (both inside .issuegrades)."""
    tab = item.find(id=f"tab-addtocart-{ivid}")
    if not tab:
        return ""
    contents = tab.select_one(".tabcontents") or tab
    paragraphs = []
    for paragraph in contents.find_all("p"):
        if paragraph.find_parent(class_="issuegrades"):
            continue
        for line_break in paragraph.find_all("br"):
            line_break.replace_with(" ")
        paragraphs.append(" ".join(paragraph.get_text("").split()))
    return " ".join(text for text in paragraphs if text)


def _variant(item: Tag, label: str, details: McsDetails) -> McsVariant:
    number, suffix = split_label(label)
    year = month = None
    for link in item.select(".othercolright a[href]"):
        year_month = _query_value(link["href"], "minyrmo")
        if re.fullmatch(r"\d{6}", year_month) and 1 <= int(year_month[4:]) <= 12:
            year, month = int(year_month[:4]), int(year_month[4:])
            break
        year_only = _query_value(link["href"], "minyr")
        if re.fullmatch(r"\d{4}", year_only):
            year = int(year_only)
            break
    cover_url, thumb_url = _images(item)
    return McsVariant(
        ivid=_ivid(item) or 0,
        label=label,
        number=number,
        suffix=suffix,
        year=year,
        month=month,
        cover_url=cover_url,
        thumb_url=thumb_url,
        details=details,
    )


def _images(item: Tag) -> tuple[str, str]:
    """The 600 px cover and the 120 px thumbnail, built from whichever of the two the page links
    (only images on media.mycomicshop.com over HTTPS)."""
    link = item.select_one(".imgcol a.fancyboxthis")
    candidates = [link.get("href", "")] if link else []
    image = item.select_one(".imgcol img")
    if image:
        candidates.append(image.get("src", ""))
    for url in candidates:
        match = IMAGE_PATTERN.fullmatch(str(url).strip())
        if match:
            file = match.group("file")
            return IMAGE_URL.format(size=COVER_SIZE, file=file), IMAGE_URL.format(size=THUMB_SIZE, file=file)
    return "", ""


# ---------- Free text ----------


@dataclass
class _Parts:
    """A free text split into its parts. In the solicitation style the last name is still glued to
    the synopsis in `tail`, with the roles of its code in `tail_roles`."""

    note: str = ""
    credits: list[McsCredit] = field(default_factory=list)
    synopsis: str = ""
    tail: str = ""
    tail_roles: list[str] = field(default_factory=list)
    price: Decimal | None = None


def parse_free_text(text: str, siblings: Sequence[str] = ()) -> McsDetails:
    """Parse a variant's free text. `siblings` are the free texts of the other variants of the same
    issue: they tell where the synopsis starts after solicitation codes."""
    text = " ".join(text.split())
    parts = _split(text)
    sibling_parts = [_split(" ".join(sibling.split())) for sibling in siblings] if parts.tail else []
    return _details(text, parts, sibling_parts)


def _details(text: str, parts: _Parts, siblings: Sequence[_Parts]) -> McsDetails:
    """The details of a split free text; `siblings` are the other variants of the issue, split."""
    credits = list(parts.credits)
    synopsis = parts.synopsis
    if parts.tail:
        tail = parts.tail.split()
        length, uncertain = _last_name_length(tail, siblings)
        if length is None:
            uncertain = len(tail) > GUESSED_NAME_WORDS
            length = min(GUESSED_NAME_WORDS, len(tail))
        credits += [
            McsCredit(name=name, roles=list(parts.tail_roles), uncertain=uncertain)
            for name in split_names(" ".join(tail[:length]))
        ]
        synopsis = " ".join(tail[length:])
    details = McsDetails(text=text, note=parts.note, synopsis=synopsis, price=parts.price)
    _read_note(details)
    details.credits = _merge_credits(details.credits + credits)
    return details


def _split(text: str) -> _Parts:
    price, body = take_price(text)
    sentences = split_sentences(body)
    first = next((index for index, sentence in enumerate(sentences) if _credit_sentence(sentence)), None)
    code = CODE_PATTERN.search(body)
    sentence_offset = len(" ".join(sentences[:first])) if first is not None else None
    if code and (sentence_offset is None or code.start() < sentence_offset):
        return _split_codes(body, code, price)
    if first is None:
        # No credits to end the note: it is the leading sentences that read like one.
        lead = 0
        while lead < len(sentences) and _is_note(sentences[lead]):
            lead += 1
        return _Parts(note=" ".join(sentences[:lead]), synopsis=" ".join(sentences[lead:]), price=price)
    credits: list[McsCredit] = []
    end = first
    while end < len(sentences):
        match = _credit_sentence(sentences[end])
        if not match:
            break
        credits += sentence_credits(match.group("phrases"), match.group("names"))
        end += 1
    return _Parts(
        note=" ".join(sentences[:first]),
        credits=credits,
        synopsis=" ".join(sentences[end:]),
        price=price,
    )


def _credit_sentence(sentence: str) -> re.Match[str] | None:
    """The SENTENCE_CREDIT match of a credit sentence. One that names an exclusive ("Cover by X,
    exclusive to Y.") is a variant note instead."""
    if EXCLUSIVE_WORD.search(sentence):
        return None
    return SENTENCE_CREDIT.fullmatch(sentence)


def _is_note(sentence: str) -> bool:
    facts = (RATIO_NOTE_PATTERN, RATIO_PATTERN, PRINT_RUN_PATTERN, PRINTING_PATTERN, EXCLUSIVE_TO_PATTERN)
    if any(pattern.search(sentence) for pattern in facts) or EXCLUSIVE_PATTERN.match(sentence):
        return True
    return (
        len(sentence.split()) <= MAX_NOTE_WORDS
        and NOTE_WORDS.search(sentence) is not None
        and NOTE_COVER_WORDS.search(sentence) is not None
    )


def _split_codes(body: str, first: re.Match[str], price: Decimal | None) -> _Parts:
    codes = [first]
    for match in CODE_PATTERN.finditer(body, first.end()):
        if len(body[codes[-1].end() : match.start()].split()) > MAX_NAMES_WORDS:
            break  # a code inside the synopsis
        codes.append(match)
    credits = []
    for code, following in pairwise(codes):
        roles = code_roles(code.group("codes"))
        credits += [
            McsCredit(name=name, roles=list(roles)) for name in split_names(body[code.end() : following.start()])
        ]
    return _Parts(
        note=body[: first.start()].strip(),
        credits=credits,
        tail=body[codes[-1].end() :].strip(),
        tail_roles=code_roles(codes[-1].group("codes")),
        price=price,
    )


def take_price(text: str) -> tuple[Decimal | None, str]:
    """The last "Cover price $X.XX" and the text without it."""
    matches = list(PRICE_PATTERN.finditer(text))
    if not matches:
        return None, text.strip()
    last = matches[-1]
    price = parse_decimal(last.group("price").replace(",", ""))
    return price, " ".join((text[: last.start()] + " " + text[last.end() :]).split())


def split_sentences(text: str) -> list[str]:
    """Sentences ending in ". ", each with its full stop. A full stop after an initial ("J. Scott
    Campbell", "M. Bison"), or followed by a lowercase word, does not end a sentence."""
    sentences = []
    start = 0
    for match in FULL_STOP.finditer(text):
        before = text[start : match.start()].split()
        word = before[-1] if before else ""
        after = text[match.end() :].lstrip()
        initial = re.fullmatch(r"[A-Z]", word) is not None
        abbreviation = word in ABBREVIATIONS and not after[:1].isupper()
        if after and (initial or abbreviation or after[0].islower()):
            continue
        sentences.append(text[start : match.end()].strip())
        start = match.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return sentences


def split_names(text: str) -> list[str]:
    """Names separated by ", ", " and " or " & " (not inside parentheses), without a final period."""
    text = text.strip().removesuffix(".").strip()
    return [name.strip() for name in NAME_SEPARATOR.split(text) if name and name.strip()]


def sentence_credits(phrases: str, names: str) -> list[McsCredit]:
    """The credits of "<phrases> by <names>", e.g. ("Art", "Greg Capullo (pencils) and Todd
    McFarlane (inks)"). A role in parentheses after a name replaces the phrase's role."""
    roles = [PHRASE_ROLES[" ".join(phrase.split()).casefold()] for phrase in re.findall(_PHRASE, phrases, flags=re.I)]
    credits = []
    for name in split_names(names):
        match = NAME_WITH_ROLE.fullmatch(name)
        if match:
            own_roles = [
                PAREN_ROLES.get(role.casefold(), role.strip().title())
                for role in ROLE_SEPARATOR.split(match.group("roles"))
                if role.strip()
            ]
            credits.append(McsCredit(name=match.group("name").strip(), roles=own_roles or list(roles)))
        else:
            credits.append(McsCredit(name=name, roles=list(roles)))
    return credits


def code_roles(codes: str) -> list[str]:
    """["Artist", "Cover"] for "A/CA"."""
    return [CODE_ROLES[code.strip()] for code in codes.split("/")]


def _last_name_length(tail: list[str], siblings: Sequence[_Parts]) -> tuple[int | None, bool]:
    """How many words of `tail` (the last name glued to the synopsis) are the name, and whether
    that is uncertain; (None, True) when no sibling tells.

    A sentence-style sibling's synopsis gives the exact start. Otherwise the code-style siblings
    with a different last name are aligned with `tail`: where they start to match is at most the
    synopsis start, and earlier when the names share their last words ("Jae Lee" and "Jim Lee"
    match from "Lee"), so the latest start wins. It is uncertain when the siblings disagree, or
    when a single sibling tells (nothing can show that its match does not begin inside the name)."""
    exact: list[int] = []
    aligned: list[int] = []
    for other_tail in {sibling.tail for sibling in siblings if sibling.tail}:
        other = other_tail.split()
        shared = _common_prefix(tail, other)
        if shared > MAX_NAME_WORDS or shared >= min(len(tail), len(other)):
            continue  # same name (or same text): it says nothing
        length = _aligned_start(tail, other, max(shared, 1))
        if length:
            aligned.append(length)
    for sibling in siblings:
        if not sibling.tail and sibling.synopsis:
            length = _synopsis_start(tail, sibling.synopsis.split())
            if length:
                exact.append(length)
    if exact:
        length, _count = Counter(exact).most_common(1)[0]
        return length, len(set(exact)) > 1
    if aligned:
        length = max(aligned)
        lone_capitalized = len(aligned) == 1 and length < len(tail) and tail[length][:1].isupper()
        return length, len(set(aligned)) > 1 or lone_capitalized
    return None, True


def _common_prefix(first: list[str], second: list[str]) -> int:
    count = 0
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        count += 1
    return count


def _aligned_start(tail: list[str], other: list[str], lowest: int) -> int | None:
    """The smallest i such that tail[i:] and other[j:] start with the same words, for some j."""
    for start in range(lowest, MAX_NAME_WORDS + 1):
        for other_start in range(lowest, MAX_NAME_WORDS + 1):
            size = min(MATCH_WORDS, len(tail) - start, len(other) - other_start)
            if size >= MIN_MATCH_WORDS and tail[start : start + size] == other[other_start : other_start + size]:
                return start
    return None


def _synopsis_start(tail: list[str], synopsis: list[str]) -> int | None:
    size = min(MATCH_WORDS, len(synopsis))
    if size < MIN_MATCH_WORDS:
        return None
    for start in range(1, MAX_NAME_WORDS + 1):
        if tail[start : start + size] == synopsis[:size]:
            return start
    return None


def _read_note(details: McsDetails) -> None:
    """Fill the ratio, print run, printing, exclusive and the note's cover artists from the note."""
    note = details.note
    if not note:
        return
    ratio = RATIO_NOTE_PATTERN.search(note)
    if ratio:
        details.ratio = f"1:{ratio.group('count').replace(',', '')}"
    else:
        ratio = RATIO_PATTERN.search(note)
        details.ratio = f"{ratio.group(1)}:{ratio.group(2)}" if ratio else ""
    print_run = PRINT_RUN_PATTERN.search(note)
    details.print_run = parse_int(print_run.group("count").replace(",", "")) if print_run else None
    printing = PRINTING_PATTERN.search(note)
    details.printing = int(printing.group(1)) if printing else None
    for sentence in split_sentences(note):
        name = "" if details.stores or details.event else _exclusive_name(sentence)
        if name and EVENT_PATTERN.search(name):
            details.event = name
        elif name:
            details.stores = [store for store in STORE_SEPARATOR.split(name) if store]
        cover = NOTE_COVER_PATTERN.search(sentence)
        if cover:
            details.credits += [McsCredit(name=name, roles=["Cover"]) for name in split_names(cover.group("names"))]


def _exclusive_name(sentence: str) -> str:
    """Who a variant is exclusive to: "<name> [joint] Exclusive …" (only the part after the last
    " - ", e.g. "Foil Cover - Midtown Comics Exclusive") or "…, exclusive to <name>." (only the part
    before the first " - ", e.g. "exclusive to Midtown Comics - Limited to 500 copies."); "" if none."""
    match = EXCLUSIVE_TO_PATTERN.search(sentence)
    if match:
        return match.group("name").split(" - ", 1)[0].strip(" ,-")
    match = EXCLUSIVE_PATTERN.match(sentence)
    if match:
        return match.group("name").rsplit(" - ", 1)[-1].strip(" ,-")
    return ""


def _merge_credits(credits: list[McsCredit]) -> list[McsCredit]:
    """One credit per name (first appearance order) with all its roles, without the cover-only
    credits that name no artist (NOT_AN_ARTIST). A merged name is uncertain only if every
    appearance was."""
    merged: dict[str, McsCredit] = {}
    for credit in credits:
        if credit.is_cover and not credit.is_story and NOT_AN_ARTIST.search(credit.name):
            continue
        key = credit.name.casefold()
        if key not in merged:
            merged[key] = McsCredit(name=credit.name, roles=list(credit.roles), uncertain=credit.uncertain)
            continue
        target = merged[key]
        target.roles += [role for role in credit.roles if role not in target.roles]
        target.uncertain = target.uncertain and credit.uncertain
    return list(merged.values())
