"""What the views use: which catalogues are available, and loading an issue as a proposal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext as _

from . import comicvine, metron
from .errors import NotConfigured
from .mapper import Proposal, build_proposal
from .types import SourceIssue


@dataclass(frozen=True)
class CatalogSource:
    key: str
    name: str
    is_configured: Callable[[], bool]
    fetch: Callable[[int], dict[str, Any]]
    normalize: Callable[[dict[str, Any]], SourceIssue]


# In the order the import box offers them: Metron first (primary), Comic Vine as the fallback.
SOURCES = {
    metron.KEY: CatalogSource(metron.KEY, metron.NAME, metron.is_configured, metron.fetch_issue, metron.to_source_issue),
    comicvine.KEY: CatalogSource(
        comicvine.KEY, comicvine.NAME, comicvine.is_configured, comicvine.fetch_issue, comicvine.to_source_issue
    ),
}


def source_choices() -> list[dict[str, Any]]:
    """[{"key", "name", "enabled"}] for every catalogue, for the import box."""
    return [{"key": source.key, "name": source.name, "enabled": source.is_configured()} for source in SOURCES.values()]


def any_source_enabled() -> bool:
    return any(source.is_configured() for source in SOURCES.values())


def load_proposal(source_key: str, issue_id: int) -> Proposal:
    """The catalogue's issue `issue_id` as a proposal for the edition form. Raises a
    comics.catalog_sources.errors.CatalogError with a message for the user when it cannot."""
    source = SOURCES[source_key]
    if not source.is_configured():
        raise NotConfigured(_("%(source)s is not configured.") % {"source": source.name})
    return build_proposal(source.normalize(source.fetch(issue_id)))
