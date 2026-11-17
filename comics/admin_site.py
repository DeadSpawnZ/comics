"""The project's admin site: the Django admin is for superusers only.

Installed as the default site by ComiAdminConfig (comics/apps.py), so `admin.site` and every
`@admin.register` keep working unchanged. Day-to-day catalogue work happens in Gestión, which is
gated by the `comics.access_management` permission instead (see comics/roles.py)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.contrib.admin import AdminSite
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from comics.views.utils import safe_next_url


class ComiAdminSite(AdminSite):
    def has_permission(self, request: HttpRequest) -> bool:
        return request.user.is_active and request.user.is_superuser

    def login(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> HttpResponse:
        """No admin login form: the app's login page is the only one. Admin pages send visitors
        without access here (with `next`):
        - anonymous visitors go on to the app login, which brings them back to `next`;
        - signed-in users who are not superusers get 403 (sending them to the login page would
          loop: it redirects signed-in users straight back to `next`);
        - superusers go to `next` (or the admin index)."""
        index = reverse("admin:index", current_app=self.name)
        next_url = safe_next_url(request, index)
        if urlsplit(next_url).path == request.path:
            next_url = index  # `next` pointing back here would redirect in a loop
        if not request.user.is_authenticated:
            return redirect_to_login(next_url, reverse("login"))
        if not self.has_permission(request):
            raise PermissionDenied
        return redirect(next_url)
