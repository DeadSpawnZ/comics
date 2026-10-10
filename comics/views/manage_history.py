"""Gestión: the versions of an edition or an issue (comics.history) and going back to one of them."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import CHANGE
from django.core.exceptions import ValidationError
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from comics.decorators import management_required
from comics.forms import RevertForm
from comics.history import history_model
from comics.models import Edition, Issue
from comics.versions import object_label, revert_to_version, version_entries
from comics.views.utils import log_action, safe_next_url

# Per tracked model: the URL names of its history page and of the revert action.
HISTORY_URLS = {
    Edition: ("manage_edition_history", "manage_edition_revert"),
    Issue: ("manage_issue_history", "manage_issue_revert"),
}


def _back_fallback(model: type[Model], pk: int) -> str:
    """Where the history page goes back to without `next`: the edition's Gestión page (if it still
    exists), else the edition list (issues have no Gestión page of their own)."""
    if model is Edition and Edition.objects.filter(pk=pk).exists():
        return reverse("manage_edition_edit", args=[pk])
    return reverse("manage_editions")


def _history_page(request: HttpRequest, model: type[Model], pk: int) -> HttpResponse:
    entries = version_entries(model, pk)
    if not entries:
        raise Http404
    back_url = safe_next_url(request, _back_fallback(model, pk))
    _history_url_name, revert_url_name = HISTORY_URLS[model]
    return render(
        request,
        "manage/version_history.html",
        {
            "is_edition": model is Edition,
            "object_pk": pk,
            "object_label": object_label(model, pk),
            "entries": entries,
            "back_url": back_url,
            "revert_url_name": revert_url_name,
            "revert_form": RevertForm(),
        },
    )


def _revert(request: HttpRequest, model: type[Model], pk: int, version_id: int) -> HttpResponseRedirect:
    version = get_object_or_404(history_model(model), id=pk, history_id=version_id)
    history_url_name, _revert_url_name = HISTORY_URLS[model]
    back_url = safe_next_url(request, _back_fallback(model, pk))
    form = RevertForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    else:
        try:
            revert_to_version(version, user=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            when = formats.date_format(timezone.localtime(version.history_date), "DATETIME_FORMAT")
            restored = model._default_manager.get(pk=pk)
            log_action(request, restored, CHANGE, f"Restaurada la versión del {when} desde Gestión.")
            messages.success(request, _("Restored the version of %(date)s.") % {"date": when})
    return redirect(f"{reverse(history_url_name, args=[pk])}?{urlencode({'next': back_url})}")


@management_required
@require_GET
def edition_history(request: HttpRequest, pk: int) -> HttpResponse:
    return _history_page(request, Edition, pk)


@management_required
@require_POST
def edition_revert(request: HttpRequest, pk: int, version_id: int) -> HttpResponseRedirect:
    return _revert(request, Edition, pk, version_id)


@management_required
@require_GET
def issue_history(request: HttpRequest, pk: int) -> HttpResponse:
    return _history_page(request, Issue, pk)


@management_required
@require_POST
def issue_revert(request: HttpRequest, pk: int, version_id: int) -> HttpResponseRedirect:
    return _revert(request, Issue, pk, version_id)
