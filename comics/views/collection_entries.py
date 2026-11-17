"""Registering pieces (Collection records): in Gestión for any collector, and in Mis comics for
the signed-in user. Both use CollectionEntryForm and the two small JSON endpoints below."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, ProtectedError
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.functional import Promise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, pgettext_lazy
from django.views.decorators.http import require_GET, require_POST

from comics.decorators import management_required
from comics.forms import (
    CollectionEntryForm,
    SignatureFormSet,
    _number_key,
    available_purchases,
    edition_label,
    purchase_label,
)
from comics.helper import parse_date_or_none, parse_id_or_none
from comics.models import Collection, CollectionQuerySet, Dealer, Edition, Signature, TradeStatus
from comics.views.manage_editions import PAGE_SIZE, RECENT_COUNT_LONG
from comics.views.utils import (
    carry_over_query,
    first_sale_date,
    initial_from_query,
    log_action,
    requested_collector,
    safe_next_url,
)

# Values carried over by "Save and add another" (a batch bought the same day from the same person).
CARRY_OVER = ("publishing", "trade_date", "trade_type", "participant", "collector")
TYPE_TABS = [("", "all"), (Collection.TradeChoices.BUYING, "buying"), (Collection.TradeChoices.SELLING, "selling")]
# The "participant" filter value for pieces with no participant (any other value is a Dealer id).
NO_PARTICIPANT = "none"
# Orderings of the Gestión collection list: "sort" value -> (label, order_by). The default ("") is A-Z by
# title; each one ends in the pk so the pages stay stable when the other values tie.
SORT_OPTIONS: dict[str, tuple[Promise, tuple[str, ...]]] = {
    "": (
        gettext_lazy("Title A–Z"),
        (
            "edition__publishing__publishing_title",
            "edition__publishing__year",
            "edition__number",
            "edition__variant",
            "trade_date",
            "pk",
        ),
    ),
    "date_desc": (gettext_lazy("Trade date: newest first"), ("-trade_date", "-pk")),
    "date_asc": (gettext_lazy("Trade date: oldest first"), ("trade_date", "pk")),
    "amount_desc": (gettext_lazy("Amount: highest first"), ("-amount", "-pk")),
    "amount_asc": (gettext_lazy("Amount: lowest first"), ("amount", "pk")),
    "recent": (pgettext_lazy("pieces", "Recently added"), ("-pk",)),
}


def _signature_formset(request: HttpRequest, form: CollectionEntryForm) -> BaseInlineFormSet:
    return SignatureFormSet(request.POST or None, instance=form.instance, prefix="signatures")


def _save_entry(
    request: HttpRequest, form: CollectionEntryForm, signatures: BaseInlineFormSet, *, created: bool, origin: str
) -> Collection | None:
    """Save the piece and its signatures together; on success log it (made "desde `origin`", the
    page it was made from) and return the piece, otherwise add the error to the form."""
    try:
        with transaction.atomic():
            piece = form.save()
            signatures.instance = piece
            signatures.save()
    except ValidationError as exc:
        form.add_error(None, exc)
        return None
    log_action(
        request, piece, ADDITION if created else CHANGE, f"{'Creado' if created else 'Modificado'} desde {origin}."
    )
    message = _("Piece added: %(piece)s") if created else _("Piece saved: %(piece)s")
    messages.success(request, message % {"piece": piece.edition})
    return piece


def _form_context(form: CollectionEntryForm, signatures: BaseInlineFormSet, piece: Collection | None) -> dict[str, Any]:
    # The submitted value may be malformed; the form reports that, here it only means "no edition".
    selected = parse_id_or_none(form["edition"].value())
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
def api_editions(request: HttpRequest) -> JsonResponse:
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
def api_purchases(request: HttpRequest) -> JsonResponse:
    """Purchases a sale can point to. Only Gestión users may ask about another collector's pieces."""
    collector = requested_collector(request)
    try:
        edition_id = int(request.GET.get("edition", ""))
    except ValueError:
        return JsonResponse({"results": []})
    current = None
    if request.GET.get("current", "").isdigit():
        current = Collection.objects.filter(pk=request.GET["current"], collector=collector).first()
    purchases = available_purchases(collector, edition_id, parse_date_or_none(request.GET.get("before")), current)
    return JsonResponse({"results": [{"id": item.pk, "label": purchase_label(item)} for item in purchases]})


