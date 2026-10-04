"""The project's admin site: the Django admin is for superusers only.

Installed as the default site by ComiAdminConfig (comics/apps.py), so `admin.site` and every
`@admin.register` keep working unchanged. Day-to-day catalogue work happens in Gestión, which is
gated by the `comics.access_management` permission instead (see comics/roles.py)."""

from __future__ import annotations

from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.http import HttpRequest


class SuperuserAuthenticationForm(AdminAuthenticationForm):
    """The admin login form, checking the superuser flag instead of is_staff (same as the site)."""

    def confirm_login_allowed(self, user: AbstractBaseUser) -> None:
        if not (user.is_active and user.is_superuser):
            raise ValidationError(
                self.error_messages["invalid_login"],
                code="invalid_login",
                params={"username": self.username_field.verbose_name},
            )


class ComiAdminSite(AdminSite):
    login_form = SuperuserAuthenticationForm

    def has_permission(self, request: HttpRequest) -> bool:
        return request.user.is_active and request.user.is_superuser
