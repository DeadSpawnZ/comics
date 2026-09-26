from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse
from django.utils.dateparse import parse_date
from django.db.models import (
    OuterRef,
    Subquery,
    Prefetch,
    IntegerField,
    Case,
    When,
    Value,
)
from django.db.models.functions import Cast

from comics.models import Collection, Signature, Editorial, Edition

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
def comics_view(request):
    collector = request.user
    letter = request.GET.get("letter", "A")
    selected_country = request.GET.get("country")

    # Prefetch para artistas firmantes
    signed_artists = Prefetch(
        "signature_set",
        queryset=Signature.objects.select_related("artist"),
        to_attr="prefetched_signatures",
    )

    first_editorial_country = Subquery(
        Editorial.objects.filter(publishing__edition=OuterRef("edition")).order_by("id").values("country")[:1]
    )

    collections = (
        Collection.objects.owned_by(collector)
        .select_related("edition__publishing", "participant")
        .prefetch_related(
            signed_artists,
            "edition__publishing__editorials",
        )
        .annotate(first_editorial_country=first_editorial_country,
            edition_number_int=Case(
                When(
                    edition__number__regex=r'^\d+$',
                    then=Cast("edition__number", IntegerField())
                ),
                default=Value(None),
                output_field=IntegerField(),
            )
        )
        .order_by(
            "edition__publishing__title__name",
            "edition__publishing__publishing_title",
            "first_editorial_country",
            "edition__publishing__serie",
            "edition__publishing__year",
            "edition_number_int",
            "edition__number",
            "edition__variant",
            "trade_date",
        )
    )

    # "" (Todas) desactiva el filtro por letra en vez de forzar una letra.
    if letter:
        collections = collections.filter(edition__publishing__title__name__istartswith=letter)

    if selected_country:
        collections = collections.filter(
            edition__publishing__editorials__country=selected_country
        ).distinct()

    paginator = Paginator(collections, PAGE_LIMIT)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    elided_page_range = page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1)

    return render(
        request,
        "collections/collector_collections.html",
        {
            "page_obj": page_obj,
            "elided_page_range": elided_page_range,
            "selected_letter": letter,
            "selected_country": selected_country,
            "editorial_countries": Editorial.CountryAbbr.choices,
            "alphabet": [chr(i) for i in range(ord("A"), ord("Z") + 1)],
        },
    )


def get_previous_trades(request, edition_id):
    try:
        used_previous_trades_ids = (
            Collection.objects.filter(edition_id=edition_id)
            .exclude(previous_trade=None)
            .values_list("previous_trade_id", flat=True)
        )
        trades = (
            Collection.objects.filter(edition_id=edition_id, trade_type=Collection.TradeChoices.BUYING)
            .exclude(id__in=used_previous_trades_ids)
            .select_related("edition__publishing", "participant")
            .prefetch_related("edition__publishing__editorials")
        )

        # No se puede vender algo que aun no se poseia: solo se ofrecen compras
        # ocurridas en la fecha de venta o antes.
        before_date = parse_date(request.GET.get("before", ""))
        if before_date:
            trades = trades.filter(trade_date__lte=before_date)

        trades = trades.order_by(
            "edition__publishing__publishing_title",
            "edition__number",
            "edition__variant",
            "trade_date",
        )

        data = [
            {"id": trade.id, "text": f"{trade.edition} || {trade.trade_date} || {trade.participant.name}"}
            for trade in trades
        ]
        return JsonResponse({"results": data})
    except Collection.DoesNotExist:
        return JsonResponse({"results": []})
    except Exception as e:
        return JsonResponse({"error": str(e)})
