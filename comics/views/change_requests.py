"""Change requests from the collectors' side (Comics module): suggest a correction to an edition or
an issue, propose a new one, follow one's own requests and withdraw them while pending. Reviewing
them is in comics.views.reviews."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics import change_requests
from comics.forms import EditionProposalForm, IssueProposalForm, ProposalForm
from comics.models import ChangeRequest, Edition, Issue
from comics.views.utils import safe_next_url

PAGE_SIZE = 20
# The kinds a new object can be proposed for, by their URL slug.
NEW_KINDS: dict[str, tuple[type[Model], type[ProposalForm]]] = {
    "edicion": (Edition, EditionProposalForm),
    "issue": (Issue, IssueProposalForm),
}


def _proposal_page(
    request: HttpRequest,
    model: type[Model],
    form_class: type[ProposalForm],
    target: Model | None,
    back_url: str,
) -> HttpResponse:
    """Show the proposal form and, once valid, submit the request and go to it."""
    # A fresh copy of the row: the form validates the proposal on it, the row is never saved.
    instance = model._default_manager.get(pk=target.pk) if target is not None else model()
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        change_request = change_requests.submit(
            author=request.user,
            model=model,
            changes=form.proposed_changes(),
            label=change_requests.target_label(target if target is not None else form.instance),
            reason=form.cleaned_data["reason"],
            evidence_url=form.cleaned_data["evidence_url"],
            target=target,
        )
        messages.success(request, _("Request sent: a reviewer will look at it soon."))
        return redirect("comics_request_detail", change_request.pk)
    model_fields = [form[name] for name in form.fields if name not in ProposalForm.REQUEST_FIELDS]
    return render(
        request,
        "change_requests/proposal_form.html",
        {
            "form": form,
            "model_fields": model_fields,
            "request_fields": [form[name] for name in ProposalForm.REQUEST_FIELDS],
            "is_edition": model is Edition,
            "target": target,
            "target_label": change_requests.target_label(target) if target is not None else "",
            "back_url": back_url,
        },
    )


@login_required
def suggest_edition_change(request: HttpRequest, pk: int) -> HttpResponse:
    edition = get_object_or_404(Edition.objects.select_related("publishing"), pk=pk)
    back_url = safe_next_url(request, reverse("comics_edition_detail", args=[pk]))
    return _proposal_page(request, Edition, EditionProposalForm, edition, back_url)


@login_required
def suggest_issue_change(request: HttpRequest, pk: int) -> HttpResponse:
    issue = get_object_or_404(Issue.objects.select_related("publishing"), pk=pk)
    back_url = safe_next_url(request, reverse("comics_editions"))
    return _proposal_page(request, Issue, IssueProposalForm, issue, back_url)


@login_required
def propose_new(request: HttpRequest, kind: str) -> HttpResponse:
    if kind not in NEW_KINDS:
        raise Http404
    model, form_class = NEW_KINDS[kind]
    back_url = safe_next_url(request, reverse("comics_requests"))
    return _proposal_page(request, model, form_class, None, back_url)


@login_required
def my_requests(request: HttpRequest) -> HttpResponse:
    requests = ChangeRequest.objects.filter(author=request.user)
    page_obj = Paginator(requests, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "change_requests/my_requests.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        },
    )


@login_required
def my_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    change_request = get_object_or_404(ChangeRequest, pk=pk, author=request.user)
    return render(
        request,
        "change_requests/detail.html",
        {
            "change_request": change_request,
            "reviews": change_requests.review_request(change_request).fields,
            "can_withdraw": change_requests.WITHDRAW in change_request.allowed_actions(request.user),
        },
    )


@login_required
@require_POST
def withdraw_request(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    change_request = get_object_or_404(ChangeRequest, pk=pk, author=request.user)
    try:
        change_requests.withdraw(change_request, user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, _("Request withdrawn."))
    return redirect("comics_request_detail", pk)
