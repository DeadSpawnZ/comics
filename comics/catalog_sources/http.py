"""HTTP for the catalogue clients, with the standard library (urllib): JSON requests to the APIs,
HTML pages from MyComicShop (which has no API) and cover downloads from an allowlist of image hosts.

Politeness and safety rules shared by every source:

- Every request sends CATALOG_USER_AGENT and has a timeout; bodies are read up to a size limit.
- API requests never follow redirects: a redirect could carry the credentials (header or query
  string) to another host. A redirect is reported as an unexpected response.
- Covers are fetched only over HTTPS from ALLOWED_COVER_HOSTS, also after a redirect, so a URL
  posted with the form can never make the server request anything else (SSRF).
- Errors become comics.catalog_sources.errors exceptions with user-facing messages, raised
  `from None` so the original exception (whose attributes hold the URL, and with it the Comic Vine
  key) is not chained into tracebacks or logs. Whatever fails while sending or reading (network,
  HTTP protocol, a malformed URL or body) is one of those errors, never an unhandled exception.
- The functions whose frames hold credentials (URL with the key, Authorization header) are marked
  with @sensitive_variables, so Django's error pages and error mails show them as stars.
"""

from __future__ import annotations

import codecs
import json
import socket
import time
from email.message import Message
from http.client import HTTPException, HTTPResponse
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from .errors import AuthFailed, BadResponse, CatalogError, CoverRejected, NotFound, RateLimited, Unavailable

TIMEOUT_SECONDS = 10
MAX_JSON_BYTES = 2 * 1024 * 1024
# A MyComicShop series page weighs ~15 KB per variant (a 24-variant series is ~360 KB).
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_COVER_BYTES = 10 * 1024 * 1024
ALLOWED_COVER_HOSTS = frozenset({"static.metron.cloud", "comicvine.gamespot.com", "media.mycomicshop.com"})
CP1252_FALLBACK = "comi-cp1252-fallback"
# What sending a request or reading its answer can raise besides HTTPError: network errors and
# timeouts (OSError, URLError), protocol errors (IncompleteRead, RemoteDisconnected...), a malformed
# URL (InvalidURL is a ValueError) and a body nested too deep to decode.
REQUEST_ERRORS = (OSError, HTTPException, ValueError, RecursionError)


class _RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str) -> None:
        return None  # urllib then raises HTTPError with the 3xx status


class _AllowlistedRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> Request | None:
        if not is_allowed_cover_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_api_opener = build_opener(_RefuseRedirects)
_cover_opener = build_opener(_AllowlistedRedirects)


def open_url(opener: OpenerDirector, request: Request) -> HTTPResponse:
    """The single place that touches the network (tests replace it)."""
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"User-Agent": settings.CATALOG_USER_AGENT, **(extra or {})}


def error_for_status(status: int, source: str, not_found: str) -> CatalogError:
    if status in (401, 403):
        return AuthFailed(_("%(source)s rejected the credentials. Check them in the .env file.") % {"source": source})
    if status == 404:
        return NotFound(not_found)
    if status in (420, 429):  # Comic Vine answers 420 when it limits a client
        return RateLimited(
            _("%(source)s is receiving too many requests from Comi. Wait a minute and try again.") % {"source": source}
        )
    if status >= 500:
        return Unavailable(
            _("%(source)s is not working right now (error %(status)s). Try again later.")
            % {"source": source, "status": status}
        )
    return BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": source})


