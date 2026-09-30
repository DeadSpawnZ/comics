"""Comics module pages that show the catalog from the point of view of the signed-in user:
what they own and what they are missing. Editing these records belongs to Gestión."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from comics.models import Collection, Connecting, Edition, ReadingArc
from comics.views.manage_editions import _safe_next, edition_catalog_context, sibling_editions_of

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


def _owned_counts(user, edition_ids):
    """{edition_id: number of copies `user` owns} for the given editions."""
    counts = {}
    for edition_id in (
        Collection.objects.owned_by(user).filter(edition_id__in=edition_ids).values_list("edition_id", flat=True)
    ):
        counts[edition_id] = counts.get(edition_id, 0) + 1
    return counts


@login_required
def edition_list(request):
    """Read-only edition catalog with the same filters as Gestión, flagged with what the user owns."""
    context = edition_catalog_context(request)
    page_obj = context["page_obj"]
    counts = _owned_counts(request.user, [edition.id for edition in page_obj])
    for edition in page_obj:
        edition.owned_count = counts.get(edition.id, 0)
    return render(request, "my_comics/edition_list.html", context)


@login_required
def edition_detail(request, pk):
    edition = get_object_or_404(Edition.objects.select_related("publishing", "issue__publishing"), pk=pk)
    siblings = sibling_editions_of(edition)
    owned_ids = set(_owned_counts(request.user, [edition.id] + [sibling.id for sibling in siblings]))
    for sibling in siblings:
        sibling.owned = sibling.id in owned_ids
    copies = (
        Collection.objects.owned_by(request.user)
        .filter(edition=edition)
        .select_related("participant")
        .order_by("trade_date")
    )
    return render(
        request,
        "my_comics/edition_detail.html",
        {
            "edition": edition,
            "back_url": _safe_next(request, reverse("comics_editions")),
            "copies": copies,
            "cover_artists": edition.cover_artists.order_by("name"),
            "collected": edition.collected_entries.select_related("issue__publishing").order_by("order"),
            "sibling_editions": siblings,
            "sibling_groups": Edition.group_by_cover_kind(siblings),
            "linked_elsewhere": bool(
                edition.issue_id
                and (
                    edition.issue.publishing_id != edition.publishing_id
                    or edition.issue.number != edition.number.strip()
                )
            ),
        },
    )
