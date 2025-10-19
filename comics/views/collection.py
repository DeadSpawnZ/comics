from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from comics.models import Collection

PAGE_LIMIT = 30


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


@login_required
def all(request):
    try:
        user_id = request.user.id
        user = User.objects.get(id=user_id)
        collection_list = Collection.objects.filter(collector=user).order_by("comic__publishing__publishing_title")
        context = {"collection_list": collection_list}
        return render(request, "collection.html", context)
    except Exception as ex:
        print(str(ex))
        pass


@login_required
def collector_collections_view(request):
    collector = request.user
    letter = request.GET.get("letter", "A")
    collections = (
        Collection.objects.filter(collector=collector)
        .filter(comic__publishing__publishing_title__istartswith=letter)
        .select_related("comic__publishing")
        .order_by("comic__publishing__publishing_title", "comic__number", "comic__variant")
    )

    paginator = Paginator(collections, PAGE_LIMIT)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "collections/collector_collections.html",
        {
            "page_obj": page_obj,
            "selected_letter": letter,
            "alphabet": [chr(i) for i in range(ord("A"), ord("Z") + 1)],
        },
    )
