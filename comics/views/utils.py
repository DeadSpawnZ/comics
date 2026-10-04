"""Small helpers shared by several view modules."""

from __future__ import annotations

from django.contrib.admin.models import LogEntry
from django.db.models import Model
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next_url(request: HttpRequest, fallback: str) -> str:
    """The `next` URL (POST or GET) when it stays on this site, else `fallback`."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def log_action(request: HttpRequest, obj: Model, flag: int, message: str) -> None:
    """Record a change made outside the Django admin in its history (LogEntry), shown in Gestión."""
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[obj], action_flag=flag, change_message=message, single_object=True
    )
