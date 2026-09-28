from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
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


def _attach_sibling_editions(collections, user):
    """Set `collection.sibling_editions`: the other editions of the same issue (variants, printings,
    foreign or anniversary editions), each flagged with `owned`. Two queries for the whole page."""
    issue_ids = {collection.edition.issue_id for collection in collections if collection.edition and collection.edition.issue_id}
    by_issue = {}
    if issue_ids:
        editions = (
            Edition.objects.filter(issue_id__in=issue_ids)
            .select_related("publishing")
            .order_by("release_date", "publishing__publishing_title", "number", "variant", "printing")
        )
        owned_ids = set(
            Collection.objects.owned_by(user).filter(edition__issue_id__in=issue_ids).values_list("edition_id", flat=True)
        )
        for edition in editions:
            edition.owned = edition.id in owned_ids
            by_issue.setdefault(edition.issue_id, []).append(edition)
    for collection in collections:
        edition = collection.edition
        siblings = by_issue.get(edition.issue_id, []) if edition else []
        collection.sibling_editions = [sibling for sibling in siblings if sibling.id != edition.id]
        collection.sibling_groups = Edition.group_by_cover_kind(collection.sibling_editions)


@login_required
def comics_view(request):
    collector = request.user
    letter = request.GET.get("letter", "A")
    selected_country = request.GET.get("country")

    # Prefetch for the signing artists
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

    # "" (Todas) disables the letter filter instead of forcing a letter.
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
    _attach_sibling_editions(page_obj, collector)

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


# Used only by the admin collection form (collection_form_events.js): staff only.
@staff_member_required
@require_GET
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

        # You cannot sell something you did not own yet: only purchases made
        # on or before the sale date are offered.
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
