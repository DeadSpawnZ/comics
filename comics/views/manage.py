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
from django.views.decorators.http import require_GET

from comics.models import Collection, Edition, Connecting, Publishing


class ConnectingForm(forms.ModelForm):
    class Meta:
        model = Connecting
        fields = ["name", "rows", "columns", "notes"]
        labels = {"name": "Nombre", "rows": "Filas", "columns": "Columnas", "notes": "Notas"}
        error_messages = {"name": {"unique": "Ya existe un connecting con ese nombre."}}


def _number_sort_key(edition):
    number = edition.number.strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def _edition_payload(edition, owned_ids):
    return {
        "id": edition.id,
        "title": edition.publishing.publishing_title,
        "detail": f"#{edition.number} {edition.variant} · {edition.printing}".strip(),
        "thumbnail": edition.thumbnail.url if edition.thumbnail else None,
        "owned": edition.id in owned_ids,
    }


def _owned_edition_ids(user, edition_ids):
    return set(
        Collection.objects.owned_by(user).filter(edition_id__in=edition_ids).values_list("edition_id", flat=True)
    )


@staff_member_required
def connecting_list(request):
    connectings = Connecting.objects.annotate(piece_count=Count("pieces")).order_by("name")
    cards = []
    for connecting in connectings:
        grid = connecting.ownership_grid(request.user)
        pieces = [cell for row in grid for cell in row if cell]
        cards.append(
            {
                "connecting": connecting,
                "grid": grid,
                "piece_count": len(pieces),
                "owned_count": sum(1 for piece in pieces if piece.owned),
                "capacity": connecting.rows * connecting.columns,
            }
        )
    return render(request, "manage/connecting_list.html", {"cards": cards})


@staff_member_required
def connecting_editor(request, pk=None):
    connecting = get_object_or_404(Connecting, pk=pk) if pk else None

    if request.method == "POST":
        return _save_connecting(request, connecting)

    pieces = []
    if connecting:
        placed = list(connecting.pieces.select_related("edition__publishing"))
        owned_ids = _owned_edition_ids(request.user, [piece.edition_id for piece in placed])
        pieces = [
            {"row": piece.row, "column": piece.column, "edition": _edition_payload(piece.edition, owned_ids)}
            for piece in placed
        ]

    publishings = [
        {
            "id": publishing["id"],
            "label": " ".join(
                part
                for part in (
                    publishing["publishing_title"],
                    f"({publishing['year']})" if publishing["year"] else "",
                    publishing["serie"],
                    f"· {publishing['language'].upper()}",
                )
                if part
            ),
        }
        for publishing in Publishing.objects.order_by("publishing_title", "year", "serie").values(
            "id", "publishing_title", "year", "serie", "language"
        )
    ]

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
        return JsonResponse({"errors": ["La solicitud no es válida."]}, status=400)

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
        # Registro en el historial del admin ("Historia" y "Acciones recientes").
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
    owned_ids = _owned_edition_ids(request.user, [edition.id for edition in editions])
    return JsonResponse({"results": [_edition_payload(edition, owned_ids) for edition in editions]})
