"""Metron (https://metron.cloud), the primary catalogue: REST API with HTTP Basic auth, limited to
about 30 requests per minute. Responses are cached, so loading the same issue again (or another
cover of it) costs no request."""

from __future__ import annotations

import base64
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from .errors import BadResponse, NotConfigured
from .http import get_json, throttle
from .types import SourceCredit, SourceIssue, SourceVariant, parse_date, parse_decimal, parse_int

KEY = "metron"
NAME = "Metron"
API_URL = "https://metron.cloud/api/"
CACHE_SECONDS = 24 * 60 * 60
REQUESTS_PER_MINUTE = 25  # Metron allows 30; keep a margin


def is_configured() -> bool:
    return bool(settings.METRON_USERNAME and settings.METRON_PASSWORD)


@sensitive_variables("token")
def fetch_issue(issue_id: int) -> dict[str, Any]:
    """The issue as Metron's API returns it (GET /api/issue/<id>/), cached for a day."""
    if not is_configured():
        raise NotConfigured(_("%(source)s is not configured.") % {"source": NAME})
    cache_key = f"catalog:{KEY}:issue:{issue_id}"
    data = cache.get(cache_key)
    if data is not None:
        return data
    throttle(KEY, REQUESTS_PER_MINUTE, NAME)
    token = base64.b64encode(f"{settings.METRON_USERNAME}:{settings.METRON_PASSWORD}".encode()).decode("ascii")
    data = get_json(
        f"{API_URL}issue/{int(issue_id)}/",
        source=NAME,
        not_found=_("%(source)s has no issue with the id %(id)s.") % {"source": NAME, "id": issue_id},
        headers={"Authorization": f"Basic {token}"},
    )
    if not isinstance(data, dict) or not isinstance(data.get("series"), dict):
        raise BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": NAME})
    cache.set(cache_key, data, CACHE_SECONDS)
    return data


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
    )
