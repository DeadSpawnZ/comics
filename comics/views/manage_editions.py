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
from django.views.decorators.http import require_GET, require_POST

from comics.forms import EditionManageForm
from comics.models import CollectedIssue, Edition, Issue
from comics.views.manage import _number_sort_key

PAGE_SIZE = 25
CONTENT_SINGLE = "single"
CONTENT_COMPILATION = "compilation"
TYPE_TABS = [("", "Todas"), ("individual", "Individuales"), ("compilacion", "Compilaciones")]


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
    return {"id": issue.id, "label": str(issue)}


def _issues_for_publishing(publishing_id):
    issues = Issue.objects.filter(publishing_id=publishing_id).select_related("publishing")
    return sorted(issues, key=_number_sort_key)


@staff_member_required
def edition_list(request):
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

    return render(
        request,
        "manage/edition_list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "tabs": tabs,
            "query": query,
            "format_filter": format_filter,
            "type_filter": type_filter,
            "format_choices": Edition.FormatChoices.choices,
        },
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
        selected_issue_id = edition.issue_id  # vinculo manual; si es el automatico se deja en "Automatico"
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
                messages.success(request, f"Edición {'creada' if created else 'guardada'}: {saved}")
                if "save_continue" in request.POST:
                    return redirect(f"{reverse('manage_edition_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
                return redirect(back_url)

    issue_publishing_id = None
    if selected_issue_id:
        issue_publishing_id = Issue.objects.filter(pk=selected_issue_id).values_list("publishing_id", flat=True).first()
    elif edition:
        issue_publishing_id = edition.publishing_id
    collected = {issue.id: issue for issue in Issue.objects.filter(pk__in=collected_ids).select_related("publishing")}

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
            "issue_options": _issues_for_publishing(issue_publishing_id) if issue_publishing_id else [],
            "back_url": back_url,
            "form_data": {
                "issuesUrl": reverse("manage_publishing_issues"),
                "collected": [_issue_payload(collected[i]) for i in collected_ids if i in collected],
            },
        },
    )


def _clean_content(data, content):
    """Valida la seccion de contenido: issue individual (o automatico) o lista de issues recopilados."""
    errors = []
    issue_id = None
    collected_ids = []
    if content == CONTENT_COMPILATION:
        try:
            collected_ids = [int(value) for value in json.loads(data.get("collected") or "[]")]
        except (TypeError, ValueError):
            errors.append("La lista de issues recopilados no es válida.")
            collected_ids = []
        if not errors and not collected_ids:
            errors.append("Una compilación necesita al menos un issue recopilado.")
        if len(collected_ids) != len(set(collected_ids)):
            errors.append("Un issue está repetido en la compilación.")
        if collected_ids and Issue.objects.filter(pk__in=collected_ids).count() != len(set(collected_ids)):
            errors.append("Alguno de los issues recopilados ya no existe.")
    elif content == CONTENT_SINGLE:
        raw = data.get("issue") or ""
        if raw:
            try:
                issue_id = int(raw)
            except ValueError:
                errors.append("El issue elegido no es válido.")
            else:
                if not Issue.objects.filter(pk=issue_id).exists():
                    errors.append("El issue elegido ya no existe.")
    else:
        errors.append("Elige si la edición es un issue individual o una compilación.")
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
        # Primero se quitan los issues recopilados: si no, save() la sigue tratando como compilacion.
        if edition.pk:
            edition.collected_entries.all().delete()
        edition.issue_id = issue_id  # None = automatico segun publishing y numero
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
            uses.append(f"{collections} registro{'s' if collections != 1 else ''} de colección")
        if pieces:
            uses.append(f"{pieces} connecting{'s' if pieces != 1 else ''}")
        messages.error(request, f"No se puede eliminar «{label}»: se usa en {' y '.join(uses)}.")
    else:
        messages.success(request, f"Edición eliminada: {label}")
    return redirect(back_url)


@staff_member_required
@require_GET
def publishing_issues(request):
    try:
        publishing_id = int(request.GET.get("publishing", ""))
    except ValueError:
        return JsonResponse({"results": []})
    return JsonResponse({"results": [_issue_payload(issue) for issue in _issues_for_publishing(publishing_id)]})
