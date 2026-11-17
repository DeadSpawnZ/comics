"""Collectables module: the signed-in user's pieces and the shared Article catalogue, laid out like
My comics (Collection and Editions), plus "New piece" for the signed-in user. Only the signed-in
user's own pieces are ever shown."""

from __future__ import annotations

from typing import Any, TypeVar

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Model, Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from comics.forms import CollectableEntryForm, article_images, available_collectable_purchases, purchase_label
from comics.helper import parse_date_or_none, parse_id_or_none
from comics.models import Article, Collectable
from comics.views.utils import carry_over_query, initial_from_query, log_action, requested_collector, safe_next_url

PAGE_LIMIT = 30
ALPHABET = [chr(i) for i in range(ord("A"), ord("Z") + 1)]
# Values carried over by "Save and add another" (a batch bought the same day from the same person);
# the collector only exists in the Gestión form.
CARRY_OVER = ("trade_date", "trade_type", "participant", "collector")

M = TypeVar("M", bound=Model)


def _filter_context(request: HttpRequest) -> dict[str, Any]:
    """Letter chip, search text and category from the query string (an unknown category is ignored),
    with what the filter widgets need to render."""
    category = request.GET.get("category", "")
    if category not in Article.CategoryChoices.values:
        category = ""
    return {
        "selected_letter": request.GET.get("letter", ""),
        "query": request.GET.get("q", "").strip(),
        "selected_category": category,
        "category_choices": Article.CategoryChoices.choices,
        "alphabet": ALPHABET,
    }


def _article_filter(filters: dict[str, Any], prefix: str = "") -> Q:
    """The filters as a condition on Article fields; `prefix` reaches the article from a related
    model (e.g. "article__" from Collectable)."""
    condition = Q()
    if filters["selected_letter"]:
        condition &= Q(**{f"{prefix}name__istartswith": filters["selected_letter"]})
    if filters["query"]:
        query = filters["query"]
        condition &= Q(**{f"{prefix}name__icontains": query}) | Q(**{f"{prefix}description__icontains": query})
    if filters["selected_category"]:
        condition &= Q(**{f"{prefix}category": filters["selected_category"]})
    return condition


def _page_context(request: HttpRequest, queryset: QuerySet[M]) -> dict[str, Any]:
    page_obj = Paginator(queryset, PAGE_LIMIT).get_page(request.GET.get("page"))
    return {
        "page_obj": page_obj,
        "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
    }


@login_required
def collectables_view(request: HttpRequest) -> HttpResponse:
    """The pieces the signed-in collector still owns."""
    filters = _filter_context(request)
    pieces = (
        Collectable.objects.owned_by(request.user)
        .filter(_article_filter(filters, prefix="article__"))
        .select_related("article", "participant")
        .order_by("article__name", "trade_date", "pk")
    )
    return render(request, "collectables/collectables.html", {**filters, **_page_context(request, pieces)})


@login_required
def article_list(request: HttpRequest) -> HttpResponse:
    """The shared article catalogue, flagged with how many pieces of each the user owns."""
    filters = _filter_context(request)
    articles = Article.objects.filter(_article_filter(filters)).order_by("name", "pk")
    context = {**filters, **_page_context(request, articles)}

    page_obj = context["page_obj"]
    counts = (
        Collectable.objects.owned_by(request.user)
        .filter(article_id__in=[article.id for article in page_obj])
        .count_by_article()
    )
    for article in page_obj:
        article.owned_count = counts.get(article.id, 0)
    return render(request, "collectables/article_list.html", context)


@login_required
def article_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """An article with every purchase and sale of it by the signed-in user."""
    article = get_object_or_404(Article, pk=pk)
    trades = list(
        Collectable.objects.filter(collector=request.user, article=article)
        .with_sold_flag()
        .select_related("participant")
        .order_by("trade_date", "pk")
    )
    # What the user still owns: the purchases no sale points to (the same rule as owned_by).
    owned_count = sum(
        1 for trade in trades if trade.trade_type == Collectable.TradeChoices.BUYING and not trade.is_sold
    )
    return render(
        request,
        "collectables/article_detail.html",
        {
            "article": article,
            "back_url": safe_next_url(request, reverse("collectables_articles")),
            "trades": trades,
            "owned_count": owned_count,
        },
    )


# ---------- "New piece": the signed-in collector registers a purchase or a sale ----------
# save_piece and piece_form_context are shared with the Gestión piece pages (manage_collectables).


def save_piece(request: HttpRequest, form: CollectableEntryForm, *, created: bool, origin: str) -> Collectable | None:
    """Save a valid piece form and log it (made "desde `origin`", the page it was made from); None
    (with the error on the form) if the model rejects it, e.g. a concurrent sale of the same purchase
    caught by Collectable.save."""
    try:
        with transaction.atomic():
            piece = form.save()
            log_action(
                request,
                piece,
                ADDITION if created else CHANGE,
                f"{'Creado' if created else 'Modificado'} desde {origin}.",
            )
    except ValidationError as exc:
        form.add_error(None, exc)
        return None
    message = _("Piece added: %(piece)s") if created else _("Piece saved: %(piece)s")
    messages.success(request, message % {"piece": piece.article})
    return piece


def piece_form_context(form: CollectableEntryForm, piece: Collectable | None) -> dict[str, Any]:
    """What the piece form templates need: the form, the selected article (server-rendered preview)
    and the data of its script (collectable_form.js)."""
    # The submitted value may be malformed; the form reports that, here it only means "no article".
    selected = parse_id_or_none(form["article"].value())
    return {
        "form": form,
        "piece": piece,
        "selected_article": Article.objects.filter(pk=selected).first() if selected else None,
        "form_data": {
            "purchasesUrl": reverse("collectables_api_purchases"),
            "current": piece.pk if piece else None,
            "images": article_images(),
        },
    }


@login_required
@require_GET
def api_purchases(request: HttpRequest) -> JsonResponse:
    """Purchases of an article a sale can point to. Only Gestión users may ask about another
    collector's pieces."""
    collector = requested_collector(request)
    article_id = parse_id_or_none(request.GET.get("article"))
    if article_id is None:
        return JsonResponse({"results": []})
    current_id = parse_id_or_none(request.GET.get("current"))
    current = Collectable.objects.filter(pk=current_id, collector=collector).first() if current_id else None
    before = parse_date_or_none(request.GET.get("before"))
    purchases = available_collectable_purchases(collector, article_id, before, current)
    return JsonResponse({"results": [{"id": item.pk, "label": purchase_label(item)} for item in purchases]})


@login_required
def piece_new(request: HttpRequest) -> HttpResponse:
    """A new piece of the signed-in collector; `?article=<id>` preselects the article."""
    initial = initial_from_query(request, (*CARRY_OVER, "article"))
    form = CollectableEntryForm(request.POST or None, collector=request.user, initial=initial)
    if request.method == "POST" and form.is_valid() and save_piece(request, form, created=True, origin="Collectables"):
        if "save_add_another" in request.POST:
            return redirect(f"{reverse('collectables_piece_new')}?{carry_over_query(form, CARRY_OVER)}")
        return redirect("collectables")
    return render(request, "collectables/piece_form.html", piece_form_context(form, None))
