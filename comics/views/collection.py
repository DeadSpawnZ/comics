from django.shortcuts import render

# Create your views here.
from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from comics.models import Collection


def collectable(request, collectable_id):
    collectable_obj = get_object_or_404(Collection, pk=collectable_id)
    try:
        # selected_choice = collectable.choice_set.get(pk=request.POST["choice"])
        return render(
            request,
            "collection.html",
            {
                "collectable": collectable_obj,
                "error_message": "You didn't select a choice.",
            },
        )
    except Exception as ex:
        print(str(ex))
        pass


def comic(request, collectable_id):
    collectable_obj = get_object_or_404(Collection, pk=collectable_id)
    try:
        # selected_choice = collectable.choice_set.get(pk=request.POST["choice"])
        return render(
            request,
            "collection.html",
            {
                "collectable": collectable_obj,
                "error_message": "You didn't select a choice.",
            },
        )
    except Exception as ex:
        print(str(ex))
        pass
