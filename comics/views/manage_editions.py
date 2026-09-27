import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, ProtectedError, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, gettext_lazy, ngettext, pgettext_lazy
from django.views.decorators.http import require_GET, require_POST

from comics.forms import EditionManageForm
from comics.models import CollectedIssue, Edition, Issue
from comics.views.manage import _number_sort_key

PAGE_SIZE = 25
CONTENT_SINGLE = "single"
CONTENT_COMPILATION = "compilation"
TYPE_TABS = [
    ("", pgettext_lazy("editions", "All")),
    ("individual", gettext_lazy("Single")),
    ("compilacion", gettext_lazy("Compilations")),
]


def _log(request, edition, flag, message):
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[edition], action_flag=flag, change_message=message, single_object=True
    )


def _safe_next(request, fallback):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return fallback


def _issue_payload(issue):
    return {"id": issue.id, "label": str(issue), "number": issue.number}


def _own_issue_parts(form, edition):
    """Publishing id and number the edition has in the form (submitted or saved): they define its own issue."""
    if form.is_bound:
        publishing_id, number = form.data.get("publishing"), form.data.get("number", "")
    elif edition:
        publishing_id, number = edition.publishing_id, edition.number
    else:
        return None, ""
    try:
        return int(publishing_id), number.strip()
    except (TypeError, ValueError):
        return None, number.strip()


def _issues_for_publishing(publishing_id):
    issues = Issue.objects.filter(publishing_id=publishing_id).select_related("publishing")
    return sorted(issues, key=_number_sort_key)


@staff_member_required
def edition_list(request):
    return render(request, "manage/edition_list.html", edition_catalog_context(request))


def edition_catalog_context(request):
    """Filtered, paginated edition catalog (type tabs, format, search). Shared by Gestión and
    the read-only Comics module."""
    query = request.GET.get("q", "").strip()
    format_filter = request.GET.get("format", "")
    type_filter = request.GET.get("tipo", "")

    base = Edition.objects.annotate(
        is_compilation_flag=Exists(CollectedIssue.objects.filter(edition=OuterRef("pk")))
    )
    if query:
        base = base.filter(Q(publishing__publishing_title__icontains=query) | Q(number__iexact=query))
    if format_filter:
        base = base.filter(format=format_filter)

    by_type = {
        "": base,
        "individual": base.filter(is_compilation_flag=False),
        "compilacion": base.filter(is_compilation_flag=True),
    }
    tabs = [(value, label, by_type[value].count()) for value, label in TYPE_TABS]
    editions = (
        by_type.get(type_filter, base)
        .select_related("publishing", "issue__publishing")
        .order_by("publishing__publishing_title", "publishing__year", "number", "variant", "printing")
    )

    page_obj = Paginator(editions, PAGE_SIZE).get_page(request.GET.get("page"))
    for edition in page_obj:
        edition.linked_elsewhere = bool(
            edition.issue_id
            and (edition.issue.publishing_id != edition.publishing_id or edition.issue.number != edition.number.strip())
        )
    if page_obj.paginator.count:
        compiled_counts = {}
        for edition_id in CollectedIssue.objects.filter(edition__in=list(page_obj)).values_list("edition_id", flat=True):
            compiled_counts[edition_id] = compiled_counts.get(edition_id, 0) + 1
        for edition in page_obj:
            edition.compiled_count = compiled_counts.get(edition.id, 0)

    return {
        "page_obj": page_obj,
        "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        "tabs": tabs,
        "query": query,
        "format_filter": format_filter,
        "type_filter": type_filter,
        "format_choices": Edition.FormatChoices.choices,
    }


def sibling_editions_of(edition):
    """Other editions of the same issue (empty for compilations)."""
    if not edition.issue_id:
        return []
    return list(
        Edition.objects.filter(issue_id=edition.issue_id)
        .exclude(pk=edition.pk)
        .select_related("publishing")
        .order_by("release_date", "publishing__publishing_title", "number", "variant", "printing")
    )


