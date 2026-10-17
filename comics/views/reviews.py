"""The review area: the queue of change requests and the page where Reviewers validate or reject
them and Administrators approve or reject them (comics.change_requests). It is not part of Gestión:
it is gated by the change-request permissions (decorators.review_required), so Reviewers can use it
without getting Gestión. Every step is a POST with the CSRF token and an optional note."""

from __future__ import annotations

from typing import Protocol

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy, pgettext_lazy
from django.views.decorators.http import require_GET, require_POST

from comics import change_requests
from comics.decorators import review_required
from comics.forms import ReviewNoteForm
from comics.models import ChangeRequest, Edition
from comics.roles import can_access_management

PAGE_SIZE = 25
OPEN = "open"
ALL = "all"
STATUS_TABS = [
    (OPEN, gettext_lazy("To review")),
    (ChangeRequest.Status.PENDING, gettext_lazy("Pending")),
    (ChangeRequest.Status.VALIDATED, gettext_lazy("Validated")),
    (ChangeRequest.Status.APPROVED, gettext_lazy("Approved")),
    (ChangeRequest.Status.REJECTED, gettext_lazy("Rejected")),
    (ChangeRequest.Status.WITHDRAWN, gettext_lazy("Withdrawn")),
    (ALL, pgettext_lazy("requests", "All")),
]
SUCCESS_MESSAGES = {
    change_requests.VALIDATE: gettext_lazy("Request validated."),
    change_requests.APPROVE: gettext_lazy("Request approved: the change is applied."),
    change_requests.REJECT: gettext_lazy("Request rejected."),
}


@review_required
@require_GET
def review_queue(request: HttpRequest) -> HttpResponse:
    selected = request.GET.get("status", OPEN)
    if selected not in dict(STATUS_TABS):
        selected = OPEN
    counts = dict(ChangeRequest.objects.order_by().values_list("status").annotate(count=Count("pk")))
    tab_counts = {
        OPEN: sum(counts.get(status, 0) for status in ChangeRequest.OPEN_STATUSES),
        ALL: sum(counts.values()),
        **counts,
    }
    tabs = [(value, label, tab_counts.get(value, 0)) for value, label in STATUS_TABS]

    requests = ChangeRequest.objects.select_related("author")
    if selected == OPEN:
        # Oldest first: the queue is worked through in order of arrival.
        requests = requests.open().order_by("created_at", "id")
    elif selected != ALL:
        requests = requests.filter(status=selected)
    page_obj = Paginator(requests, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "reviews/queue.html",
        {
            "tabs": tabs,
            "selected": selected,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        },
    )


@review_required
@require_GET
def review_detail(request: HttpRequest, pk: int) -> HttpResponse:
    change_request = get_object_or_404(
        ChangeRequest.objects.select_related("author", "validated_by", "decided_by"), pk=pk
    )
    review = change_requests.review_request(change_request)
    history_url = None
    if review.target is not None and can_access_management(request.user):
        name = "manage_edition_history" if change_request.model is Edition else "manage_issue_history"
        history_url = reverse(name, args=[change_request.target_pk])
    return render(
        request,
        "reviews/detail.html",
        {
            "change_request": change_request,
            "reviews": review.fields,
            "has_conflicts": review.has_conflicts,
            "target_deleted": change_request.action == ChangeRequest.Action.UPDATE and review.target is None,
            "actions": change_request.allowed_actions(request.user),
            "note_form": ReviewNoteForm(),
            "history_url": history_url,
        },
    )


class Step(Protocol):
    """A step of comics.change_requests that takes a note (validate, approve, reject)."""

    def __call__(self, change_request: ChangeRequest, *, user: User, note: str) -> object: ...


def _take_step(request: HttpRequest, pk: int, action: str, step: Step) -> HttpResponseRedirect:
    change_request = get_object_or_404(ChangeRequest, pk=pk)
    form = ReviewNoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    else:
        try:
            step(change_request, user=request.user, note=form.cleaned_data["note"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, SUCCESS_MESSAGES[action])
    return redirect("review_detail", pk)


@review_required
@require_POST
def review_validate(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    return _take_step(request, pk, change_requests.VALIDATE, change_requests.validate)


@review_required
@require_POST
def review_approve(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    return _take_step(request, pk, change_requests.APPROVE, change_requests.approve)


@review_required
@require_POST
def review_reject(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    return _take_step(request, pk, change_requests.REJECT, change_requests.reject)
