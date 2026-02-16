from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Prefetch, OuterRef, Subquery, Q
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse

from comics.models import Collection, Signature, Editorial, Comic

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
        Editorial.objects.filter(publishing__comic=OuterRef("comic")).order_by("id").values("country")[:1]
    )

    sold_previous_trade_ids = Collection.objects.filter(
        collector=collector,
        trade_type=Collection.TradeChoices.SELLING,
        previous_trade__isnull=False,
    ).values_list("previous_trade_id", flat=True)

    collections = (
        Collection.objects.filter(collector=collector)
        .filter(comic__publishing__publishing_title__istartswith=letter)
        .exclude(Q(trade_type=Collection.TradeChoices.SELLING) | Q(id__in=sold_previous_trade_ids))
        .select_related("comic__publishing", "participant")
        .prefetch_related(
            "comic__artists",
            signed_artists,
            "comic__publishing__editorials",
        )
        .annotate(first_editorial_country=first_editorial_country)
        .order_by(
            "comic__publishing__publishing_title",
            "first_editorial_country",
            "comic__publishing__serie",
            "comic__publishing__year",
            "comic__number",
            "comic__variant",
            "trade_date",
        )
    )

    if selected_country:
        collections = collections.filter(
            comic__publishing__editorials__country=selected_country
        ).distinct()

    paginator = Paginator(collections, PAGE_LIMIT)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "collections/collector_collections.html",
        {
            "page_obj": page_obj,
            "selected_letter": letter,
            "selected_country": selected_country,
            "editorial_countries": Editorial.CountryAbbr.choices,
            "alphabet": [chr(i) for i in range(ord("A"), ord("Z") + 1)],
        },
    )


def get_previous_trades(request, comic_id):
    try:
        used_previous_trades_ids = (
            Collection.objects.filter(comic_id=comic_id)
            .exclude(previous_trade=None)
            .values_list("previous_trade_id", flat=True)
        )
        trades = (
            Collection.objects.filter(comic_id=comic_id, trade_type=Collection.TradeChoices.BUYING)
            .exclude(id__in=used_previous_trades_ids)
            .order_by(
                "comic__publishing__publishing_title",
                "comic__number",
                "comic__variant",
                "trade_date",
            )
        )

        data = [
            {"id": trade.id, "text": f"{trade.comic} || {trade.trade_date} || {trade.participant.name}"}
            for trade in trades
        ]
        return JsonResponse({"results": data})
    except Collection.DoesNotExist:
        return JsonResponse({"results": []})
    except Exception as e:
        return JsonResponse({"error": str(e)})
