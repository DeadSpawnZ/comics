from django.contrib.auth import authenticate
from django.http import HttpResponse
from django.shortcuts import redirect
from django.contrib.auth import login, logout


def login_view(request):
    username = request.POST.get("username")
    password = request.POST.get("password")
    user = authenticate(username=username, password=password)
    if user is not None:
        login(request, user)
        return redirect("/collection/")
    else:
        return HttpResponse("Your username and password didn't match.")


def logout_view(request):
    logout(request)
    return redirect("/publishing/")
