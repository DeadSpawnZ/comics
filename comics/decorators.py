"""Access decorators for views: one per area, so the rule for each area lives in one place."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.urls import reverse

from comics.roles import can_access_management

View = Callable[..., HttpResponse]


def management_required(view_func: View) -> View:
    """Gestión views and APIs: anonymous users go to LOGIN_URL (with `next`); signed-in users who
    fail can_access_management (the single Gestión rule) get 403."""

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not can_access_management(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return login_required(wrapper)


def admin_site_required(view_func: View) -> View:
    """Endpoints used only by Django admin pages: same rule as the admin site itself
    (ComiAdminSite.has_permission) and, like staff_member_required, the admin login otherwise."""

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if admin.site.has_permission(request):
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path(), reverse("admin:login"))

    return wrapper
