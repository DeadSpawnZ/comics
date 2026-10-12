"""Comic Vine (https://comicvine.gamespot.com/api/), the fallback catalogue: the API key goes in the
query string (never logged nor shown, see comics.catalog_sources.http) and the answers are JSON
envelopes with their own status codes. An issue does not say its publisher nor the year its
volume began, so the volume is fetched too (cached longer: it rarely changes)."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from .errors import AuthFailed, BadResponse, NotConfigured, NotFound, RateLimited
from .http import get_json, throttle
from .types import SourceCredit, SourceIssue, SourceSeries, parse_date, parse_int

KEY = "comicvine"
NAME = "Comic Vine"
API_URL = "https://comicvine.gamespot.com/api/"
ISSUE_CACHE_SECONDS = 24 * 60 * 60
VOLUME_CACHE_SECONDS = 7 * 24 * 60 * 60
REQUESTS_PER_MINUTE = 20  # Comic Vine also penalizes bursts ("velocity"), not only the hourly count
ISSUE_FIELDS = "id,name,issue_number,volume,cover_date,store_date,person_credits,image,description,site_detail_url"
VOLUME_FIELDS = "id,name,start_year,publisher"

# Status codes of Comic Vine's JSON envelope (the HTTP status is usually 200 anyway).
STATUS_OK = 1
STATUS_INVALID_KEY = 100
STATUS_NOT_FOUND = 101
STATUS_RATE_LIMITED = 107


def is_configured() -> bool:
    return bool(settings.COMICVINE_API_KEY)


def _not_found(resource_id: int) -> str:
    return _("%(source)s has no issue with the id %(id)s.") % {"source": NAME, "id": resource_id}


@sensitive_variables("query")
def _get(path: str, fields: str, cache_key: str, cache_seconds: int, resource_id: int) -> dict[str, Any]:
    data = cache.get(cache_key)
    if data is not None:
        return data
    throttle(KEY, REQUESTS_PER_MINUTE, NAME)
    query = urlencode({"api_key": settings.COMICVINE_API_KEY, "format": "json", "field_list": fields})
    envelope = get_json(f"{API_URL}{path}?{query}", source=NAME, not_found=_not_found(resource_id))
    if not isinstance(envelope, dict):
        raise BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": NAME})
    status = envelope.get("status_code")
    if status == STATUS_INVALID_KEY:
        raise AuthFailed(_("%(source)s rejected the credentials. Check them in the .env file.") % {"source": NAME})
    if status == STATUS_NOT_FOUND:
        raise NotFound(_not_found(resource_id))
    if status == STATUS_RATE_LIMITED:
        raise RateLimited(
            _("%(source)s is receiving too many requests from Comi. Wait a minute and try again.") % {"source": NAME}
        )
    results = envelope.get("results")
    if status != STATUS_OK or not isinstance(results, dict):
        # A missing id answers OK with an empty list instead of a 101 on some resources.
        if status == STATUS_OK and not results:
            raise NotFound(_not_found(resource_id))
        raise BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": NAME})
    cache.set(cache_key, results, cache_seconds)
    return results


def fetch_issue(issue_id: int) -> dict[str, Any]:
    """{"issue": <issue resource>, "volume": <its volume resource, or {}>}."""
    if not is_configured():
        raise NotConfigured(_("%(source)s is not configured.") % {"source": NAME})
    issue = _get(
        f"issue/4000-{int(issue_id)}/", ISSUE_FIELDS, f"catalog:{KEY}:issue:{issue_id}", ISSUE_CACHE_SECONDS, issue_id
    )
    volume: dict[str, Any] = {}
    volume_id = parse_int((issue.get("volume") or {}).get("id"))
    if volume_id:
        try:
            volume = _get(
                f"volume/4050-{volume_id}/",
                VOLUME_FIELDS,
                f"catalog:{KEY}:volume:{volume_id}",
                VOLUME_CACHE_SECONDS,
                volume_id,
            )
        except NotFound:
            volume = {}  # the issue is still useful without its publisher and start year
    return {"issue": issue, "volume": volume}


def fetch_series_ids(source_issue: SourceIssue) -> SourceSeries:
    """Comic Vine knows no other catalogue's ids; its own volume id is in `source_issue`."""
    return SourceSeries(comicvine_volume_id=source_issue.comicvine_volume_id)


def fetch_series(source_issue: SourceIssue, with_publisher: bool) -> SourceSeries:
    """What Comic Vine adds about the series: nothing beyond the volume fetched with the issue (its
    id and start year are already in `source_issue`). It does not say the publisher's country, and
    the first issue's date would cost another request per volume, so the proposal falls back to the
    start year."""
    return SourceSeries()


def html_to_text(value: str) -> str:
    """Comic Vine descriptions are HTML: keep paragraphs and line breaks, drop the markup."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n", value or "")
    text = html.unescape(strip_tags(text))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def to_source_issue(data: dict[str, Any]) -> SourceIssue:
    """Comic Vine's issue (and volume) JSON as the mapper's neutral input. Comic Vine knows neither
    the volume number, the language, the price nor the variants."""
    issue, volume = data.get("issue") or {}, data.get("volume") or {}
    image = issue.get("image") or {}
    credits = [
        SourceCredit(
            name=str(person.get("name") or "").strip(),
            roles=[role.strip() for role in str(person.get("role") or "").split(",") if role.strip()],
        )
        for person in issue.get("person_credits") or []
        if isinstance(person, dict) and person.get("name")
    ]
    return SourceIssue(
        source=KEY,
        source_name=NAME,
        source_id=parse_int(issue.get("id")),
        source_url=str(issue.get("site_detail_url") or ""),
        publisher=str((volume.get("publisher") or {}).get("name") or "").strip(),
        series_name=str(volume.get("name") or (issue.get("volume") or {}).get("name") or "").strip(),
        series_volume=None,
        series_year=parse_int(volume.get("start_year")),
        series_type="",
        language="",
        number=str(issue.get("issue_number") or "").strip(),
        store_date=parse_date(issue.get("store_date")),
        cover_date=parse_date(issue.get("cover_date")),
        price=None,
        price_currency="",
        description=html_to_text(str(issue.get("description") or "")),
        image=str(image.get("super_url") or image.get("original_url") or image.get("medium_url") or ""),
        credits=credits,
        variants=[],
        metron_id=None,
        comicvine_id=parse_int(issue.get("id")),
        gcd_id=None,
        comicvine_volume_id=parse_int(volume.get("id")) or parse_int((issue.get("volume") or {}).get("id")),
        source_publisher_id=parse_int((volume.get("publisher") or {}).get("id")),
    )