def _request_error(exc: BaseException, source: str) -> CatalogError:
    """The user-facing error for one of REQUEST_ERRORS."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return Unavailable(_("%(source)s did not answer in time. Try again in a moment.") % {"source": source})
    if isinstance(exc, (OSError, HTTPException)):
        return Unavailable(_("%(source)s could not be reached. Try again later.") % {"source": source})
    return BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": source})


@sensitive_variables("url", "headers", "request", "response", "exc")
def get_json(url: str, *, source: str, not_found: str, headers: dict[str, str] | None = None) -> Any:
    """GET `url` and decode its JSON body. `source` names the catalogue in error messages and
    `not_found` is the message for a 404."""
    try:
        request = Request(url, headers=_headers({"Accept": "application/json", **(headers or {})}))
        with open_url(_api_opener, request) as response:
            body = response.read(MAX_JSON_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise error_for_status(status, source, not_found) from None
    except REQUEST_ERRORS as exc:
        raise _request_error(exc, source) from None
    unreadable = BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": source})
    if len(body) > MAX_JSON_BYTES:
        raise unreadable
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise unreadable from None


def _cp1252_fallback(error: UnicodeError) -> tuple[str, int]:
    """Codec error handler: decode the first invalid byte as cp1252 and resume right after it, so a
    stray cp1252 byte inside UTF-8 text (e.g. 0x85 for "…") keeps its character instead of
    becoming U+FFFD. The five bytes cp1252 leaves undefined still become U+FFFD."""
    if not isinstance(error, UnicodeDecodeError):
        raise error
    byte = error.object[error.start : error.start + 1]
    return bytes(byte).decode("cp1252", errors="replace"), error.start + 1


codecs.register_error(CP1252_FALLBACK, _cp1252_fallback)


def decode_html(body: bytes) -> str:
    """UTF-8 text, with any byte that is not valid UTF-8 read as cp1252 (see _cp1252_fallback)."""
    return body.decode("utf-8", errors=CP1252_FALLBACK)


def post_html(url: str, *, source: str, not_found: str, data: bytes = b"") -> str:
    """POST `data` (form-encoded) to `url` and return the HTML body as text, under the same rules as
    get_json: user agent, timeout, size limit, no redirects and errors as CatalogError."""
    try:
        request = Request(url, data=data, method="POST", headers=_headers({"Accept": "text/html"}))
        with open_url(_api_opener, request) as response:
            body = response.read(MAX_HTML_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise error_for_status(status, source, not_found) from None
    except REQUEST_ERRORS as exc:
        raise _request_error(exc, source) from None
    if len(body) > MAX_HTML_BYTES:
        raise BadResponse(_("%(source)s sent an answer Comi cannot read.") % {"source": source})
    return decode_html(body)


def throttle(key: str, per_minute: int, source: str) -> None:
    """Refuse (RateLimited) more than `per_minute` requests to a source within a clock minute, so
    Comi stays under the catalogue's published limit even if several people import at once. Counted
    in the default cache (per process with the local-memory cache: a soft limit)."""
    bucket = f"catalog:rate:{key}:{int(time.time() // 60)}"
    cache.add(bucket, 0, timeout=120)
    try:
        count = cache.incr(bucket)
    except ValueError:  # expired between add() and incr()
        cache.set(bucket, 1, timeout=120)
        count = 1
    if count > per_minute:
        raise RateLimited(
            _("%(source)s is receiving too many requests from Comi. Wait a minute and try again.") % {"source": source}
        )


def is_allowed_cover_url(url: str) -> bool:
    """HTTPS on the default port, on an allowlisted host, without credentials in the URL, and without
    whitespace or control characters (urlsplit accepts some that urllib then refuses)."""
    if not url.isprintable() or any(char.isspace() for char in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in ALLOWED_COVER_HOSTS
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


def download_cover(url: str) -> bytes:
    """The bytes of a cover from an allowlisted host (see is_allowed_cover_url), at most
    MAX_COVER_BYTES. Whether they are a valid picture is checked by the caller (the form)."""
    if not is_allowed_cover_url(url):
        raise CoverRejected(_("Covers can only be downloaded from the catalogues Comi imports from."))
    failed = _("The cover could not be downloaded. Try again, or upload it yourself.")
    try:
        request = Request(url, headers=_headers({"Accept": "image/*"}))
        with open_url(_cover_opener, request) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_COVER_BYTES:
                raise CoverRejected(_too_large())
            body = response.read(MAX_COVER_BYTES + 1)
    except HTTPError as exc:
        exc.close()
        raise CoverRejected(failed) from None
    except REQUEST_ERRORS:
        raise CoverRejected(failed) from None
    if len(body) > MAX_COVER_BYTES:
        raise CoverRejected(_too_large())
    return body


def _too_large() -> str:
    return _("The cover is too large (maximum %(size)s MB).") % {"size": MAX_COVER_BYTES // (1024 * 1024)}
