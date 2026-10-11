"""Why a catalogue lookup or a cover download failed, as messages for the user.

Every error carries a translated, user-facing `message` and the HTTP status the Gestión endpoint
answers with. Messages never include URLs, query strings or headers: the Comic Vine key travels in
the query string and the Metron credentials in a header, so neither may reach a page or a log."""

from __future__ import annotations


class CatalogError(Exception):
    status = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotConfigured(CatalogError):
    """The source has no credentials in the settings."""

    status = 503


class NotFound(CatalogError):
    status = 404


class AuthFailed(CatalogError):
    """The source rejected the credentials."""

    status = 502


class RateLimited(CatalogError):
    """The source (or our own limit, see comics.catalog_sources.http.throttle) asks to slow down."""

    status = 429


class Unavailable(CatalogError):
    """Timeout, connection error or server error at the source."""

    status = 504


class BadResponse(CatalogError):
    """The source answered something we cannot read (not JSON, too large, unexpected shape)."""

    status = 502


class CoverRejected(CatalogError):
    """A cover URL that is not allowed, or a download that is not an acceptable picture."""

    status = 400
