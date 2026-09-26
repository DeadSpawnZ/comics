from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.template import loader
from django.urls import reverse
from django.contrib.auth import authenticate

from comics.models import Publishing, Edition


def get_publishing_date(request, publishing_id):
    try:
        publishing = Publishing.objects.get(id=publishing_id)
        return JsonResponse({"date": publishing.date.strftime("%d/%m/%Y")})
    except Publishing.DoesNotExist:
        return JsonResponse({"date": None})


def get_comics_by_publishing(request, publishing_id):
    try:
        editions = Edition.objects.filter(publishing_id=publishing_id).order_by("number", "variant")
        data = [{"id": edition.id, "text": str(edition)} for edition in editions]
        return JsonResponse({"results": data})
    except Edition.DoesNotExist:
        return JsonResponse({"results": []})
    except Exception as e:
        return JsonResponse({"error": str(e)})
