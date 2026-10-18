"""Gestión of the Collectables module: the article catalogue (Article) and the pieces (Collectable)
of every collector, laid out like the Gestión editions and collections."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from comics.decorators import management_required
from comics.forms import ArticleManageForm, CollectableEntryForm, article_choices
from comics.models import Article, Collectable
from comics.views.collectables import CARRY_OVER, piece_form_context, save_piece
from comics.views.collection_entries import TYPE_TABS
from comics.views.manage_editions import PAGE_SIZE, RECENT_COUNT
from comics.views.utils import carry_over_query, initial_from_query, log_action, safe_next_url

ORIGIN = "Gestión"  # where the history entries say the change was made

# ---------- Articles ----------


@management_required
def article_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    category_filter = request.GET.get("category", "")
    if category_filter not in Article.CategoryChoices.values:
        category_filter = ""

    articles = Article.objects.annotate(piece_count=Count("pieces"))
    filtered = query or category_filter
    if query:
        articles = articles.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if category_filter:
        articles = articles.filter(category=category_filter)

    page_obj = Paginator(articles.order_by("name", "pk"), PAGE_SIZE).get_page(request.GET.get("page"))
    # The latest additions go first (only on the unfiltered first page), then the A-Z list.
    recent = list(articles.order_by("-pk")[:RECENT_COUNT]) if page_obj.number == 1 and not filtered else []
    return render(
        request,
        "manage/article_list.html",
        {
            "recent": recent,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "query": query,
            "category_filter": category_filter,
            "category_choices": Article.CategoryChoices.choices,
        },
    )


@management_required
def article_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    article = get_object_or_404(Article, pk=pk) if pk else None
    back_url = safe_next_url(request, reverse("manage_articles"))
    form = ArticleManageForm(request.POST or None, request.FILES or None, instance=article)
    if request.method == "POST" and form.is_valid():
        created = article is None
        with transaction.atomic():
            saved = form.save()
            label = "Creado" if created else "Modificado"
            log_action(request, saved, ADDITION if created else CHANGE, f"{label} desde {ORIGIN}.")
        message = _("Article created: %(article)s") if created else _("Article saved: %(article)s")
        messages.success(request, message % {"article": saved})
        if "save_continue" in request.POST:
            return redirect(f"{reverse('manage_article_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    piece_count = article.pieces.count() if article else 0
    return render(
        request,
        "manage/article_form.html",
        {"article": article, "form": form, "back_url": back_url, "piece_count": piece_count},
    )


@management_required
@require_POST
def article_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    article = get_object_or_404(Article, pk=pk)
    label = str(article)
    back_url = safe_next_url(request, reverse("manage_articles"))
    try:
        with transaction.atomic():
            log_action(request, article, DELETION, f"Eliminado desde {ORIGIN}.")
            article.delete()
    except ProtectedError:
        # Collectable.article is PROTECT: the pieces have to be deleted (or moved) first.
        pieces = article.pieces.count()
        messages.error(
            request,
            ngettext(
                "“%(name)s” cannot be deleted: %(count)d piece of it is registered.",
                "“%(name)s” cannot be deleted: %(count)d pieces of it are registered.",
                pieces,
            )
            % {"name": label, "count": pieces},
        )
    else:
        messages.success(request, _("Article deleted: %(article)s") % {"article": label})
    return redirect(back_url)


# ---------- Pieces of every collector ----------


@management_required
def collectable_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    collector_filter = request.GET.get("collector", "")
    article_filter = request.GET.get("article", "")
    type_filter = request.GET.get("tipo", "")

    base = Collectable.objects.all()
    if query:
        base = base.filter(Q(article__name__icontains=query) | Q(participant__name__icontains=query))
    if collector_filter.isdigit():
        base = base.filter(collector_id=collector_filter)
    if article_filter.isdigit():
        base = base.filter(article_id=article_filter)
    labels = {"all": _("All"), "buying": _("Purchases"), "selling": _("Sales")}
    tabs = [
        (value, labels[key], (base.filter(trade_type=value) if value else base).count()) for value, key in TYPE_TABS
    ]
    related = ("collector", "participant", "article")
    pieces = (
        (base.filter(trade_type=type_filter) if type_filter else base)
        .select_related(*related)
        .order_by("article__name", "trade_date", "pk")
    )

    page_obj = Paginator(pieces, PAGE_SIZE).get_page(request.GET.get("page"))
    # The latest additions go first (only on the unfiltered first page), then the A-Z list.
    recent: list[Collectable] = []
    if page_obj.number == 1 and not (query or collector_filter or article_filter or type_filter):
        recent = list(Collectable.objects.select_related(*related).order_by("-pk")[:RECENT_COUNT])
    return render(
        request,
        "manage/collectable_list.html",
        {
            "recent": recent,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "tabs": tabs,
            "query": query,
            "type_filter": type_filter,
            "collector_filter": collector_filter,
            "collectors": User.objects.order_by("username"),
            "article_filter": article_filter,
            "article_options": article_choices(),
        },
    )


@management_required
def collectable_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    piece = get_object_or_404(Collectable.objects.select_related("article"), pk=pk) if pk else None
    back_url = safe_next_url(request, reverse("manage_collectables"))
    # A new piece starts as the signed-in user's; "Save and add another" and `?article=` fill the rest.
    initial: dict[str, int | str] = {}
    if piece is None:
        initial = {"collector": request.user.pk, **initial_from_query(request, (*CARRY_OVER, "article"))}
    form = CollectableEntryForm(request.POST or None, instance=piece, initial=initial)
    if request.method == "POST" and form.is_valid():
        saved = save_piece(request, form, created=piece is None, origin=ORIGIN)
        if saved:
            next_query = urlencode({"next": back_url})
            if "save_add_another" in request.POST:
                query = carry_over_query(form, CARRY_OVER)
                return redirect(f"{reverse('manage_collectable_new')}?{query}&{next_query}")
            if "save_continue" in request.POST:
                return redirect(f"{reverse('manage_collectable_edit', args=[saved.pk])}?{next_query}")
            return redirect(back_url)
    context = piece_form_context(form, piece)
    context["back_url"] = back_url
    return render(request, "manage/collectable_form.html", context)


@management_required
@require_POST
def collectable_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Delete a piece. As in the comics collection, a sale that sold a deleted purchase stays, with
    no previous trade (Collectable.previous_trade is SET_NULL)."""
    piece = get_object_or_404(Collectable.objects.select_related("article"), pk=pk)
    label = str(piece)
    back_url = safe_next_url(request, reverse("manage_collectables"))
    with transaction.atomic():
        log_action(request, piece, DELETION, f"Eliminado desde {ORIGIN}.")
        piece.delete()
    messages.success(request, _("Piece deleted: %(piece)s") % {"piece": label})
    return redirect(back_url)