# ---------- Mis comics: "Nueva pieza" for the signed-in user ----------


@login_required
def my_piece_new(request: HttpRequest) -> HttpResponse:
    initial = initial_from_query(request, CARRY_OVER)
    form = CollectionEntryForm(request.POST or None, collector=request.user, initial=initial)
    signatures = _signature_formset(request, form)
    if request.method == "POST" and all([form.is_valid(), signatures.is_valid()]):
        piece = _save_entry(request, form, signatures, created=True, origin="Mis comics")
        if piece:
            if "save_add_another" in request.POST:
                return redirect(f"{reverse('comics_piece_new')}?{carry_over_query(form, CARRY_OVER)}")
            # Show the collection on the letter where the new piece is listed.
            title = piece.edition.publishing.title
            letter = (title.name if title else piece.edition.publishing.publishing_title)[:1].upper()
            return redirect(f"{reverse('comics')}?{urlencode({'letter': letter})}")
    return render(request, "my_comics/piece_form.html", _form_context(form, signatures, None))


# ---------- Gestión: pieces of every collector ----------


@dataclass(frozen=True)
class CollectionListFilters:
    """The GET parameters of the Gestión collection list, normalized: a value the list does not know
    (a collector that is not an id, an unknown status or sort...) becomes "" and is ignored."""

    query: str = ""
    collector: str = ""
    participant: str = ""  # a Dealer id, or NO_PARTICIPANT
    status: str = ""  # a TradeStatus value
    trade_type: str = ""  # the "tipo" tab
    sort: str = ""  # a SORT_OPTIONS key

    @classmethod
    def from_request(cls, request: HttpRequest) -> CollectionListFilters:
        params = request.GET
        participant = params.get("participant", "")
        trade_type = params.get("tipo", "")
        status = params.get("status", "")
        sort = params.get("sort", "")
        return cls(
            query=params.get("q", "").strip(),
            collector=_digits_or_empty(params.get("collector", "")),
            participant=participant if participant == NO_PARTICIPANT else _digits_or_empty(participant),
            status=status if status in TradeStatus.values else "",
            trade_type=trade_type if trade_type in Collection.TradeChoices.values else "",
            sort=sort if sort in SORT_OPTIONS else "",
        )

    @property
    def narrowed(self) -> bool:
        """Whether a filter of the filters row (not the tab, not the sort) is set: what Clear removes."""
        return bool(self.query or self.collector or self.participant or self.status)

    def apply(self, pieces: CollectionQuerySet) -> CollectionQuerySet:
        """`pieces` narrowed by the search, collector, participant and status (the tab is left out:
        the tab counters are computed from this)."""
        if self.query:
            pieces = pieces.filter(edition__publishing__publishing_title__icontains=self.query)
        if self.collector:
            pieces = pieces.filter(collector_id=self.collector)
        if self.participant == NO_PARTICIPANT:
            pieces = pieces.filter(participant__isnull=True)
        elif self.participant:
            pieces = pieces.filter(participant_id=self.participant)
        return pieces.with_status(self.status)


def _digits_or_empty(value: str) -> str:
    return value if value.isdigit() else ""


