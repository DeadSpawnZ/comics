from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template import loader
from django.urls import reverse

from comics.models import Publishing, Comic


def all(request):
    try:
        publishing_list = Publishing.objects.all().order_by("publishing_title").values()
        context = {
            "publishing_list": publishing_list,
        }
        return render(request, "publishings.html", context)
    except Exception as ex:
        print(str(ex))
        pass


def publishing(request, publishing_id):
    comics_list = Comic.objects.filter(publishing_id=publishing_id)
    try:
        return render(
            request,
            "comics.html",
            {"comics_list": comics_list},
        )
    except Exception as ex:
        print(str(ex))
        pass
