from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect


def login_view(request: HttpRequest) -> HttpResponse:
    username = request.POST.get("username")
    password = request.POST.get("password")
    user = authenticate(username=username, password=password)
    if user is not None:
        login(request, user)
        return redirect("/collectables/")
    else:
        return HttpResponse("Your username and password didn't match.")


def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    logout(request)
    return redirect("/admin/")
