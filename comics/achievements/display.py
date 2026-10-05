"""What a profile shows of the achievements: a row of earned medals and, on the owner's own view,
a row of the ones not earned yet.

The owner sees every enabled achievement: earned ones with their level and date, locked ones with
their progress ("45 / 100"), and secret ones not earned yet as "???" (with the hint, if Gestión
set one). Other users see only what was earned. Disabled achievements, and rows without a rule in
code, are never shown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth.models import User
from django.utils.functional import Promise

from comics.models import Achievement, AchievementTier, UserAchievement

from .medals import Medal, medal_for
from .registry import Kind, Rule, all_rules


@dataclass
class AchievementCard:
    achievement: Achievement
    earned: bool
    level: int  # reached (0 when not earned)
    max_level: int
    medal: Medal
    awarded_at: datetime | None = None
    tier: AchievementTier | None = None  # reached tier, for its threshold and label
    next_threshold: int | None = None  # of the next level, when there is one
    measure: str | Promise = ""  # what the rule counts, for the level line
    concealed: bool = False  # secret and not earned: shown as "???"
    progress: int | None = None  # towards `goal`, only on the owner's view
    goal: int | None = None

    @property
    def tiered(self) -> bool:
        return self.max_level > 1

    @property
    def percent(self) -> int:
        if not self.goal:
            return 0
        return min(100, round(100 * (self.progress or 0) / self.goal))


@dataclass
class Showcase:
    earned: list[AchievementCard]
    locked: list[AchievementCard]  # empty unless the owner's own view
    total: int  # enabled achievements with a rule

    @property
    def earned_count(self) -> int:
        return len(self.earned)


def showcase(user: User, *, include_locked: bool) -> Showcase:
    """`user`'s achievements; `include_locked` for the owner's own view (locked ones and progress).
    Earned medals come highest level first; locked ones with progress before hidden secrets."""
    rules = all_rules()
    achievements = list(Achievement.objects.filter(is_enabled=True, code__in=rules).prefetch_related("tiers"))
    held = {award.achievement_id: award for award in UserAchievement.objects.filter(user=user)}
    earned: list[AchievementCard] = []
    locked: list[AchievementCard] = []
    for achievement in achievements:
        card = _card(user, achievement, rules[achievement.code], held.get(achievement.pk), include_locked)
        if card is not None:
            (earned if card.earned else locked).append(card)
    # Stable sorts: ties keep the catalogue order (category, display_order, code).
    earned.sort(key=lambda card: (-card.level, not card.tiered))
    locked.sort(key=lambda card: card.concealed)
    return Showcase(earned=earned, locked=locked, total=len(achievements))


def _card(
    user: User, achievement: Achievement, rule: Rule, award: UserAchievement | None, include_locked: bool
) -> AchievementCard | None:
    if award is None and not include_locked:
        return None
    tiers = list(achievement.tiers.all())
    max_level = max((tier.level for tier in tiers), default=1)
    level = award.level if award else 0
    concealed = award is None and achievement.is_secret
    upcoming = [tier.threshold for tier in tiers if tier.level > level]
    next_threshold = None if concealed or not upcoming else min(upcoming)
    progress = goal = None
    if include_locked and not concealed and rule.kind is Kind.COUNT and next_threshold is not None:
        goal, progress = next_threshold, rule.progress(user)
    return AchievementCard(
        achievement=achievement,
        earned=award is not None,
        level=level,
        max_level=max_level,
        medal=medal_for(achievement, level, concealed=concealed, have=progress, need=goal),
        awarded_at=award.awarded_at if award else None,
        tier=next((tier for tier in tiers if tier.level == level), None),
        next_threshold=next_threshold,
        measure=rule.measure,
        concealed=concealed,
        progress=progress,
        goal=goal,
    )
