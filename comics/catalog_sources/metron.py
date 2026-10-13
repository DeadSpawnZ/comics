"""Metron (https://metron.cloud), the primary catalogue: REST API with HTTP Basic auth, limited to
about 30 requests per minute. Responses are cached, so loading the same issue again (or another
cover of it) costs no request. Matching the publishing asks for the series too (fetch_series_ids:
its Comic Vine id), and proposing a new publishing (fetch_series) for its issue list and the
publisher; all cached longer, each only when needed."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from .errors import BadResponse, CatalogError, NotConfigured
from .http import get_json, throttle
from .types import (
    SourceCredit,
    SourceIssue,
    SourceSeries,
    SourceVariant,
    parse_date,
    parse_decimal,
    parse_int,
)

KEY = "metron"
NAME = "Metron"
API_URL = "https://metron.cloud/api/"
CACHE_SECONDS = 24 * 60 * 60
SERIES_CACHE_SECONDS = 7 * 24 * 60 * 60  # series and publishers rarely change
REQUESTS_PER_MINUTE = 25  # Metron allows 30; keep a margin


def is_configured() -> bool:
    return bool(settings.METRON_USERNAME and settings.METRON_PASSWORD)


@sensitive_variables("token")
def _get(
    path: str,
    cache_key: str,
    cache_seconds: int,
    not_found: str,
    is_valid: Callable[[dict[str, Any]], bool] = lambda data: True,
) -> dict[str, Any]:
    """GET API_URL + `path`: a JSON object that passes `is_valid`, cached under `cache_key`."""
    data = cache.get(cache_key)
    if data is not None:
        return data
    throttle(KEY, REQUESTS_PER_MINUTE, NAME)
    token = base64.b64encode(f"{settings.METRON_USERNAME}:{settings.METRON_PASSWORD}".encode()).decode("ascii")
    data = get_json(f"{API_URL}{path}", source=NAME, not_found=not_found, headers={"Authorization": f"Basic {token}"})
    if not isinstance(data, dict) or not is_valid(data):
        raise BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": NAME})
    cache.set(cache_key, data, cache_seconds)
    return data


def fetch_issue(issue_id: int) -> dict[str, Any]:
    """The issue as Metron's API returns it (GET /api/issue/<id>/), cached for a day."""
    if not is_configured():
        raise NotConfigured(_("%(source)s is not configured.") % {"source": NAME})
    return _get(
        f"issue/{int(issue_id)}/",
        f"catalog:{KEY}:issue:{issue_id}",
        CACHE_SECONDS,
        _("%(source)s has no issue with the id %(id)s.") % {"source": NAME, "id": issue_id},
        lambda data: isinstance(data.get("series"), dict),
    )


def _optional(
    path: str, cache_key: str, cache_seconds: int, is_valid: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """An answer that only enriches a proposal: {} when it cannot be had (missing, rate limit,
    unexpected shape...)."""
    try:
        not_found = _("%(source)s sent an answer Comi cannot read.") % {"source": NAME}
        return _get(path, cache_key, cache_seconds, not_found, is_valid)
    except CatalogError:
        return {}


def first_issue(issues: Any) -> dict[str, Any]:
    """The earliest issue of a series issue list (by cover date, else store date), or {}. Only the
    first page of the list is read: Metron lists a series' issues by date, so it holds the first
    issue; taking the earliest date of the page does not depend on the order within it."""
    dated = []
    for item in issues if isinstance(issues, list) else []:
        if not isinstance(item, dict):
            continue
        when = parse_date(item.get("cover_date")) or parse_date(item.get("store_date"))
        if when:
            dated.append((when, item))
    return min(dated, key=lambda pair: pair[0])[1] if dated else {}


def fetch_series_ids(source_issue: SourceIssue) -> SourceSeries:
    """The ids of `source_issue`'s series in other catalogues (GET /api/series/<id>/). Optional: when
    it cannot be read they stay unknown."""
    series_id = source_issue.metron_series_id
    if not series_id:
        return SourceSeries()
    series = _optional(
        f"series/{int(series_id)}/",
        f"catalog:{KEY}:series:{series_id}",
        SERIES_CACHE_SECONDS,
        lambda data: parse_int(data.get("id")) == series_id,
    )
    return SourceSeries(
        comicvine_volume_id=parse_int(series.get("cv_id")), gcd_series_id=parse_int(series.get("gcd_id"))
    )


def fetch_series(source_issue: SourceIssue, with_publisher: bool) -> SourceSeries:
    """The first issue of `source_issue`'s series (/api/issue/?series_id=<id>) and, when
    `with_publisher`, the publisher's country (/api/publisher/<id>/); the series ids are
    fetch_series_ids'. Each answer is optional: what cannot be read stays unknown."""
    first: dict[str, Any] = {}
    publisher: dict[str, Any] = {}
    series_id = source_issue.metron_series_id
    if series_id:
        issues = _optional(
            f"issue/?series_id={int(series_id)}",
            f"catalog:{KEY}:series-issues:{series_id}",
            CACHE_SECONDS,
            lambda data: isinstance(data.get("results"), list),
        )
        first = first_issue(issues.get("results"))
    publisher_id = source_issue.source_publisher_id
    if with_publisher and publisher_id:
        publisher = _optional(
            f"publisher/{int(publisher_id)}/",
            f"catalog:{KEY}:publisher:{publisher_id}",
            SERIES_CACHE_SECONDS,
            lambda data: parse_int(data.get("id")) == publisher_id,
        )
    return SourceSeries(
        first_issue_store_date=parse_date(first.get("store_date")),
        first_issue_cover_date=parse_date(first.get("cover_date")),
        publisher_country=str(publisher.get("country") or "").strip(),
    )


def to_source_issue(data: dict[str, Any]) -> SourceIssue:
    """Metron's issue JSON as the mapper's neutral input."""
    series = data.get("series") or {}
    publisher = data.get("publisher") or {}
    series_type = series.get("series_type") or {}
    credits = [
        SourceCredit(
            name=str(credit.get("creator") or "").strip(),
            roles=[str(role.get("name") or "") for role in credit.get("role") or [] if isinstance(role, dict)],
        )
        for credit in data.get("credits") or []
        if isinstance(credit, dict) and credit.get("creator")
    ]
    variants = [
        SourceVariant(
            name=str(variant.get("name") or "").strip(),
            image=str(variant.get("image") or ""),
            price=parse_decimal(variant.get("price")),
        )
        for variant in data.get("variants") or []
        if isinstance(variant, dict)
    ]
    return SourceIssue(
        source=KEY,
        source_name=NAME,
        source_id=parse_int(data.get("id")),
        source_url=str(data.get("resource_url") or ""),
        publisher=str(publisher.get("name") or "").strip(),
        series_name=str(series.get("name") or "").strip(),
        series_volume=parse_int(series.get("volume")),
        series_year=parse_int(series.get("year_began")),
        series_type=str(series_type.get("name") or ""),
        language=str(series.get("language") or "").lower(),
        number=str(data.get("number") or "").strip(),
        store_date=parse_date(data.get("store_date")),
        cover_date=parse_date(data.get("cover_date")),
        price=parse_decimal(data.get("price")),
        price_currency=str(data.get("price_currency") or ""),
        description=str(data.get("desc") or "").strip(),
        image=str(data.get("image") or ""),
        credits=credits,
        variants=variants,
        metron_id=parse_int(data.get("id")),
        comicvine_id=parse_int(data.get("cv_id")),
        gcd_id=parse_int(data.get("gcd_id")),
        metron_series_id=parse_int(series.get("id")),
        source_publisher_id=parse_int(publisher.get("id")),
    )
