"""Small helpers shared by several view modules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from django.contrib.admin.models import LogEntry
from django.db import IntegrityError
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


T = TypeVar("T")


class GeneratesCode(Protocol):
    def assign_code(self) -> None: ...


def save_with_fresh_code(form: GeneratesCode, save: Callable[[], T]) -> T:
    """Run `save` (an atomic creation of a row whose code `form` generated from its name) and, if
    another request took that code in the meantime (IntegrityError on the unique code), generate
    the code again and retry once. A second failure is raised."""
    try:
        return save()
    except IntegrityError:
        form.assign_code()
        return save()
