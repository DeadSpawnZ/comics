from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render, redirect
from django.template import loader
from django.urls import reverse
from django.contrib.auth import authenticate

from comics.models import Publishing, Comic, Editorial, Printing


def all(request):
    try:
        publishing_list = Publishing.objects.all().order_by("publishing_title").values()
        for pub in publishing_list:
            editorials = Editorial.objects.filter(publishing=pub["id"])
            pub["editorials"] = [editorial.name for editorial in editorials]

            printing = Printing.objects.filter(id=pub["printing_id"])
            pub["print"] = printing[0]

            first_editorial = editorials[0] if editorials else None
            country_code = first_editorial.country.lower() if first_editorial else ""
            pub["country"] = country_code
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
