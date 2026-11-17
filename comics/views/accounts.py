"""Accounts: sign in, sign out and self-registration.

There is no SMTP server yet, so there is no email verification or password reset by email. When
there is, they belong here (Django's PasswordResetView & co. only need URLs and templates)."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods, require_POST

from comics.forms import LoginForm, RegistrationForm
from comics.views.utils import safe_next_url

DEFAULT_LANDING = reverse_lazy("collectables")


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """GET shows the sign-in page (the LOGIN_URL target); POST signs in. Both honor a same-site
    `next` URL (what @login_required adds), carried through the form as a hidden field."""
    next_url = safe_next_url(request, "")
    if request.user.is_authenticated:
        return redirect(next_url or DEFAULT_LANDING)
    form = LoginForm(request, data=request.POST if request.method == "POST" else None)
    if form.is_valid():
        login(request, form.get_user())
        return redirect(next_url or DEFAULT_LANDING)
    return render(request, "accounts/login.html", {"form": form, "next_url": next_url})


@require_http_methods(["GET", "POST"])
def register_view(request: HttpRequest) -> HttpResponse:
    """Self-registration. A new account is signed in right away and, like every user, is a
    Collector (comics.signals). Signed-in users have nothing to do here."""
    next_url = safe_next_url(request, "")
    if request.user.is_authenticated:
        return redirect(next_url or DEFAULT_LANDING)
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        user = form.save()
        login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])
        return redirect(next_url or DEFAULT_LANDING)
    return render(request, "accounts/register.html", {"form": form, "next_url": next_url})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    """POST only (CSRF-protected): a link or an image on another site cannot sign users out."""
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)
