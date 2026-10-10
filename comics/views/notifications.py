"""Notifications of the signed-in user: the navbar bell's menu, the full list and marking them read.

Users only ever see and act on their own notifications (anyone else's is a 404). Marking read is
a state change, so it is POST-only (with the CSRF token)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from comics.models import Notification
from comics.views.utils import safe_next_url

MENU_SIZE = 6  # latest notifications in the bell's menu
PAGE_SIZE = 20


@login_required
@require_GET
def notification_list(request: HttpRequest) -> HttpResponse:
    notifications = Notification.objects.filter(recipient=request.user).for_display()
    page_obj = Paginator(notifications, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "notifications/list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        },
    )


@login_required
@require_GET
def notification_menu(request: HttpRequest) -> HttpResponse:
    """The bell's menu (an HTML fragment, loaded when the menu opens so pages only pay for the
    badge). `next` is the page it is shown on, where "Mark all as read" returns."""
    notifications = list(Notification.objects.filter(recipient=request.user).for_display()[:MENU_SIZE])
    return render(
        request,
        "notifications/includes/menu.html",
        {
            "notifications": notifications,
            "next_url": safe_next_url(request, reverse("notifications")),
        },
    )


@login_required
@require_POST
def notification_open(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Mark one notification read and go to what it is about (the notifications page when it
    points nowhere, or somewhere that is not on this site)."""
    notification = get_object_or_404(Notification.objects.for_display(), pk=pk, recipient=request.user)
    notification.mark_read()
    target = notification.target_url
    if not target or not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = reverse("notifications")
    return HttpResponseRedirect(target)


@login_required
@require_POST
def notification_mark_all_read(request: HttpRequest) -> HttpResponseRedirect:
    Notification.objects.filter(recipient=request.user).mark_read()
    return HttpResponseRedirect(safe_next_url(request, reverse("notifications")))
