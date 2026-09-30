"""Registering pieces (Collection records): in Gestión for any collector, and in Mis comics for
the signed-in user. Both use CollectionEntryForm and the two small JSON endpoints below."""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from comics.forms import (
    CollectionEntryForm,
    SignatureFormSet,
    _number_key,
    available_purchases,
    edition_label,
    purchase_label,
)
from comics.models import Collection, Edition
from comics.views.manage_editions import PAGE_SIZE, RECENT_COUNT, _log, _safe_next

# Values carried over by "Save and add another" (a batch bought the same day from the same person).
CARRY_OVER = ("publishing", "trade_date", "trade_type", "participant", "collector")
TYPE_TABS = [("", "all"), (Collection.TradeChoices.BUYING, "buying"), (Collection.TradeChoices.SELLING, "selling")]


def _carried_initial(request):
    return {key: request.GET[key] for key in CARRY_OVER if request.GET.get(key)}


def _carry_over_query(form):
    values = {key: form.data.get(key) for key in CARRY_OVER if form.data.get(key)}
    return urlencode(values)


def _signature_formset(request, form):
    return SignatureFormSet(request.POST or None, instance=form.instance, prefix="signatures")


def _save_entry(request, form, signatures, created):
    """Save the piece and its signatures together; on success log it and return the piece,
    otherwise add the error to the form."""
    try:
        with transaction.atomic():
            piece = form.save()
            signatures.instance = piece
            signatures.save()
    except ValidationError as exc:
        form.add_error(None, exc)
        return None
    origin = "Gestión" if request.resolver_match.url_name.startswith("manage_") else "Mis comics"
    _log(request, piece, ADDITION if created else CHANGE, f"{'Creado' if created else 'Modificado'} desde {origin}.")
    message = _("Piece added: %(piece)s") if created else _("Piece saved: %(piece)s")
    messages.success(request, message % {"piece": piece.edition})
    return piece


def _form_context(form, signatures, piece):
    selected = form["edition"].value()
    edition = Edition.objects.filter(pk=selected).first() if selected else None
    return {
        "form": form,
        "signatures": signatures,
        "piece": piece,
        "selected_edition": edition,
        "form_data": {
            "editionsUrl": reverse("comics_api_editions"),
            "purchasesUrl": reverse("comics_api_purchases"),
            "current": piece.pk if piece else None,
        },
    }


# ---------- JSON endpoints used by the form ----------


@login_required
@require_GET
def api_editions(request):
    try:
        publishing_id = int(request.GET.get("publishing", ""))
    except ValueError:
        return JsonResponse({"results": []})
    editions = Edition.objects.filter(publishing_id=publishing_id).select_related("publishing")
    editions = sorted(editions, key=lambda item: (_number_key(item.number), item.variant, item.printing))
    return JsonResponse(
        {
            "results": [
                {
                    "id": edition.pk,
                    "label": edition_label(edition),
                    "title": edition.short_name,
                    "thumbnail": edition.thumbnail.url if edition.thumbnail else None,
                    "coverPrice": str(edition.cover_price),
                }
                for edition in editions
            ]
        }
    )


@login_required
@require_GET
def api_purchases(request):
    """Purchases a sale can point to. Only staff may ask about another collector's pieces."""
    collector = request.user
    if request.user.is_staff and request.GET.get("collector", "").isdigit():
        collector = User.objects.filter(pk=request.GET["collector"]).first() or collector
    try:
        edition_id = int(request.GET.get("edition", ""))
    except ValueError:
        return JsonResponse({"results": []})
    current = None
    if request.GET.get("current", "").isdigit():
        current = Collection.objects.filter(pk=request.GET["current"], collector=collector).first()
    purchases = available_purchases(collector, edition_id, parse_date(request.GET.get("before", "")), current)
    return JsonResponse({"results": [{"id": item.pk, "label": purchase_label(item)} for item in purchases]})


# ---------- Mis comics: "Nueva pieza" for the signed-in user ----------


