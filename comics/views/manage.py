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

from comics.models import Collection, Comic, Connecting, Publishing


class ConnectingForm(forms.ModelForm):
    class Meta:
        model = Connecting
        fields = ["name", "rows", "columns", "notes"]
        labels = {"name": "Nombre", "rows": "Filas", "columns": "Columnas", "notes": "Notas"}
        error_messages = {"name": {"unique": "Ya existe un connecting con ese nombre."}}


def _number_sort_key(comic):
    number = comic.number.strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def _comic_payload(comic, owned_ids):
    return {
        "id": comic.id,
        "title": comic.publishing.publishing_title,
        "detail": f"#{comic.number} {comic.variant} · {comic.printing}".strip(),
        "thumbnail": comic.thumbnail.url if comic.thumbnail else None,
        "owned": comic.id in owned_ids,
    }


def _owned_comic_ids(user, comic_ids):
    return set(
        Collection.objects.owned_by(user).filter(comic_id__in=comic_ids).values_list("comic_id", flat=True)
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
        placed = list(connecting.pieces.select_related("comic__publishing"))
        owned_ids = _owned_comic_ids(request.user, [piece.comic_id for piece in placed])
        pieces = [
            {"row": piece.row, "column": piece.column, "comic": _comic_payload(piece.comic, owned_ids)}
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

    comics = sorted(
        Comic.objects.filter(publishing_id=publishing_id).select_related("publishing"),
        key=lambda comic: (_number_sort_key(comic), comic.variant, comic.printing),
    )
    owned_ids = _owned_comic_ids(request.user, [comic.id for comic in comics])
    return JsonResponse({"results": [_comic_payload(comic, owned_ids) for comic in comics]})
