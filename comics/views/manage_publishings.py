from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Model, ProtectedError, Q
from django.forms import BaseForm, ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.decorators import management_required
from comics.forms import (
    EditorialDialogForm,
    PublishingDialogForm,
    PublishingManageForm,
    TitleDialogForm,
    publishing_label,
)
from comics.models import Publishing, Title
from comics.views.manage_editions import PAGE_SIZE, RECENT_COUNT
from comics.views.utils import log_action, safe_next_url


@management_required
def publishing_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    language = request.GET.get("language", "")

    all_publishings = Publishing.objects.annotate(edition_count=Count("edition", distinct=True)).prefetch_related(
        "editorials"
    )
    publishings = all_publishings
    if query:
        publishings = publishings.filter(Q(publishing_title__icontains=query) | Q(title__name__icontains=query))
    if language:
        publishings = publishings.filter(language=language)
    publishings = publishings.order_by("publishing_title", "year", "serie")

    page_obj = Paginator(publishings, PAGE_SIZE).get_page(request.GET.get("page"))
    # The latest additions go first (only on the unfiltered first page), then the A-Z list.
    recent = []
    if page_obj.number == 1 and not (query or language):
        recent = list(all_publishings.order_by("-id")[:RECENT_COUNT])
    return render(
        request,
        "manage/publishing_list.html",
        {
            "recent": recent,
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "query": query,
            "language": language,
            "language_choices": Publishing.LangAbbr.choices,
        },
    )


@management_required
def publishing_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    publishing = get_object_or_404(Publishing, pk=pk) if pk else None
    back_url = safe_next_url(request, reverse("manage_publishings"))
    form = PublishingManageForm(request.POST or None, instance=publishing)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                saved = form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            created = publishing is None
            log_action(
                request,
                saved,
                ADDITION if created else CHANGE,
                f"{'Creado' if created else 'Modificado'} desde Gestión.",
            )
            message = _("Publishing created: %(publishing)s") if created else _("Publishing saved: %(publishing)s")
            messages.success(request, message % {"publishing": publishing_label(saved)})
            if "save_add_edition" in request.POST:
                query = urlencode({"publishing": saved.pk, "next": reverse("manage_editions")})
                return redirect(f"{reverse('manage_edition_new')}?{query}")
            if "save_continue" in request.POST:
                return redirect(f"{reverse('manage_publishing_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
            return redirect(back_url)

    return render(
        request,
        "manage/publishing_form.html",
        {
            "publishing": publishing,
            "form": form,
            "back_url": back_url,
            "title_options": Title.objects.order_by("name").values_list("name", flat=True),
            "edition_count": publishing.edition_set.count() if publishing else 0,
        },
    )


@management_required
@require_POST
def publishing_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    publishing = get_object_or_404(Publishing, pk=pk)
    label = publishing_label(publishing)
    back_url = safe_next_url(request, reverse("manage_publishings"))
    try:
        with transaction.atomic():
            log_action(request, publishing, DELETION, "Eliminado desde Gestión.")
            publishing.delete()
    except ProtectedError:
        messages.error(
            request,
            _("“%(name)s” cannot be deleted: it has editions or issues. Delete or move them first.") % {"name": label},
        )
    else:
        messages.success(request, _("Publishing deleted: %(publishing)s") % {"publishing": label})
    return redirect(back_url)


# ---------- "Create" dialogs of the edition form (JSON APIs) ----------
#
# Each one validates with its form (the same rules as the Gestión pages and the models), creates the
# row and answers {"id", "label"} so the page can add it to its selects; or 400 with
# {"errors": {field: [messages]}, "non_field_errors": [messages]}, the field names without the
# form's prefix. POST only, with the CSRF token like any form.


def _invalid(form: BaseForm) -> JsonResponse:
    errors = {name: [str(message) for message in messages] for name, messages in form.errors.items()}
    non_field = errors.pop(NON_FIELD_ERRORS, [])
    return JsonResponse({"errors": errors, "non_field_errors": non_field}, status=400)


def _create(request: HttpRequest, form: ModelForm, log_message: str, duplicate: str) -> Model | None:
    """Save `form` and log it, atomically; None (with the error on the form) when the database or a
    model rule refuses it, e.g. another request created the same row in the meantime."""
    try:
        with transaction.atomic():
            created = form.save()
            log_action(request, created, ADDITION, log_message)
    except ValidationError as exc:  # Publishing.save() checks duplicates too
        form.add_error(None, exc)
        return None
    except IntegrityError:
        form.add_error(None, duplicate)
        return None
    return created


@management_required
@require_POST
def title_create_api(request: HttpRequest) -> JsonResponse:
    form = TitleDialogForm(request.POST)
    duplicate = _("A title with that name already exists.")
    title = _create(request, form, "Creado desde Gestión.", duplicate) if form.is_valid() else None
    if title is None:
        return _invalid(form)
    return JsonResponse({"id": title.pk, "label": title.name})


@management_required
@require_POST
def editorial_create_api(request: HttpRequest) -> JsonResponse:
    form = EditorialDialogForm(request.POST)
    duplicate = _("An editorial with that name already exists.")
    editorial = _create(request, form, "Creada desde Gestión.", duplicate) if form.is_valid() else None
    if editorial is None:
        return _invalid(form)
    return JsonResponse({"id": editorial.pk, "label": editorial.name})


@management_required
@require_POST
def publishing_create_api(request: HttpRequest) -> JsonResponse:
    """Also answers the publishing title, which the edition form uses to name the edition's own issue."""
    form = PublishingDialogForm(request.POST)
    duplicate = _("A publishing with these values already exists.")
    publishing = _create(request, form, "Creado desde Gestión.", duplicate) if form.is_valid() else None
    if publishing is None:
        return _invalid(form)
    return JsonResponse(
        {"id": publishing.pk, "label": publishing.select_label, "publishing_title": publishing.publishing_title}
    )