@management_required
def collection_list(request: HttpRequest) -> HttpResponse:
    filters = CollectionListFilters.from_request(request)
    base = filters.apply(Collection.objects.all())
    labels = {"all": _("All"), "buying": _("Purchases"), "selling": _("Sales")}
    tabs = [
        (value, labels[key], (base.filter(trade_type=value) if value else base).count()) for value, key in TYPE_TABS
    ]
    related = ("collector", "participant", "edition__publishing")
    editorials = "edition__publishing__editorials"  # read by Edition.__str__ (the delete button's name)
    pieces = (
        (base.filter(trade_type=filters.trade_type) if filters.trade_type else base)
        .select_related(*related)
        .prefetch_related(editorials)
        .with_sale_date()
        .order_by(*SORT_OPTIONS[filters.sort][1])
    )

    page_obj = Paginator(pieces, PAGE_SIZE).get_page(request.GET.get("page"))
    # The latest additions go first (only on the unfiltered first page in the default order), then the A-Z list.
    recent = []
    if page_obj.number == 1 and filters == CollectionListFilters():
        rows = Collection.objects.select_related(*related).prefetch_related(editorials).with_sale_date()
        recent = list(rows.order_by("-id")[:RECENT_COUNT_LONG])
    return render(
        request,
        "manage/collection_list.html",
        {
            "recent": recent,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "tabs": tabs,
            "filters": filters,
            "collectors": User.objects.order_by("username"),
            # Only the dealers that take part in some comic trade, A-Z (Dealer.Meta.ordering).
            "participants": Dealer.objects.filter(Exists(Collection.objects.filter(participant=OuterRef("pk")))),
            "no_participant": NO_PARTICIPANT,
            "statuses": TradeStatus.choices,
            "sort_options": [(value, label) for value, (label, _order) in SORT_OPTIONS.items()],
        },
    )


@management_required
def collection_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    piece = get_object_or_404(Collection.objects.select_related("edition").with_sale_date(), pk=pk) if pk else None
    back_url = safe_next_url(request, reverse("manage_collections"))
    initial = {} if piece else {"collector": request.user.pk, **initial_from_query(request, CARRY_OVER)}
    form = CollectionEntryForm(request.POST or None, instance=piece, initial=initial)
    signatures = _signature_formset(request, form)
    if request.method == "POST" and all([form.is_valid(), signatures.is_valid()]):
        saved = _save_entry(request, form, signatures, created=piece is None, origin="Gestión")
        if saved:
            if "save_add_another" in request.POST:
                query = carry_over_query(form, CARRY_OVER)
                return redirect(f"{reverse('manage_collection_new')}?{query}&{urlencode({'next': back_url})}")
            if "save_continue" in request.POST:
                return redirect(f"{reverse('manage_collection_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
            return redirect(back_url)
    context = _form_context(form, signatures, piece)
    context["back_url"] = back_url
    return render(request, "manage/collection_form.html", context)


def _delete_refusal(piece: Collection, label: str, error: ProtectedError) -> str:
    """Why `piece` could not be deleted: a sale points to it (previous_trade), it has signatures
    (Signature.collectable), or both."""
    sold_on = first_sale_date(piece, error)
    signed = any(isinstance(obj, Signature) for obj in error.protected_objects)
    if sold_on is None:
        return _("“%(name)s” cannot be deleted: it has signatures registered.") % {"name": label}
    sale_date = date_format(sold_on)
    if signed:
        reason = _("It was sold on %(date)s and it has signatures registered.") % {"date": sale_date}
    else:
        reason = _("It was sold on %(date)s: delete the sale first.") % {"date": sale_date}
    return _("“%(name)s” cannot be deleted. %(reason)s") % {"name": label, "reason": reason}


@management_required
@require_POST
def collection_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    piece = get_object_or_404(Collection.objects.select_related("edition"), pk=pk)
    label = str(piece.edition)
    back_url = safe_next_url(request, reverse("manage_collections"))
    try:
        with transaction.atomic():
            log_action(request, piece, DELETION, "Eliminado desde Gestión.")
            piece.delete()
    except ProtectedError as error:
        messages.error(request, _delete_refusal(piece, label, error))
    else:
        messages.success(request, _("Piece deleted: %(piece)s") % {"piece": label})
    return redirect(back_url)
