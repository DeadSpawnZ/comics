"""Hunt (Cacería) in the Comics module: the editions the signed-in collector is looking for, marking and
unmarking them, and the note of each mark. The marks are WantedEdition rows; only active ones (not
completed by a purchase) are shown and edited here."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.forms import HuntMarkForm, WantedEditionNoteForm
from comics.models import Edition, WantedEdition
from comics.views.manage_editions import PAGE_SIZE
from comics.views.my_comics import owned_counts
from comics.views.utils import safe_next_url


def note_form_prefix(mark: WantedEdition) -> str:
    """Prefix of a mark's note form: every card of the page has its own form, with its own field ids."""
    return f"note-{mark.pk}"


@login_required
def hunt_list(request: HttpRequest) -> HttpResponse:
    """The user's hunt marks as a cover grid, newest first, flagged with the copies they own (a hunt
    for an owned edition is a replacement, not a first copy), each with its note form for its detail
    dialog. The editorials (the card's flag) come prefetched by `for_collector`."""
    marks = WantedEdition.objects.for_collector(request.user)
    page_obj = Paginator(marks, PAGE_SIZE).get_page(request.GET.get("page"))
    counts = owned_counts(request.user, [mark.edition_id for mark in page_obj])
    for mark in page_obj:
        mark.owned_count = counts.get(mark.edition_id, 0)
        mark.note_form = WantedEditionNoteForm(instance=mark, prefix=note_form_prefix(mark))
    return render(
        request,
        "my_comics/hunt_list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        },
    )


@login_required
@require_POST
def hunt_mark(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark the edition as hunted or unmark it, as the request states (`wanted` "1" or "0"); repeating
    the request changes nothing."""
    edition = get_object_or_404(Edition.objects.select_related("publishing"), pk=pk)
    form = HuntMarkForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    wanted = bool(form.cleaned_data["wanted"])
    WantedEdition.objects.set_wanted(request.user, edition, wanted)
    if wanted:
        messages.success(request, _("%(edition)s is now in your hunt.") % {"edition": edition.short_name})
    else:
        messages.success(request, _("%(edition)s is no longer in your hunt.") % {"edition": edition.short_name})
    return redirect(safe_next_url(request, reverse("comics_edition_detail", args=[pk])))


@login_required
@require_POST
def hunt_note(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Save the note of one of the user's active marks (emptying it removes it). Another user's mark is a
    404, so its existence is not revealed; so is a mark completed by a purchase (it is not on the Hunt
    page, and its note waits unchanged for the hunt to be brought back), also when a purchase completes
    it while the request is handled: only the note is written, and only on a still active mark. An
    invalid note is reported in the snackbar; the browser already enforces the same length limit (see
    forms.NoteField), so it only happens with forged requests."""
    mark = get_object_or_404(WantedEdition.objects.active(), pk=pk, collector=request.user)
    form = WantedEditionNoteForm(request.POST, instance=mark, prefix=note_form_prefix(mark))
    if form.is_valid():
        if not form.save(commit=False).save_note():
            raise Http404
        messages.success(request, _("Note saved.") if mark.notes else _("Note removed."))
    else:
        errors = " ".join(error for field_errors in form.errors.values() for error in field_errors)
        messages.error(request, _("The note was not saved: %(error)s") % {"error": errors})
    return redirect(safe_next_url(request, reverse("comics_hunt")))
