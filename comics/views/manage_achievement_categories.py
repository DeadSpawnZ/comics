"""Gestión: achievement categories (name, order, medallion and whether they are shown). A
category is deleted only while it has no achievements."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.achievements.engine import schedule_for_everyone
from comics.achievements.gestion import DeletionRefusedError, category_deletion_blocker, delete_category
from comics.achievements.medals import category_icon_url
from comics.decorators import management_required
from comics.forms import AchievementCategoryForm
from comics.models import AchievementCategory
from comics.views.utils import log_action, safe_next_url, save_with_fresh_code


@management_required
def category_list(request: HttpRequest) -> HttpResponse:
    # Explicit order: Meta.ordering is ignored by aggregated queries.
    categories = list(
        AchievementCategory.objects.annotate(achievement_count=Count("achievements")).order_by("display_order", "code")
    )
    for category in categories:
        category.medallion_url = category_icon_url(category)
    return render(request, "manage/achievement_category_list.html", {"categories": categories})


@management_required
def category_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    category = get_object_or_404(AchievementCategory, pk=pk) if pk else None
    back_url = safe_next_url(request, reverse("manage_achievement_categories"))
    data, files = (request.POST, request.FILES) if request.method == "POST" else (None, None)
    # Validate into a copy: a rejected POST must not change the header's name.
    editing = AchievementCategory.objects.get(pk=category.pk) if category else None
    form = AchievementCategoryForm(data, files, instance=editing)
    if request.method == "POST" and form.is_valid():
        created = category is None

        def save() -> AchievementCategory:
            with transaction.atomic():
                saved = form.save()
                action = ADDITION if created else CHANGE
                log_action(request, saved, action, f"{'Creado' if created else 'Modificado'} desde Gestión.")
                if not created and "is_enabled" in form.changed_data and saved.is_enabled:
                    # Its achievements are awarded again: whoever already qualifies gets them now.
                    schedule_for_everyone(*saved.achievements.values_list("code", flat=True))
            return saved

        saved = save_with_fresh_code(form, save) if created else save()
        message = _("Category created: %(name)s") if created else _("Category saved: %(name)s")
        messages.success(request, message % {"name": saved.name})
        if "save_continue" in request.POST:
            edit_url = reverse("manage_achievement_category_edit", args=[saved.pk])
            return redirect(f"{edit_url}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    return render(
        request,
        "manage/achievement_category_form.html",
        {
            "category": category,
            "form": form,
            "back_url": back_url,
            "medallion_url": category_icon_url(category) if category else None,
            "deletion_blocker": category_deletion_blocker(category) if category else None,
        },
    )


@management_required
@require_POST
def category_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    category = get_object_or_404(AchievementCategory, pk=pk)
    label = category.name
    back_url = safe_next_url(request, reverse("manage_achievement_categories"))
    try:
        with transaction.atomic():
            log_action(request, category, DELETION, "Eliminado desde Gestión.")
            delete_category(category)
    except DeletionRefusedError as refused:
        messages.error(request, _("“%(name)s” cannot be deleted. %(reason)s") % {"name": label, "reason": refused})
        return redirect(f"{reverse('manage_achievement_category_edit', args=[pk])}?{urlencode({'next': back_url})}")
    messages.success(request, _("Category deleted: %(name)s") % {"name": label})
    return redirect(back_url)
