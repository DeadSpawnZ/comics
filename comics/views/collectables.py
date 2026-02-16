from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from comics.models import GeekCollectable

PAGE_LIMIT = 30


@login_required
def collectables_view(request):
    letter = request.GET.get("letter")
    search = request.GET.get("search")

    collectables = GeekCollectable.objects.select_related("participant")
    if letter:
        collectables = (
            GeekCollectable.objects
            .filter(name__istartswith=letter)
            .select_related("participant")
            .order_by("name", "trade_date")
        )

    # 🔎 Búsqueda opcional
    if search:
        collectables = collectables.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search)
        )

    paginator = Paginator(collectables, PAGE_LIMIT)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "collectables/collectables.html",
        {
            "page_obj": page_obj,
            "selected_letter": letter,
            "search": search,
            "alphabet": [chr(i) for i in range(ord("A"), ord("Z") + 1)],
        },
    )
