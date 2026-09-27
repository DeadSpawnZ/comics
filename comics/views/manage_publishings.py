from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.forms import PublishingManageForm, publishing_label
from comics.models import Publishing, Title
from comics.views.manage_editions import PAGE_SIZE, _log, _safe_next


@staff_member_required
def publishing_list(request):
    query = request.GET.get("q", "").strip()
    language = request.GET.get("language", "")

    publishings = Publishing.objects.annotate(edition_count=Count("edition", distinct=True)).prefetch_related("editorials")
    if query:
        publishings = publishings.filter(Q(publishing_title__icontains=query) | Q(title__name__icontains=query))
    if language:
        publishings = publishings.filter(language=language)
    # Newest first: the ones just created are the ones being filled with editions.
    publishings = publishings.order_by("-id")

    page_obj = Paginator(publishings, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "manage/publishing_list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "query": query,
            "language": language,
            "language_choices": Publishing.LangAbbr.choices,
        },
    )


@staff_member_required
def publishing_form(request, pk=None):
    publishing = get_object_or_404(Publishing, pk=pk) if pk else None
    back_url = _safe_next(request, reverse("manage_publishings"))
    form = PublishingManageForm(request.POST or None, instance=publishing)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                saved = form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            created = publishing is None
            _log(request, saved, ADDITION if created else CHANGE, f"{'Creado' if created else 'Modificado'} desde Gestión.")
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


@staff_member_required
@require_POST
def publishing_delete(request, pk):
    publishing = get_object_or_404(Publishing, pk=pk)
    label = publishing_label(publishing)
    back_url = _safe_next(request, reverse("manage_publishings"))
    try:
        with transaction.atomic():
            _log(request, publishing, DELETION, "Eliminado desde Gestión.")
            publishing.delete()
    except ProtectedError:
        messages.error(
            request,
            _("“%(name)s” cannot be deleted: it has editions or issues. Delete or move them first.") % {"name": label},
        )
    else:
        messages.success(request, _("Publishing deleted: %(publishing)s") % {"publishing": label})
    return redirect(back_url)
