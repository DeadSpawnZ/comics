"""Comics module pages that show the catalog from the point of view of the signed-in user:
what they own and what they are missing. Editing these records belongs to Gestión."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from comics.models import Connecting, ReadingArc

COVER_STRIP_SIZE = 6
ENTRY_FILTERS = ("all", "owned", "missing")


def _percent(part, total):
    return round(part * 100 / total) if total else 0


@login_required
def arc_list(request):
    cards = []
    for arc in ReadingArc.objects.all():
        entries = arc.entries_with_ownership(request.user)
        owned = sum(1 for entry in entries if entry.owned)
        cards.append(
            {
                "arc": arc,
                "total": len(entries),
                "owned": owned,
                "percent": _percent(owned, len(entries)),
                "covers": [entry for entry in entries if entry.cover_url][:COVER_STRIP_SIZE],
            }
        )
    return render(request, "my_comics/arc_list.html", {"cards": cards})


@login_required
def arc_detail(request, pk):
    arc = get_object_or_404(ReadingArc, pk=pk)
    entries = arc.entries_with_ownership(request.user)
    owned = sum(1 for entry in entries if entry.owned)

    selected = request.GET.get("show", "all")
    if selected not in ENTRY_FILTERS:
        selected = "all"
    if selected == "owned":
        shown = [entry for entry in entries if entry.owned]
    elif selected == "missing":
        shown = [entry for entry in entries if not entry.owned]
    else:
        shown = entries

    return render(
        request,
        "my_comics/arc_detail.html",
        {
            "arc": arc,
            "entries": shown,
            "total": len(entries),
            "owned": owned,
            "missing": len(entries) - owned,
            "percent": _percent(owned, len(entries)),
            "selected": selected,
        },
    )


def _connecting_summary(connecting, user):
    grid = connecting.ownership_grid(user)
    pieces = [piece for row in grid for piece in row if piece]
    owned = sum(1 for piece in pieces if piece.owned)
    return {
        "connecting": connecting,
        "grid": grid,
        "pieces": pieces,
        "total": len(pieces),
        "owned": owned,
        "percent": _percent(owned, len(pieces)),
    }


@login_required
def connecting_list(request):
    cards = [_connecting_summary(connecting, request.user) for connecting in Connecting.objects.order_by("name")]
    return render(request, "my_comics/connecting_list.html", {"cards": cards})


@login_required
def connecting_detail(request, pk):
    summary = _connecting_summary(get_object_or_404(Connecting, pk=pk), request.user)
    summary["missing_pieces"] = [piece for piece in summary["pieces"] if not piece.owned]
    return render(request, "my_comics/connecting_detail.html", summary)
