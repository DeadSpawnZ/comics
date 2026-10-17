"""What the views use: which catalogues are available, loading an issue as a proposal, and
MyComicShop's series (the variants the user picks one from)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext as _

from comics.models import Edition

from . import comicvine, metron, mycomicshop
from .errors import InvalidLookup, NotConfigured, series_required_message
from .mapper import Proposal, SeriesLoaders, build_proposal, ordinal
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
    """MyComicShop's series `tid` for the variants modal: the series and its variants grouped by
    issue number in page order, each with the edition that already has its IVID, if any (one
    query). Raises a CatalogError like load_proposal."""
    series = mycomicshop.fetch_series(tid)
    ivids = [variant.ivid for variant in series.variants]
    existing = {
        edition.mycomicshop_id: edition
        for edition in Edition.objects.filter(mycomicshop_id__in=ivids).select_related("publishing")
    }
    issues: dict[str, list[dict[str, Any]]] = {}
    for variant in series.variants:
        issues.setdefault(variant.number, []).append(_mycomicshop_variant(variant, existing.get(variant.ivid)))
    return {
        "series": {
            "tid": series.tid,
            "title": series.title,
            "year": series.year,
            "publisher": series.publisher or series.publisher_short,
            "url": mycomicshop.series_url(tid),
        },
        "issues": [{"number": number, "variants": variants} for number, variants in issues.items()],
    }


def _mycomicshop_variant(variant: mycomicshop.McsVariant, edition: Edition | None) -> dict[str, Any]:
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
        "existing": (
            {
                "id": edition.pk,
                "label": edition.short_name,
                "url": reverse("manage_edition_edit", args=[edition.pk]),
            }
            if edition
            else None
        ),
    }