@login_required
def my_piece_new(request):
    form = CollectionEntryForm(request.POST or None, collector=request.user, initial=_carried_initial(request))
    signatures = _signature_formset(request, form)
    if request.method == "POST" and all([form.is_valid(), signatures.is_valid()]):
        piece = _save_entry(request, form, signatures, created=True)
        if piece:
            if "save_add_another" in request.POST:
                return redirect(f"{reverse('comics_piece_new')}?{_carry_over_query(form)}")
            # Show the collection on the letter where the new piece is listed.
            title = piece.edition.publishing.title
            letter = (title.name if title else piece.edition.publishing.publishing_title)[:1].upper()
            return redirect(f"{reverse('comics')}?{urlencode({'letter': letter})}")
    return render(request, "my_comics/piece_form.html", _form_context(form, signatures, None))


# ---------- Gestión: pieces of every collector ----------


@staff_member_required
def collection_list(request):
    query = request.GET.get("q", "").strip()
    collector_filter = request.GET.get("collector", "")
    type_filter = request.GET.get("tipo", "")

    base = Collection.objects.all()
    if query:
        base = base.filter(
            Q(edition__publishing__publishing_title__icontains=query) | Q(participant__name__icontains=query)
        )
    if collector_filter.isdigit():
        base = base.filter(collector_id=collector_filter)
    labels = {"all": _("All"), "buying": _("Purchases"), "selling": _("Sales")}
    tabs = [
        (value, labels[key], (base.filter(trade_type=value) if value else base).count()) for value, key in TYPE_TABS
    ]
    related = ("collector", "participant", "edition__publishing")
    pieces = (
        (base.filter(trade_type=type_filter) if type_filter else base)
        .select_related(*related)
        .order_by(
            "edition__publishing__publishing_title",
            "edition__publishing__year",
            "edition__number",
            "edition__variant",
            "trade_date",
        )
    )

    page_obj = Paginator(pieces, PAGE_SIZE).get_page(request.GET.get("page"))
    # The latest additions go first (only on the unfiltered first page), then the A-Z list.
    recent = []
    if page_obj.number == 1 and not (query or collector_filter or type_filter):
        recent = list(Collection.objects.select_related(*related).order_by("-id")[:RECENT_COUNT])
    return render(
        request,
        "manage/collection_list.html",
        {
            "recent": recent,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "tabs": tabs,
            "query": query,
            "type_filter": type_filter,
            "collector_filter": collector_filter,
            "collectors": User.objects.order_by("username"),
        },
    )


@staff_member_required
def collection_form(request, pk=None):
    piece = get_object_or_404(Collection.objects.select_related("edition"), pk=pk) if pk else None
    back_url = _safe_next(request, reverse("manage_collections"))
    initial = {} if piece else {"collector": request.user.pk, **_carried_initial(request)}
    form = CollectionEntryForm(request.POST or None, instance=piece, initial=initial)
    signatures = _signature_formset(request, form)
    if request.method == "POST" and all([form.is_valid(), signatures.is_valid()]):
        saved = _save_entry(request, form, signatures, created=piece is None)
        if saved:
            if "save_add_another" in request.POST:
                query = _carry_over_query(form)
                return redirect(f"{reverse('manage_collection_new')}?{query}&{urlencode({'next': back_url})}")
            if "save_continue" in request.POST:
                return redirect(f"{reverse('manage_collection_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
            return redirect(back_url)
    context = _form_context(form, signatures, piece)
    context["back_url"] = back_url
    return render(request, "manage/collection_form.html", context)


@staff_member_required
@require_POST
def collection_delete(request, pk):
    piece = get_object_or_404(Collection.objects.select_related("edition"), pk=pk)
    label = str(piece.edition)
    back_url = _safe_next(request, reverse("manage_collections"))
    try:
        with transaction.atomic():
            _log(request, piece, DELETION, "Eliminado desde Gestión.")
            piece.delete()
    except ProtectedError:
        messages.error(request, _("“%(name)s” cannot be deleted: it has signatures registered.") % {"name": label})
    else:
        messages.success(request, _("Piece deleted: %(piece)s") % {"piece": label})
    return redirect(back_url)
