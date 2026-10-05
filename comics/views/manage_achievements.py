"""Gestión: achievements. Everything users see of an achievement is edited here; how it is earned
lives in code (comics/achievements/rules.py), so codes are never created or changed here: rows
come from data migrations, one per rule."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import CHANGE
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from comics.achievements.engine import schedule_for_everyone
from comics.achievements.registry import Kind, all_rules, get_rule
from comics.decorators import management_required
from comics.forms import AchievementForm, AchievementTierFormSet, save_formset_reinserting
from comics.models import Achievement
from comics.views.utils import log_action, safe_next_url


@management_required
def achievement_list(request: HttpRequest) -> HttpResponse:
    rules = all_rules()
    achievements = list(Achievement.objects.annotate(earned_by=Count("awards")).prefetch_related("tiers"))
    for achievement in achievements:
        rule = rules.get(achievement.code)
        achievement.rule = rule
        # A count-based rule without levels can never be earned.
        achievement.missing_tiers = rule is not None and rule.kind is Kind.COUNT and not achievement.tiers.all()
    return render(
        request,
        "manage/achievement_list.html",
        {
            "achievements": achievements,
            "rules_without_row": sorted(set(rules) - {achievement.code for achievement in achievements}),
        },
    )


@management_required
def achievement_form(request: HttpRequest, pk: int) -> HttpResponse:
    achievement = get_object_or_404(Achievement, pk=pk)
    rule = get_rule(achievement.code)
    back_url = safe_next_url(request, reverse("manage_achievements"))
    data, files = (request.POST, request.FILES) if request.method == "POST" else (None, None)
    # The forms validate into their own copy: a rejected POST must not change the header's name.
    editing = Achievement.objects.get(pk=achievement.pk)
    form = AchievementForm(data, files, instance=editing)
    tiers = AchievementTierFormSet(
        data, instance=editing, prefix="tiers", counted=rule is not None and rule.kind is Kind.COUNT
    )
    if request.method == "POST" and form.is_valid() and tiers.is_valid():
        # Users may qualify now: after enabling it or changing its levels, evaluate everyone.
        reevaluate = rule is not None and ("is_enabled" in form.changed_data or tiers.has_changed())
        with transaction.atomic():
            saved = form.save()
            save_formset_reinserting(tiers)
            log_action(request, saved, CHANGE, "Modificado desde Gestión.")
            if reevaluate and saved.is_enabled:
                schedule_for_everyone(saved.code)
        messages.success(request, _("Achievement saved: %(name)s") % {"name": saved.name})
        if "save_continue" in request.POST:
            return redirect(f"{reverse('manage_achievement_edit', args=[saved.pk])}?{urlencode({'next': back_url})}")
        return redirect(back_url)
    return render(
        request,
        "manage/achievement_form.html",
        {"achievement": achievement, "rule": rule, "form": form, "tiers": tiers, "back_url": back_url},
    )