@staff_member_required
def edition_form(request, pk=None):
    edition = get_object_or_404(Edition.objects.select_related("issue__publishing"), pk=pk) if pk else None
    list_url = reverse("manage_editions")
    back_url = _safe_next(request, list_url)

    if edition and edition.collected_entries.exists():
        content = CONTENT_COMPILATION
    else:
        content = CONTENT_SINGLE
    selected_issue_id = None
    if edition and edition.issue_id and (
        edition.issue.publishing_id != edition.publishing_id or edition.issue.number != edition.number.strip()
    ):
        selected_issue_id = edition.issue_id  # manual link; the own issue is left as the first option
    collected_ids = list(edition.collected_entries.values_list("issue_id", flat=True)) if edition else []

    form = EditionManageForm(request.POST or None, request.FILES or None, instance=edition)
    content_errors = []

    if request.method == "POST":
        content = request.POST.get("content", CONTENT_SINGLE)
        selected_issue_id, collected_ids, content_errors = _clean_content(request.POST, content)
        if form.is_valid() and not content_errors:
            try:
                saved = _save_edition(form, content, selected_issue_id, collected_ids)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                created = edition is None
                label = "Creada" if created else "Modificada"
                _log(request, saved, ADDITION if created else CHANGE, f"{label} desde Gestión.")
                message = _("Edition created: %(edition)s") if created else _("Edition saved: %(edition)s")
                messages.success(request, message % {"edition": saved})
                if "save_continue" in request.POST:
                    return redirect(f"{reverse('manage_edition_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
                return redirect(back_url)

    issue_publishing_id = None
    if selected_issue_id:
        issue_publishing_id = Issue.objects.filter(pk=selected_issue_id).values_list("publishing_id", flat=True).first()
    elif edition:
        issue_publishing_id = edition.publishing_id
    collected = {issue.id: issue for issue in Issue.objects.filter(pk__in=collected_ids).select_related("publishing")}

    # The own issue is offered only as the first option ("issue propio"), never repeated in the list.
    own_publishing_id, own_number = _own_issue_parts(form, edition)
    own_publishing = form.fields["publishing"].queryset.filter(pk=own_publishing_id).first() if own_publishing_id else None
    own_issue = Issue.objects.filter(publishing_id=own_publishing_id, number=own_number).first() if own_publishing else None
    if own_issue and selected_issue_id == own_issue.pk:
        selected_issue_id = None
    issue_options = _issues_for_publishing(issue_publishing_id) if issue_publishing_id else []
    if own_issue:
        issue_options = [issue for issue in issue_options if issue.pk != own_issue.pk]
    if own_publishing and own_number:
        own_label = f"{own_publishing.publishing_title} #{own_number}"
        own_option = (
            _("%(issue)s · own issue") if own_issue else _("%(issue)s · own issue (will be created)")
        ) % {"issue": own_label}
    else:
        own_option = _("Own issue (by publishing and number)")

    sibling_editions = sibling_editions_of(edition) if edition else []

    return render(
        request,
        "manage/edition_form.html",
        {
            "edition": edition,
            "form": form,
            "content": content,
            "content_errors": content_errors,
            "selected_issue_id": selected_issue_id,
            "issue_publishing_id": issue_publishing_id,
            "issue_options": issue_options,
            "sibling_editions": sibling_editions,
            "sibling_groups": Edition.group_by_cover_kind(sibling_editions),
            "own_option": own_option,
            "back_url": back_url,
            "form_data": {
                "issuesUrl": reverse("manage_publishing_issues"),
                "publishingTitles": {
                    publishing.pk: publishing.publishing_title for publishing in form.fields["publishing"].queryset
                },
                "collected": [_issue_payload(collected[i]) for i in collected_ids if i in collected],
            },
        },
    )


def _clean_content(data, content):
    """Validate the content section: single issue (or automatic) or list of collected issues."""
    errors = []
    issue_id = None
    collected_ids = []
    if content == CONTENT_COMPILATION:
        try:
            collected_ids = [int(value) for value in json.loads(data.get("collected") or "[]")]
        except (TypeError, ValueError):
            errors.append(_("The list of collected issues is not valid."))
            collected_ids = []
        if not errors and not collected_ids:
            errors.append(_("A compilation needs at least one collected issue."))
        if len(collected_ids) != len(set(collected_ids)):
            errors.append(_("An issue is repeated in the compilation."))
        if collected_ids and Issue.objects.filter(pk__in=collected_ids).count() != len(set(collected_ids)):
            errors.append(_("One of the collected issues no longer exists."))
    elif content == CONTENT_SINGLE:
        raw = data.get("issue") or ""
        if raw:
            try:
                issue_id = int(raw)
            except ValueError:
                errors.append(_("The chosen issue is not valid."))
            else:
                if not Issue.objects.filter(pk=issue_id).exists():
                    errors.append(_("The chosen issue no longer exists."))
    else:
        errors.append(_("Choose whether the edition is a single issue or a compilation."))
    return issue_id, collected_ids, errors


@transaction.atomic
def _save_edition(form, content, issue_id, collected_ids):
    edition = form.save(commit=False)
    if content == CONTENT_COMPILATION:
        edition.save()
        form.save_m2m()
        edition.collected_entries.all().delete()
        CollectedIssue.objects.bulk_create(
            CollectedIssue(edition=edition, issue_id=issue, order=order)
            for order, issue in enumerate(collected_ids, start=1)
        )
        edition.sync_compilation_state()
    else:
        # Remove the collected issues first: otherwise save() still treats it as a compilation.
        if edition.pk:
            edition.collected_entries.all().delete()
        edition.issue_id = issue_id  # None = own issue (by publishing and number)
        edition.save()
        form.save_m2m()
    return edition


@staff_member_required
@require_POST
def edition_delete(request, pk):
    edition = get_object_or_404(Edition, pk=pk)
    label = str(edition)
    issue_id = edition.issue_id
    back_url = _safe_next(request, reverse("manage_editions"))
    try:
        with transaction.atomic():
            _log(request, edition, DELETION, "Eliminada desde Gestión.")
            edition.delete()
            if issue_id:
                Issue.objects.filter(pk=issue_id).delete_orphans()
    except ProtectedError:
        collections = edition.collection_set.count()
        pieces = edition.connecting_pieces.count()
        uses = []
        if collections:
            uses.append(
                ngettext("%(count)d collection record", "%(count)d collection records", collections)
                % {"count": collections}
            )
        if pieces:
            uses.append(ngettext("%(count)d connecting", "%(count)d connectings", pieces) % {"count": pieces})
        messages.error(
            request,
            _("“%(name)s” cannot be deleted: it is used in %(uses)s.")
            % {"name": label, "uses": (" " + _("and") + " ").join(uses)},
        )
    else:
        messages.success(request, _("Edition deleted: %(edition)s") % {"edition": label})
    return redirect(back_url)


@staff_member_required
@require_GET
def publishing_issues(request):
    try:
        publishing_id = int(request.GET.get("publishing", ""))
    except ValueError:
        return JsonResponse({"results": []})
    return JsonResponse({"results": [_issue_payload(issue) for issue in _issues_for_publishing(publishing_id)]})
