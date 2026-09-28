import json

from django import forms
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.http import require_GET

from comics.forms import publishing_choices
from comics.models import Edition, Connecting


class ConnectingForm(forms.ModelForm):
    class Meta:
        model = Connecting
        fields = ["name", "rows", "columns", "notes"]
        labels = {
            "name": gettext_lazy("Name"),
            "rows": gettext_lazy("Rows"),
            "columns": gettext_lazy("Columns"),
            "notes": gettext_lazy("Notes"),
        }
        error_messages = {"name": {"unique": gettext_lazy("A connecting with that name already exists.")}}


def _number_sort_key(edition):
    number = edition.number.strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def _edition_payload(edition):
    return {
        "id": edition.id,
        "title": edition.publishing.publishing_title,
        "detail": f"#{edition.number} {edition.variant_label} · {edition.printing}".strip(),
        "thumbnail": edition.thumbnail.url if edition.thumbnail else None,
    }


@staff_member_required
def connecting_list(request):
    connectings = Connecting.objects.annotate(piece_count=Count("pieces")).order_by("name")
    cards = [
        {
            "connecting": connecting,
            "grid": connecting.grid(),
            "piece_count": connecting.piece_count,
            "capacity": connecting.rows * connecting.columns,
        }
        for connecting in connectings
    ]
    return render(request, "manage/connecting_list.html", {"cards": cards})


@staff_member_required
def connecting_editor(request, pk=None):
    connecting = get_object_or_404(Connecting, pk=pk) if pk else None

    if request.method == "POST":
        return _save_connecting(request, connecting)

    pieces = []
    if connecting:
        pieces = [
            {"row": piece.row, "column": piece.column, "edition": _edition_payload(piece.edition)}
            for piece in connecting.pieces.select_related("edition__publishing")
        ]

    publishings = [{"id": pk, "label": label} for pk, label in publishing_choices()]

    editor_data = {
        "connecting": {
            "id": connecting.pk if connecting else None,
            "name": connecting.name if connecting else "",
            "rows": connecting.rows if connecting else 1,
            "columns": connecting.columns if connecting else 3,
            "notes": connecting.notes if connecting else "",
            "pieces": pieces,
        },
        "publishings": publishings,
        "saveUrl": request.path,
        "comicsUrl": reverse("manage_publishing_comics"),
        "listUrl": reverse("manage_connectings"),
    }
    return render(
        request,
        "manage/connecting_editor.html",
        {"connecting": connecting, "editor_data": editor_data},
    )


def _save_connecting(request, connecting):
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"errors": [_("The request is not valid.")]}, status=400)

    form = ConnectingForm(payload, instance=connecting)
    if not form.is_valid():
        errors = [
            f"{form.fields[field].label}: {message}" if field in form.fields else message
            for field, messages in form.errors.items()
            for message in messages
        ]
        return JsonResponse({"errors": errors}, status=400)

    obj = form.save(commit=False)
    try:
        placements = obj.clean_placements(payload.get("pieces") or [])
    except ValidationError as exc:
        return JsonResponse({"errors": exc.messages}, status=400)

    created = obj._state.adding
    with transaction.atomic():
        obj.save()
        obj.set_pieces(placements)
        # Record in the admin history ("Historia" and "Acciones recientes").
        pieces = f"{len(placements)} pieza{'s' if len(placements) != 1 else ''}"
        LogEntry.objects.log_actions(
            user_id=request.user.pk,
            queryset=[obj],
            action_flag=ADDITION if created else CHANGE,
            change_message=f"{'Creado' if created else 'Modificado'} desde Gestión ({obj.layout}, {pieces}).",
            single_object=True,
        )

    return JsonResponse({"id": obj.pk, "url": reverse("manage_connecting_edit", args=[obj.pk])})


@staff_member_required
@require_GET
def publishing_comics(request):
    try:
        publishing_id = int(request.GET.get("publishing", ""))
    except ValueError:
        return JsonResponse({"results": []})

    editions = sorted(
        Edition.objects.filter(publishing_id=publishing_id).select_related("publishing"),
        key=lambda edition: (_number_sort_key(edition), edition.variant, edition.printing),
    )
    return JsonResponse({"results": [_edition_payload(edition) for edition in editions]})
