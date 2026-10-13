"""Achievements: their categories, levels (tiers) and the awards users earned."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import (
    CASCADE,
    PROTECT,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    ForeignKey,
    ImageField,
    Index,
    Manager,
    Model,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    QuerySet,
    TimeField,
    UniqueConstraint,
)
from django.utils import timezone

from ..achievements.windows import CalendarWindow, clear_unused, window_errors
from ..helper import square_png
from .base import PictureModel, _random_name, localized


# ---------- Achievements ----------
# What an achievement is called, looks like, when it can be earned and how its levels are set
# lives here (editable in Gestión); how it is earned lives in code (comics/achievements), tied by
# the achievement's `rule`.


class AchievementCategoryManager(Manager["AchievementCategory"]):
    def get_by_natural_key(self, code: str) -> AchievementCategory:
        return self.get(code=code)


class AchievementCategory(PictureModel):
    """A group of achievements, managed in Gestión: its name, order and medallion. A disabled
    category hides (and stops awarding) all its achievements. It cannot be deleted while it has
    achievements (PROTECT)."""

    IMAGE_SIZE = 256  # pixels, square
    PICTURE_FIELD = "image"

    code = CharField(max_length=30, unique=True)  # natural key; generated from the name, never changed
    name_en = CharField(max_length=80)
    name_es = CharField(max_length=80)
    description_en = CharField(max_length=255, blank=True)
    description_es = CharField(max_length=255, blank=True)
    display_order = PositiveSmallIntegerField(default=0)
    is_enabled = BooleanField(default=True)
    image = ImageField(upload_to="achievement_categories/", blank=True)  # else the bundled medallion

    objects = AchievementCategoryManager()

    class Meta:
        ordering = ["display_order", "code"]
        verbose_name_plural = "achievement categories"

    def __str__(self) -> str:
        return self.name or self.code

    def natural_key(self) -> tuple[str]:
        return (self.code,)

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_png(_random_name(f"category_{self.code}"), upload, self.IMAGE_SIZE)

    @property
    def name(self) -> str:
        return localized(self, "name")

    @property
    def description(self) -> str:
        return localized(self, "description")


class AchievementQuerySet(QuerySet["Achievement"]):
    def active(self) -> AchievementQuerySet:
        """Shown and awarded: enabled, in an enabled category."""
        return self.filter(is_enabled=True, category__is_enabled=True)


class AchievementManager(Manager.from_queryset(AchievementQuerySet)):  # type: ignore[misc]
    def get_by_natural_key(self, code: str) -> Achievement:
        return self.get(code=code)


def _day_validators() -> list[Any]:
    return [MinValueValidator(1), MaxValueValidator(31)]


def _month_validators() -> list[Any]:
    return [MinValueValidator(1), MaxValueValidator(12)]


class Achievement(PictureModel):
    """An achievement as users see it. `code` identifies the row (natural key, bundled artwork);
    `rule` names the rule in code that awards it, so several rows can share one rule. Built-in
    rows come from data migrations; Gestión creates rows only for generic rules, which read their
    parameters (the time windows) from the row."""

    ICON_SIZE = 256  # pixels, square
    PICTURE_FIELD = "icon_image"

    code = CharField(max_length=50, unique=True)  # never changed; generated from the name in Gestión
    rule = CharField(max_length=50)  # registry key (comics/achievements/rules.py); fixed once created
    name_en = CharField(max_length=80)
    name_es = CharField(max_length=80)
    description_en = CharField(max_length=255, blank=True)
    description_es = CharField(max_length=255, blank=True)
    # Shown instead of the description while a secret achievement is not earned (optional).
    hint_en = CharField(max_length=255, blank=True)
    hint_es = CharField(max_length=255, blank=True)
    category = ForeignKey(AchievementCategory, on_delete=PROTECT, related_name="achievements")
    # Replaces the bundled artwork (static/achievements/<code>.svg, else the generic medal) when set.
    icon_image = ImageField(upload_to="achievements/", blank=True)
    is_secret = BooleanField(default=False)
    is_enabled = BooleanField(default=True)
    display_order = PositiveSmallIntegerField(default=0)
    # Time windows (comics/achievements/windows.py), read by windowed rules, in the project time zone.
    calendar_window = CharField(max_length=10, choices=CalendarWindow.choices, default=CalendarWindow.NONE)
    window_start_day = PositiveSmallIntegerField(null=True, blank=True, validators=_day_validators())
    window_start_month = PositiveSmallIntegerField(null=True, blank=True, validators=_month_validators())
    window_end_day = PositiveSmallIntegerField(null=True, blank=True, validators=_day_validators())
    window_end_month = PositiveSmallIntegerField(null=True, blank=True, validators=_month_validators())
    window_start_date = DateField(null=True, blank=True)
    window_end_date = DateField(null=True, blank=True)
    window_start_time = TimeField(null=True, blank=True)
    window_end_time = TimeField(null=True, blank=True)

    objects = AchievementManager()

    class Meta:
        # "category" follows the category's own ordering (display_order, code).
        ordering = ["category", "display_order", "code"]

    def __str__(self) -> str:
        return self.name or self.code

    def natural_key(self) -> tuple[str]:
        return (self.code,)

    natural_key.dependencies = ["comics.achievementcategory"]

    def clean(self) -> None:
        super().clean()
        clear_unused(self)
        errors = window_errors(self)
        if errors:
            raise ValidationError(errors)

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_png(_random_name(f"achievement_{self.code}"), upload, self.ICON_SIZE)

    @property
    def name(self) -> str:
        return localized(self, "name")

    @property
    def description(self) -> str:
        return localized(self, "description")

    @property
    def hint(self) -> str:
        return localized(self, "hint")

    @property
    def is_active(self) -> bool:
        return self.is_enabled and self.category.is_enabled


class AchievementTierManager(Manager["AchievementTier"]):
    def get_by_natural_key(self, code: str, level: int) -> AchievementTier:
        return self.get(achievement__code=code, level=level)


class AchievementTier(Model):
    """A level of a count-based achievement: reached when the count gets to `threshold`."""

    achievement = ForeignKey(Achievement, on_delete=CASCADE, related_name="tiers")
    level = PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    threshold = PositiveIntegerField(validators=[MinValueValidator(1)])
    label_en = CharField(max_length=40, blank=True)
    label_es = CharField(max_length=40, blank=True)

    objects = AchievementTierManager()

    class Meta:
        ordering = ["achievement", "level"]
        constraints = [UniqueConstraint(fields=["achievement", "level"], name="unique_achievement_tier_level")]

    def __str__(self) -> str:
        return f"{self.achievement.code} x{self.level} ({self.threshold})"

    def natural_key(self) -> tuple[str, int]:
        return (*self.achievement.natural_key(), self.level)

    natural_key.dependencies = ["comics.achievement"]

    @property
    def label(self) -> str:
        return localized(self, "label")


class UserAchievementManager(Manager["UserAchievement"]):
    def get_by_natural_key(self, username: str, code: str) -> UserAchievement:
        return self.get(user__username=username, achievement__code=code)

    def award(self, user: User, achievement: Achievement, level: int, *, bulk: bool = False) -> bool:
        """Give `user` `level` of `achievement` unless they already have it or a higher one.
        Safe under concurrency: one row per user and achievement (unique constraint, get_or_create)
        and the level only moves up (conditional UPDATE). True when something changed."""
        now = timezone.now()
        values = {"level": level, "level_reached_at": now, "notified": False, "bulk_awarded": bulk}
        award, created = self.get_or_create(user=user, achievement=achievement, defaults={**values, "awarded_at": now})
        if created:
            return True
        return bool(self.filter(pk=award.pk, level__lt=level).update(**values))


class UserAchievement(Model):
    """An achievement a user earned and the highest level reached. Never revoked: losing what
    earned it (e.g. selling comics) does not take it away."""

    user = ForeignKey(User, on_delete=CASCADE, related_name="achievements")
    achievement = ForeignKey(Achievement, on_delete=PROTECT, related_name="awards")
    level = PositiveSmallIntegerField(default=1)
    awarded_at = DateTimeField()  # first earned
    level_reached_at = DateTimeField()  # current level reached
    # Pending "unlocked" notice. Retroactive awards (bulk_awarded) are announced together in one.
    notified = BooleanField(default=False)
    bulk_awarded = BooleanField(default=False)

    objects = UserAchievementManager()

    class Meta:
        constraints = [UniqueConstraint(fields=["user", "achievement"], name="unique_user_achievement")]
        indexes = [Index(fields=["user", "notified"], name="user_achievement_pending")]

    def __str__(self) -> str:
        return f"{self.user} - {self.achievement.code} x{self.level}"

    def natural_key(self) -> tuple[str, str]:
        return (*self.user.natural_key(), *self.achievement.natural_key())

    natural_key.dependencies = ["auth.user", "comics.achievement"]
