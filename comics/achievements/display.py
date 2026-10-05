"""What a profile shows of the achievements, grouped by category.

The owner sees every enabled achievement: earned ones with their level and date, locked ones with
their progress ("45 / 100"), and secret ones not earned yet as "???" (with the hint, if Gestión
set one). Other users see only what was earned. Disabled achievements, and rows without a rule in
code, are never shown."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.contrib.auth.models import User

from comics.models import Achievement, AchievementCategory, AchievementTier, UserAchievement

from .registry import Kind, Rule, all_rules


@dataclass
class AchievementCard:
    achievement: Achievement
    earned: bool
    level: int  # reached (0 when not earned)
    max_level: int
    awarded_at: datetime | None = None
    tier: AchievementTier | None = None  # reached tier, for its label
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
class AchievementGroup:
    label: str
    total: int
    cards: list[AchievementCard] = field(default_factory=list)

    @property
    def earned(self) -> int:
        return sum(card.earned for card in self.cards)


@dataclass
class Showcase:
    groups: list[AchievementGroup]

    @property
    def earned(self) -> int:
        return sum(group.earned for group in self.groups)

    @property
    def total(self) -> int:
        return sum(group.total for group in self.groups)


def showcase(user: User, *, include_locked: bool) -> Showcase:
    """`user`'s achievements; `include_locked` for the owner's own view (locked ones and progress)."""
    rules = all_rules()
    achievements = list(Achievement.objects.filter(is_enabled=True, code__in=rules).prefetch_related("tiers"))
    held = {award.achievement_id: award for award in UserAchievement.objects.filter(user=user)}
    groups = {value: AchievementGroup(label=str(label), total=0) for value, label in AchievementCategory.choices}
    for achievement in achievements:
        group = groups.setdefault(achievement.category, AchievementGroup(label=achievement.category, total=0))
        group.total += 1
        card = _card(user, achievement, rules[achievement.code], held.get(achievement.pk), include_locked)
        if card is not None:
            group.cards.append(card)
    return Showcase(groups=[group for group in groups.values() if group.cards])


def _card(
    user: User, achievement: Achievement, rule: Rule, award: UserAchievement | None, include_locked: bool
) -> AchievementCard | None:
    tiers = list(achievement.tiers.all())
    max_level = max((tier.level for tier in tiers), default=1)
    if award is None and not include_locked:
        return None
    level = award.level if award else 0
    card = AchievementCard(
        achievement=achievement,
        earned=award is not None,
        level=level,
        max_level=max_level,
        awarded_at=award.awarded_at if award else None,
        tier=next((tier for tier in tiers if tier.level == level), None),
        concealed=award is None and achievement.is_secret,
    )
    if include_locked and not card.concealed and rule.kind is Kind.COUNT and level < max_level:
        upcoming = [tier.threshold for tier in tiers if tier.level > level]
        if upcoming:
            card.goal = min(upcoming)
            card.progress = rule.progress(user)
    return card
