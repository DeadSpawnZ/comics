"""Small helpers shared by several view modules."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar
from urllib.parse import urlencode

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Model
from django.forms import BaseForm
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from comics.helper import parse_id_or_none
from comics.roles import can_access_management


def safe_next_url(request: HttpRequest, fallback: str) -> str:
    """The `next` URL (POST or GET) when it stays on this site, else `fallback`."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def requested_collector(request: HttpRequest) -> User:
    """The collector a piece endpoint asks about: the `collector` id of the query string for Gestión
    users (only they may ask about another collector's pieces), otherwise the signed-in user."""
    collector_id = parse_id_or_none(request.GET.get("collector"))
    if collector_id and can_access_management(request.user):
        return User.objects.filter(pk=collector_id).first() or request.user
    return request.user


def initial_from_query(request: HttpRequest, keys: Iterable[str]) -> dict[str, str]:
    """Initial values of a new form from the query string: the non-empty ones among `keys` (e.g. the
    values "Save and add another" carried over). The form validates them as usual."""
    return {key: request.GET[key] for key in keys if request.GET.get(key)}


def carry_over_query(form: BaseForm, keys: Iterable[str]) -> str:
    """Query string with the non-empty submitted values of `keys`, so "Save and add another" opens
    the next form with them (read back by initial_from_query)."""
    return urlencode({key: form.data[key] for key in keys if form.data.get(key)})


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
