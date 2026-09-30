from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from comics.views.manage_editions import _safe_next

DEFAULT_LANDING = "/collectables/"


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """GET shows the sign-in page (also the LOGIN_URL target); POST signs in. Both honor a same-site
    `next` URL (what @login_required adds), carried through the login dialog as a hidden field."""
    next_url = _safe_next(request, "")
    if request.user.is_authenticated:
        return redirect(next_url or DEFAULT_LANDING)
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username"), password=request.POST.get("password"))
        if user is not None:
            login(request, user)
            return redirect(next_url or DEFAULT_LANDING)
        return render(request, "login.html", {"login_failed": True, "login_next": next_url})
    return render(request, "login.html", {"login_failed": False, "login_next": next_url})


def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    logout(request)
    return redirect("/admin/")
