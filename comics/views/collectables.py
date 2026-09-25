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

    collectables = GeekCollectable.objects.select_related("participant").order_by("name", "trade_date")
    if letter:
        collectables = collectables.filter(name__istartswith=letter)

    # 🔎 Búsqueda opcional
    if search:
        collectables = collectables.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search)
        )

    paginator = Paginator(collectables, PAGE_LIMIT)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    elided_page_range = page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1)

    return render(
        request,
        "collectables/collectables.html",
        {
            "page_obj": page_obj,
            "elided_page_range": elided_page_range,
            "selected_letter": letter,
            "search": search,
            "alphabet": [chr(i) for i in range(ord("A"), ord("Z") + 1)],
        },
    )
