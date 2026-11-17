"""What a profile shows of the achievements: one medallion per category with how many of its
challenges ("retos") were completed, and, inside, one row per achievement level.

Each level of an achievement is a challenge: a tiered achievement with three levels is three
challenges (base, silver, gold), one without levels is one. The owner sees every challenge:
earned ones with their date, the rest greyed out with the current progress, and the levels of
secret achievements not earned yet as "???" (with the hint, if Gestión set one). Other users see
the same category counts but only the challenges that were earned. Inactive achievements
(disabled, or in a disabled category) and rows whose rule is not in code are never shown.

Cost: three queries (achievements with their categories, their levels, the user's awards) plus,
on the owner's view, one progress measurement per count achievement with levels still to reach,
whatever the number of categories and levels."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.contrib.auth.models import User

from comics.models import Achievement, AchievementCategory, AchievementTier, UserAchievement

from .medals import Medal, category_icon_url, medal_for, rim_for
from .registry import Rule, all_rules


@dataclass
class Reto:
    """One level of an achievement, as a challenge."""

    achievement: Achievement
    level: int
    tiered: bool  # the achievement has several levels: the level name is shown
    level_name: str  # the tier's label from Gestión, else the ring's name (Base, Silver, Gold)
    requirement: str  # what reaching this level takes
    earned: bool
    medal: Medal
    concealed: bool = False  # a level of a secret achievement not earned yet: "???"
    earned_on: datetime | None = None  # only when it is known exactly (first and current level)
    progress: int | None = None  # towards `goal`: owner's view, unearned levels of count rules
    goal: int | None = None


@dataclass
class CategoryShelf:
    category: AchievementCategory
    medallion_url: str
    retos: list[Reto] = field(default_factory=list)  # the ones this viewer may see
    earned: int = 0
    total: int = 0  # every challenge of the category, also the ones this viewer does not see

    @property
    def percent(self) -> int:
        return round(100 * self.earned / self.total) if self.total else 0

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.earned == self.total


@dataclass
class Showcase:
    categories: list[CategoryShelf]

    @property
    def earned(self) -> int:
        return sum(shelf.earned for shelf in self.categories)

    @property
    def total(self) -> int:
        return sum(shelf.total for shelf in self.categories)


def showcase(user: User, *, include_locked: bool) -> Showcase:
    """`user`'s challenges by category; `include_locked` for the owner's own view (unearned
    challenges, progress and secret placeholders). Categories and achievements keep their order."""
    rules = all_rules()
    achievements = Achievement.objects.active().filter(rule__in=rules).select_related("category")
    held = {award.achievement_id: award for award in UserAchievement.objects.filter(user=user)}
    shelves: dict[int, CategoryShelf] = {}
    for achievement in achievements.prefetch_related("tiers"):
        shelf = shelves.get(achievement.category_id)
        if shelf is None:
            shelf = CategoryShelf(category=achievement.category, medallion_url=category_icon_url(achievement.category))
            shelves[achievement.category_id] = shelf
        retos = _retos(user, achievement, rules[achievement.rule], held.get(achievement.pk), include_locked)
        shelf.total += len(retos)
        shelf.earned += sum(reto.earned for reto in retos)
        shelf.retos.extend(reto for reto in retos if reto.earned or include_locked)
    return Showcase(categories=list(shelves.values()))


def _retos(
    user: User, achievement: Achievement, rule: Rule, award: UserAchievement | None, include_locked: bool
) -> list[Reto]:
    """Every level of `achievement` as a challenge (built for all; the caller filters)."""
    tiers: list[AchievementTier | None] = sorted(achievement.tiers.all(), key=lambda tier: tier.level)
    if not (rule.counted and tiers):
        tiers = [None]  # one challenge: earned or not
    held_level = award.level if award else 0
    tiered = len(tiers) > 1
    first_level = tiers[0].level if tiers[0] else 1
    reachable = any(tier and tier.level > held_level for tier in tiers)
    current = None
    if include_locked and rule.counted and reachable and not (achievement.is_secret and award is None):
        current = rule.progress_for(user, achievement)  # once per achievement
    retos = []
    for tier in tiers:
        level = tier.level if tier else 1
        earned = level <= held_level
        concealed = achievement.is_secret and not earned
        earned_on = None
        if earned and award is not None:
            if level == held_level:
                earned_on = award.level_reached_at
            elif level == first_level:
                earned_on = award.awarded_at  # the first award included the first level
        retos.append(
            Reto(
                achievement=achievement,
                level=level,
                tiered=tiered,
                level_name=(tier.label or rim_for(level).label) if tier and tiered else "",
                requirement=rule.requirement_for(tier.threshold) if tier else achievement.description,
                earned=earned,
                medal=medal_for(achievement, level, concealed=concealed, locked=not earned),
                concealed=concealed,
                earned_on=earned_on,
                progress=current if tier and not earned and not concealed else None,
                goal=tier.threshold if tier and not earned and not concealed and current is not None else None,
            )
        )
    return retos
