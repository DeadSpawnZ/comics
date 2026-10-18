"""What the views use: which catalogues are available, loading an issue as a proposal, and
MyComicShop's series (the variants the user picks one from, and linking one to an edition)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils.translation import gettext as _

from comics.models import Edition, Editorial, Publishing
from comics.versions import apply_changes

from . import comicvine, metron, mycomicshop
from .errors import Conflict, InvalidLookup, NotConfigured, NotFound, series_required_message
from .mapper import Proposal, SeriesLoaders, build_proposal, match_publishing, ordinal
from .types import SourceIssue, SourceSeries


@dataclass(frozen=True)
class CatalogSource:
    key: str
    name: str
    is_configured: Callable[[], bool]
    # (issue id, series id): the series id is only given (and required) when `requires_series`.
    fetch: Callable[[int, int | None], dict[str, Any]]
    normalize: Callable[[dict[str, Any]], SourceIssue]
    # What the catalogue adds about the issue's series: its ids in other catalogues (to match the
    # publishing), and the first issue and publisher's country, only asked for when a new publishing
    # is proposed (the bool says whether the editorial is missing too).
    fetch_series_ids: Callable[[SourceIssue], SourceSeries]
    fetch_series: Callable[[SourceIssue, bool], SourceSeries]
    # Its issues are only reachable through their series (MyComicShop's IVID needs its TID).
    requires_series: bool = False


# In the order the import box offers them: Metron first (primary), Comic Vine as the fallback, and
# MyComicShop for the variants neither has.
SOURCES = {
    metron.KEY: CatalogSource(
        metron.KEY,
        metron.NAME,
        metron.is_configured,
        lambda issue_id, series_id: metron.fetch_issue(issue_id),
        metron.to_source_issue,
        metron.fetch_series_ids,
        metron.fetch_series,
    ),
    comicvine.KEY: CatalogSource(
        comicvine.KEY,
        comicvine.NAME,
        comicvine.is_configured,
        lambda issue_id, series_id: comicvine.fetch_issue(issue_id),
        comicvine.to_source_issue,
        comicvine.fetch_series_ids,
        comicvine.fetch_series,
    ),
    mycomicshop.KEY: CatalogSource(
        mycomicshop.KEY,
        mycomicshop.NAME,
        mycomicshop.is_configured,
        mycomicshop.fetch_issue,
        mycomicshop.normalize,
        mycomicshop.fetch_series_ids,
        mycomicshop.fetch_series_details,
        requires_series=True,
    ),
}


def source_choices() -> list[dict[str, Any]]:
    """[{"key", "name", "enabled", "requires_series"}] for every catalogue, for the import box (a
    source that requires its series is loaded by series: the user picks the issue in a modal)."""
    return [
        {
            "key": source.key,
            "name": source.name,
            "enabled": source.is_configured(),
            "requires_series": source.requires_series,
        }
        for source in SOURCES.values()
    ]


def load_proposal(source_key: str, issue_id: int, series_id: int | None = None) -> Proposal:
    """The catalogue's issue `issue_id` as a proposal for the edition form; `series_id` is the
    issue's series in that catalogue, required by the sources that need it (requires_series) and
    ignored by the others. Raises a comics.catalog_sources.errors.CatalogError with a message for
    the user when it cannot."""
    source = SOURCES[source_key]
    if not source.is_configured():
        raise NotConfigured(_("%(source)s is not configured.") % {"source": source.name})
    if source.requires_series and not series_id:
        raise InvalidLookup(series_required_message(source.name))
    source_issue = source.normalize(source.fetch(issue_id, series_id if source.requires_series else None))
    loaders = SeriesLoaders(
        ids=lambda: source.fetch_series_ids(source_issue),
        details=lambda with_publisher: source.fetch_series(source_issue, with_publisher),
    )
    return build_proposal(source_issue, loaders)


def mycomicshop_series(tid: int) -> dict[str, Any]:
    """MyComicShop's series `tid` for the variants modal: the series (with the Comi publishing it
    matches, or None) and its variants grouped by issue number in page order.

    Each variant says how it relates to Comi:
    - `existing`: the edition that already has its IVID (a firm match), or None.
    - `probable`: when there is no firm match, the editions of the matched publishing that are
      probably this cover: same number, same variant letter (MyComicShop's suffix, "A" when it has
      none), exclusive (store or event) only when the variant is one, and, when MyComicShop states
      a printing, the same printing; only editions with no IVID yet. A list (usually of one, ordered by printing), empty when there is none. MyComicShop
      letters its variants itself, so a letter match is only a probability: the user confirms it
      by linking (link_mycomicshop_edition).

    Queries do not grow with the variants: one for the editions holding the IVIDs, the publishing
    match (Editorial.objects.matching and match_publishing, at most a handful, see those) and one
    for every candidate edition. Raises a CatalogError like load_proposal."""
    series = mycomicshop.fetch_series(tid)
    ivids = [variant.ivid for variant in series.variants]
    existing = {
        edition.mycomicshop_id: edition
        for edition in Edition.objects.filter(mycomicshop_id__in=ivids).select_related("publishing")
    }
    publishing = _mycomicshop_publishing(series)
    probable = _probable_editions(publishing, [variant for variant in series.variants if variant.ivid not in existing])
    issues: dict[str, list[dict[str, Any]]] = {}
    for variant in series.variants:
        issues.setdefault(variant.number, []).append(
            _mycomicshop_variant(variant, existing.get(variant.ivid), probable.get(variant.ivid, []))
        )
    return {
        "series": {
            "tid": series.tid,
            "title": series.title,
            "year": series.year,
            "publisher": series.publisher or series.publisher_short,
            "url": mycomicshop.series_url(tid),
            "publishing": {"id": publishing.pk, "label": publishing.select_label} if publishing else None,
        },
        "issues": [{"number": number, "variants": variants} for number, variants in issues.items()],
    }


def _mycomicshop_publishing(series: mycomicshop.McsSeries) -> Publishing | None:
    """The Comi publishing of the series, matched as the importer does (title, year, language "en"
    and the editorial of its publisher); None when none or several fit."""
    if not series.variants:
        return None
    # The series-level values (title, year, publisher, language) are the same for every variant.
    source_issue = mycomicshop.to_source_issue(series, series.variants[0])
    editorial = Editorial.objects.matching(source_issue.publisher) if source_issue.publisher else None
    publishing, _candidates = match_publishing(source_issue, editorial)
    return publishing


def _probable_editions(
    publishing: Publishing | None, variants: list[mycomicshop.McsVariant]
) -> dict[int, list[Edition]]:
    """{IVID: the editions of `publishing` that are probably that variant} (see mycomicshop_series),
    in one query; variants without any are left out."""
    numbers = {variant.number for variant in variants if variant.number}
    if publishing is None or not numbers:
        return {}
    by_cover: dict[tuple[str, str], list[Edition]] = {}
    editions = Edition.objects.filter(publishing=publishing, number__in=numbers, mycomicshop_id__isnull=True)
    for edition in editions.order_by("printing", "pk"):
        edition.publishing = publishing  # already loaded: short_name needs no query
        by_cover.setdefault((edition.number.strip(), edition.variant.strip().upper()), []).append(edition)
    probable: dict[int, list[Edition]] = {}
    for variant in variants:
        found = by_cover.get((variant.number, variant.suffix.strip().upper() or "A"), [])
        if variant.details.printing:
            printing = ordinal(variant.details.printing)
            found = [edition for edition in found if edition.printing == printing]
        # An exclusive (store or event) is only probably an exclusive, and a regular cover a regular one.
        exclusive = bool(variant.details.stores or variant.details.event)
        found = [edition for edition in found if _is_exclusive(edition) == exclusive]
        if found:
            probable[variant.ivid] = found
    return probable


def _is_exclusive(edition: Edition) -> bool:
    """Whether the edition is a store or event exclusive (a regular cover is never a candidate for one)."""
    return bool(edition.retailer_exclusive.strip() or edition.event_exclusive.strip())


def existing_edition(edition: Edition) -> dict[str, Any]:
    """An edition as the variants modal shows it: {"id", "label", "url"} (its Gestión page)."""
    return {
        "id": edition.pk,
        "label": edition.short_name,
        "url": reverse("manage_edition_edit", args=[edition.pk]),
    }


def _probable_edition(edition: Edition) -> dict[str, Any]:
    """existing_edition() plus "cover" ("#1 L", with the printing when it is not the first) and
    "thumb_url" (its thumbnail, "" when it has none)."""
    cover = f"#{edition.number} {edition.variant_label}".strip()
    if edition.printing != Edition.PrintingChoices.FIRST:
        cover = f"{cover} · {edition.printing}"
    return {
        **existing_edition(edition),
        "cover": cover,
        "thumb_url": edition.thumbnail.url if edition.thumbnail else "",
    }


def _mycomicshop_variant(
    variant: mycomicshop.McsVariant, edition: Edition | None, probable: list[Edition]
) -> dict[str, Any]:
    details = variant.details
    when = f"{variant.year:04d}-{variant.month:02d}" if variant.year and variant.month else str(variant.year or "")
    return {
        "ivid": variant.ivid,
        "label": variant.label,
        "variant": variant.suffix,
        "thumb_url": variant.thumb_url,
        "cover_url": variant.cover_url,
        "date": when,  # "2023-02", "2023" (only the year) or ""
        "price": str(details.price) if details.price is not None else None,
        "note": details.note,
        "ratio": details.ratio,
        "limited_to": str(details.print_run) if details.print_run else "",  # same shape as the proposal
        "printing": ordinal(details.printing) if details.printing else "",
        "retailer_exclusive": ", ".join(details.stores),
        "event_exclusive": details.event,
        "existing": existing_edition(edition) if edition else None,
        "probable": [] if edition else [_probable_edition(item) for item in probable],
    }


def link_mycomicshop_edition(edition_id: int, ivid: int, user: User) -> tuple[Edition, bool]:
    """Store MyComicShop's cover `ivid` on the edition `edition_id` (the user confirmed a probable
    match of the variants modal), so it becomes a firm match. Returns (the edition, whether it
    changed): linking the IVID it already has changes nothing. The change is saved through
    comics.versions.apply_changes, so its version records `user` and when.

    Raises NotFound when the edition does not exist, and Conflict when it already has another IVID
    or another edition has this one."""
    with transaction.atomic():
        # Locked: two links racing for the same edition cannot both pass the checks below.
        edition = Edition.objects.select_for_update().filter(pk=edition_id).first()
        if edition is None:
            raise NotFound(_("The edition does not exist: it may have been deleted."))
        if edition.mycomicshop_id == ivid:
            return edition, False
        if edition.mycomicshop_id is not None:
            raise Conflict(
                _("%(edition)s is already linked to another MyComicShop cover (%(id)s).")
                % {"edition": edition.short_name, "id": edition.mycomicshop_id}
            )
        owner = Edition.objects.filter(mycomicshop_id=ivid).select_related("publishing").first()
        if owner is not None:
            raise Conflict(_("%(edition)s already has this MyComicShop ID.") % {"edition": owner.short_name})
        reason = _("Linked to the MyComicShop cover %(id)s.") % {"id": ivid}
        try:
            apply_changes(edition, {"mycomicshop_id": ivid}, user=user, reason=reason)
        except ValidationError as exc:
            raise Conflict(" ".join(exc.messages)) from None
        except IntegrityError:  # another edition took the IVID after the check above
            raise Conflict(_("Another edition already has this MyComicShop ID.")) from None
    return edition, True
