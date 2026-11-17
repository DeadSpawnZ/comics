"""Project middleware."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse

from comics.achievements.engine import take_notices

# Paths that never render a page, whatever their Accept header says.
NON_PAGE_PREFIXES = ("/jsi18n/", "/i18n/", "/ajax/")


def is_page_request(request: HttpRequest) -> bool:
    """A browser loading a page: a GET asking for HTML (navigations send `Accept: text/html...`;
    fetch() and scripts send */* or JSON), excluding static/media files and the endpoints that
    only serve data (translation catalog, language switch, AJAX and the */api/* endpoints)."""
    path = request.path
    return (
        request.method == "GET"
        and "text/html" in request.headers.get("Accept", "")
        and not path.startswith((settings.STATIC_URL, settings.MEDIA_URL, *NON_PAGE_PREFIXES))
        and "/api/" not in path
    )


class AchievementNoticeMiddleware:
    """Announce newly earned achievements on the next page a signed-in user opens (as snackbar
    messages). Only page loads (is_page_request) look for them: one indexed query per page when
    nothing is pending. Needs the auth, session and message middleware before it."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated and is_page_request(request):
            for notice in take_notices(request.user):
                messages.success(request, notice)
        return self.get_response(request)
