from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.forms import ReadingArcForm, publishing_choices
from comics.models import Issue, ReadingArc, ReadingArcEntry
from comics.views.manage_editions import _log, _safe_next

COVER_STRIP_SIZE = 8


def _entry_payload(entry: ReadingArcEntry) -> dict[str, Any]:
    return {"id": entry.issue_id, "label": str(entry.issue), "cover": entry.cover_url}


@staff_member_required
def arc_list(request: HttpRequest) -> HttpResponse:
    cards = []
    for arc in ReadingArc.objects.all():
        entries = arc.entries_with_covers()
        cards.append(
            {
                "arc": arc,
                "total": len(entries),
                "covers": [entry for entry in entries if entry.cover_url][:COVER_STRIP_SIZE],
            }
        )
    return render(request, "manage/arc_list.html", {"cards": cards})


@staff_member_required
def arc_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    arc = get_object_or_404(ReadingArc, pk=pk) if pk else None
    back_url = _safe_next(request, reverse("manage_arcs"))
    form = ReadingArcForm(request.POST or None, instance=arc)
    issue_errors = []

    if request.method == "POST":
        try:
            issue_ids = json.loads(request.POST.get("issues") or "[]")
            if not isinstance(issue_ids, list):
                raise ValueError
        except ValueError:
            issue_ids = []
            issue_errors.append(_("The list of issues is not valid."))

        if form.is_valid() and not issue_errors:
            saved = form.save(commit=False)
            try:
                cleaned_ids = saved.clean_issue_ids(issue_ids)
            except ValidationError as exc:
                issue_errors.extend(exc.messages)
            else:
                created = arc is None
                with transaction.atomic():
                    saved.save()
                    saved.set_issues(cleaned_ids)
                    _log(
                        request,
                        saved,
                        ADDITION if created else CHANGE,
                        f"{'Creado' if created else 'Modificado'} desde Gestión.",
                    )
                message = _("Reading arc created: %(arc)s") if created else _("Reading arc saved: %(arc)s")
                messages.success(request, message % {"arc": saved})
                if "save_continue" in request.POST:
                    return redirect(f"{reverse('manage_arc_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
                return redirect(back_url)

        # Keep what the user submitted when the form is shown again with errors. Only real ids are
        # kept: the list comes from the client and may hold anything (strings, lists, objects).
        submitted_ids = [i for i in issue_ids if isinstance(i, int) and not isinstance(i, bool)]
        submitted = {issue.pk: issue for issue in Issue.objects.filter(pk__in=submitted_ids)}
        items = [{"id": pk, "label": str(submitted[pk])} for pk in submitted_ids if pk in submitted]
    else:
        items = [_entry_payload(entry) for entry in arc.entries_with_covers()] if arc else []

    return render(
        request,
        "manage/arc_form.html",
        {
            "arc": arc,
            "form": form,
            "issue_errors": issue_errors,
            "back_url": back_url,
            "publishings": publishing_choices(),
            "form_data": {"issuesUrl": reverse("manage_publishing_issues"), "issues": items},
        },
    )


@staff_member_required
@require_POST
def arc_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    arc = get_object_or_404(ReadingArc, pk=pk)
    label = str(arc)
    with transaction.atomic():
        _log(request, arc, DELETION, "Eliminado desde Gestión.")
        arc.delete()
    messages.success(request, _("Reading arc deleted: %(arc)s") % {"arc": label})
    return redirect(_safe_next(request, reverse("manage_arcs")))
