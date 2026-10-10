"""Gestión: achievements. Everything users see of an achievement is edited here; how it is earned
lives in code (comics/achievements/rules.py). Built-in achievements come from data migrations,
one per built-in rule; Gestión creates (and deletes, while nobody earned them) achievements with
generic rules, which take their parameters (time windows) from the row."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db import transaction
from django.db.models import Count
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from comics.achievements.catalogue import BUILTIN_CODES
from comics.achievements.engine import schedule_for_everyone
from comics.achievements.gestion import DeletionRefusedError, achievement_deletion_blocker, delete_achievement
from comics.achievements.medals import icon_source, preview_medals, top_medal
from comics.achievements.registry import Rule, all_rules, builtin_rules, generic_rules, get_rule
from comics.achievements.windows import has_window
from comics.decorators import management_required
from comics.forms import (
    WINDOW_FIELDS,
    AchievementCreateForm,
    AchievementForm,
    AchievementTierFormSet,
    save_formset_reinserting,
)
from comics.models import Achievement
from comics.views.utils import log_action, safe_next_url, save_with_fresh_code


@management_required
def achievement_list(request: HttpRequest) -> HttpResponse:
    rules = all_rules()
    achievements = list(
        Achievement.objects.select_related("category")
        .annotate(earned_by=Count("awards"))
        .prefetch_related("tiers")
        # Explicit: Meta.ordering is ignored by aggregated queries. Grouped by category, in order.
        .order_by("category__display_order", "category__code", "display_order", "code")
    )
    for achievement in achievements:
        rule = rules.get(achievement.rule)
        achievement.rule_info = rule
        # A count-based rule without levels can never be earned.
        achievement.missing_tiers = rule is not None and rule.counted and not achievement.tiers.all()
        # A windowed rule without a window never awards anything either (only rows made by hand).
        achievement.missing_window = rule is not None and rule.needs_window and not has_window(achievement)
        achievement.is_builtin = achievement.code in BUILTIN_CODES
        achievement.medal = top_medal(achievement)  # uses the prefetched tiers
    return render(
        request,
        "manage/achievement_list.html",
        {
            "achievements": achievements,
            "rules_without_row": sorted(set(builtin_rules()) - {achievement.rule for achievement in achievements}),
            "can_create": bool(generic_rules()),
        },
    )


@management_required
def achievement_form(request: HttpRequest, pk: int) -> HttpResponse:
    achievement = get_object_or_404(Achievement.objects.select_related("category"), pk=pk)
    rule = get_rule(achievement.rule)
    back_url = safe_next_url(request, reverse("manage_achievements"))
    data, files = (request.POST, request.FILES) if request.method == "POST" else (None, None)
    # The forms validate into their own copy: a rejected POST must not change the header's name.
    editing = Achievement.objects.get(pk=achievement.pk)
    form = AchievementForm(data, files, instance=editing, rule=rule)
    tiers = AchievementTierFormSet(data, instance=editing, prefix="tiers", counted=rule is not None and rule.counted)
    if request.method == "POST" and form.is_valid() and tiers.is_valid():
        # Users may qualify now: after it becomes active (enabled, or moved into an enabled
        # category) or its levels or windows change, evaluate everyone. Waiting for new activity is
        # not enough: login_in_window only sees the last sign-in, which the next one overwrites.
        was_active = achievement.is_active
        changed = set(form.changed_data)
        with transaction.atomic():
            saved = form.save()
            save_formset_reinserting(tiers)
            log_action(request, saved, CHANGE, "Modificado desde Gestión.")
            became_active = saved.is_active and not was_active
            reevaluate = bool(changed & {"is_enabled", *WINDOW_FIELDS}) or tiers.has_changed() or became_active
            if rule is not None and reevaluate and saved.is_active:
                schedule_for_everyone(saved.code)
        messages.success(request, _("Achievement saved: %(name)s") % {"name": saved.name})
        if "save_continue" in request.POST:
            return redirect(f"{reverse('manage_achievement_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    return render(
        request,
        "manage/achievement_form.html",
        {
            **_form_context(achievement, rule, form, tiers, back_url),
            "deletion_blocker": achievement_deletion_blocker(achievement),
        },
    )


@management_required
def achievement_create(request: HttpRequest) -> HttpResponse:
    """A new achievement with a generic rule. The rule picked decides whether it takes levels and
    windows, so a POST builds the forms for the posted rule."""
    generic = generic_rules()
    if not generic:
        return redirect("manage_achievements")
    back_url = safe_next_url(request, reverse("manage_achievements"))
    posted = request.POST.get("rule") if request.method == "POST" else None
    rule = generic.get(posted or "") or generic[sorted(generic)[0]]
    data, files = (request.POST, request.FILES) if request.method == "POST" else (None, None)
    new = Achievement(rule=rule.key)
    form = AchievementCreateForm(data, files, instance=new, rule=rule, initial={"rule": rule.key})
    tiers = AchievementTierFormSet(data, instance=new, prefix="tiers", counted=rule.counted)
    if request.method == "POST" and form.is_valid() and tiers.is_valid():

        def create() -> Achievement:
            with transaction.atomic():
                saved = form.save()
                tiers.instance = saved
                save_formset_reinserting(tiers)
                log_action(request, saved, ADDITION, "Creado desde Gestión.")
                if saved.is_active:
                    schedule_for_everyone(saved.code)
            return saved

        saved = save_with_fresh_code(form, create)
        messages.success(request, _("Achievement created: %(name)s") % {"name": saved.name})
        if "save_continue" in request.POST:
            return redirect(f"{reverse('manage_achievement_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    return render(request, "manage/achievement_form.html", _form_context(None, rule, form, tiers, back_url))


@management_required
@require_POST
def achievement_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    achievement = get_object_or_404(Achievement, pk=pk)
    label = achievement.name
    back_url = safe_next_url(request, reverse("manage_achievements"))
    try:
        with transaction.atomic():
            log_action(request, achievement, DELETION, "Eliminado desde Gestión.")
            delete_achievement(achievement)
    except DeletionRefusedError as refused:
        messages.error(request, _("“%(name)s” cannot be deleted. %(reason)s") % {"name": label, "reason": refused})
        return redirect(f"{reverse('manage_achievement_edit', args=[pk])}?{urlencode({'next': back_url})}")
    messages.success(request, _("Achievement deleted: %(name)s") % {"name": label})
    return redirect(back_url)


def _form_context(
    achievement: Achievement | None,
    rule: Rule | None,
    form: AchievementForm,
    tiers: BaseInlineFormSet,
    back_url: str,
) -> dict[str, object]:
    context: dict[str, object] = {
        "achievement": achievement,
        "rule": rule,
        "form": form,
        "tiers": tiers,
        "back_url": back_url,
    }
    if achievement is not None:
        # The saved medal at each level, with the picture it uses now.
        context["medals"] = preview_medals(achievement)
        context["icon_source"] = icon_source(achievement)
    return context
