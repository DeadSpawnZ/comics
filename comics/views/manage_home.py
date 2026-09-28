"""Gestión home: shortcuts, counts and the recent change history (the admin's LogEntry), so there is
no need to switch to the Django admin to see what changed."""

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from comics.models import Collection, Connecting, Edition, Publishing, ReadingArc

HISTORY_PAGE_SIZE = 40

# Models with a Gestión page: content type model name -> (model, edit URL name).
MANAGE_PAGES = {
    "collection": (Collection, "manage_collection_edit"),
    "edition": (Edition, "manage_edition_edit"),
    "publishing": (Publishing, "manage_publishing_edit"),
    "readingarc": (ReadingArc, "manage_arc_edit"),
    "connecting": (Connecting, "manage_connecting_edit"),
}

TYPE_FILTERS = [
    ("", gettext_lazy("Everything")),
    ("collection", gettext_lazy("Pieces")),
    ("edition", gettext_lazy("Editions")),
    ("publishing", "Publishings"),
    ("readingarc", gettext_lazy("Reading arcs")),
    ("connecting", "Connectings"),
    ("other", gettext_lazy("Other")),
]

TYPE_LABELS = {
    "collection": gettext_lazy("Piece"),
    "edition": gettext_lazy("Edition"),
    "publishing": "Publishing",
    "readingarc": gettext_lazy("Reading arc"),
    "connecting": "Connecting",
    "issue": "Issue",
    "title": gettext_lazy("Title"),
    "dealer": gettext_lazy("Participant"),
    "editorial": "Editorial",
    "artist": gettext_lazy("Artist"),
    "signature": gettext_lazy("Signature"),
    "user": gettext_lazy("User"),
}

ACTIONS = {
    ADDITION: ("add_circle", "is-addition", gettext_lazy("Added")),
    CHANGE: ("edit", "is-change", gettext_lazy("Changed")),
    DELETION: ("delete", "is-deletion", gettext_lazy("Deleted")),
}


def _link(entry, existing):
    """Where an entry points: its Gestión page, else its admin page; nothing if it was deleted."""
    if entry.action_flag == DELETION:
        return None, False
    model = entry.content_type.model
    if model in MANAGE_PAGES:
        if entry.object_id not in existing.get(model, set()):
            return None, False
        return f"{reverse(MANAGE_PAGES[model][1], args=[entry.object_id])}?next={reverse('manage_home')}", False
    try:
        return entry.get_admin_url() or None, True
    except NoReverseMatch:
        return None, False


def _existing_ids(entries):
    """{model: {object_id, ...}} of the objects (with a Gestión page) that still exist."""
    wanted = {}
    for entry in entries:
        if entry.content_type.model in MANAGE_PAGES and entry.object_id and entry.object_id.isdigit():
            wanted.setdefault(entry.content_type.model, set()).add(int(entry.object_id))
    return {
        model: {str(pk) for pk in MANAGE_PAGES[model][0].objects.filter(pk__in=ids).values_list("pk", flat=True)}
        for model, ids in wanted.items()
    }


def _day_label(day, today):
    if day == today:
        return _("Today")
    if (today - day).days == 1:
        return _("Yesterday")
    return None  # the template formats the date


@staff_member_required
def manage_home(request):
    type_filter = request.GET.get("tipo", "")
    user_filter = request.GET.get("usuario", "")

    entries = LogEntry.objects.select_related("user", "content_type").order_by("-action_time")
    if type_filter == "other":
        entries = entries.exclude(content_type__model__in=MANAGE_PAGES)
    elif type_filter in MANAGE_PAGES:
        entries = entries.filter(content_type__app_label="comics", content_type__model=type_filter)
    if user_filter.isdigit():
        entries = entries.filter(user_id=user_filter)

    page_obj = Paginator(entries, HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))
    existing = _existing_ids(page_obj)
    today = timezone.localdate()
    days = []
    for entry in page_obj:
        entry.icon, entry.css, entry.verb = ACTIONS.get(entry.action_flag, ACTIONS[CHANGE])
        entry.link, entry.in_admin = _link(entry, existing)
        entry.type_label = TYPE_LABELS.get(entry.content_type.model, entry.content_type.name)
        day = timezone.localtime(entry.action_time).date()
        if not days or days[-1]["date"] != day:
            days.append({"date": day, "label": _day_label(day, today), "entries": []})
        days[-1]["entries"].append(entry)

    stats = [
        (_("Pieces"), Collection.objects.count(), "manage_collections", "inventory_2"),
        ("Publishings", Publishing.objects.count(), "manage_publishings", "collections_bookmark"),
        (_("Editions"), Edition.objects.count(), "manage_editions", "menu_book"),
        (_("Reading arcs"), ReadingArc.objects.count(), "manage_arcs", "format_list_numbered"),
        ("Connectings", Connecting.objects.count(), "manage_connectings", "grid_view"),
    ]
    return render(
        request,
        "manage/home.html",
        {
            "days": days,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "type_filters": TYPE_FILTERS,
            "type_filter": type_filter,
            "user_filter": user_filter,
            "users": User.objects.filter(is_staff=True).order_by("username"),
            "stats": stats,
        },
    )
