"""Gestión: announcements. An announcement is sent, when it is created, to everyone or to the members
of some roles (one notification each); its texts and link can be edited afterwards (the
notifications show the current ones) and deleting it removes its notifications."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from comics.decorators import management_required
from comics.forms import AnnouncementCreateForm, AnnouncementForm
from comics.models import Announcement
from comics.roles import role_label
from comics.views.utils import log_action, safe_next_url

PAGE_SIZE = 25


def _with_stats(announcements: QuerySet[Announcement]) -> QuerySet[Announcement]:
    """With `recipient_count` and `read_count` (from its notifications), its roles and author."""
    return (
        announcements.select_related("created_by")
        .prefetch_related("roles")
        .annotate(
            recipient_count=Count("notifications"),
            read_count=Count("notifications", filter=Q(notifications__read_at__isnull=False)),
        )
    )


def _audience_label(announcement: Announcement) -> str:
    """Uses the prefetched roles."""
    if announcement.audience == Announcement.Audience.EVERYONE:
        return _("Everyone")
    return ", ".join(sorted(role_label(group.name) for group in announcement.roles.all()))


@management_required
def announcement_list(request: HttpRequest) -> HttpResponse:
    # Explicit order: Meta.ordering is ignored by aggregated queries.
    announcements = _with_stats(Announcement.objects.all()).order_by("-created_at", "-id")
    page_obj = Paginator(announcements, PAGE_SIZE).get_page(request.GET.get("page"))
    for announcement in page_obj:
        announcement.audience_label = _audience_label(announcement)
    return render(
        request,
        "manage/announcement_list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        },
    )


@management_required
def announcement_create(request: HttpRequest) -> HttpResponse:
    back_url = safe_next_url(request, reverse("manage_announcements"))
    form = AnnouncementCreateForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            announcement = form.save(commit=False)
            announcement.created_by = request.user
            announcement.save()
            form.save_m2m()
            sent = announcement.publish()
            log_action(request, announcement, ADDITION, "Creado desde Gestión.")
        messages.success(
            request,
            ngettext(
                "Announcement sent to %(count)d user.",
                "Announcement sent to %(count)d users.",
                sent,
            )
            % {"count": sent},
        )
        return redirect(back_url)
    return render(
        request,
        "manage/announcement_form.html",
        {"announcement": None, "form": form, "back_url": back_url},
    )


@management_required
def announcement_form(request: HttpRequest, pk: int) -> HttpResponse:
    announcement = get_object_or_404(_with_stats(Announcement.objects.all()), pk=pk)
    back_url = safe_next_url(request, reverse("manage_announcements"))
    # Validate into a copy: a rejected POST must not change the header's title.
    editing = Announcement.objects.get(pk=announcement.pk)
    form = AnnouncementForm(request.POST if request.method == "POST" else None, instance=editing)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            log_action(request, saved, CHANGE, "Modificado desde Gestión.")
        messages.success(request, _("Announcement saved: %(title)s") % {"title": saved.title})
        if "save_continue" in request.POST:
            edit_url = reverse("manage_announcement_edit", args=[saved.pk])
            return redirect(f"{edit_url}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    return render(
        request,
        "manage/announcement_form.html",
        {
            "announcement": announcement,
            "audience_label": _audience_label(announcement),
            "form": form,
            "back_url": back_url,
        },
    )


@management_required
@require_POST
def announcement_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Delete an announcement and, with it (cascade), the notifications it sent."""
    announcement = get_object_or_404(Announcement, pk=pk)
    title = announcement.title
    back_url = safe_next_url(request, reverse("manage_announcements"))
    with transaction.atomic():
        log_action(request, announcement, DELETION, "Eliminado desde Gestión.")
        announcement.delete()
    messages.success(request, _("Announcement deleted: %(title)s") % {"title": title})
    return redirect(back_url)
