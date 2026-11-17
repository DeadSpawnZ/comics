"""How an achievement medal looks: its picture and the ring of the level reached.

The picture comes, in this order, from the image uploaded in Gestión, the artwork bundled for the
achievement's code (static/achievements/<code>.svg) or the generic medal (_generic.svg), so there
is always a picture. A secret achievement not earned yet always shows the secret placeholder,
never its real picture. The ring
is drawn by the page (CSS) around the picture: neutral for level 1 and achievements without
levels, silver for level 2 and gold from level 3 up."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

from django.contrib.staticfiles import finders
from django.db.models import TextChoices
from django.templatetags.static import static
from django.utils.translation import gettext_lazy as _

from comics.models import Achievement, AchievementCategory

STATIC_DIR = "achievements"
SECRET_ICON = f"{STATIC_DIR}/_secret.svg"
CATEGORY_ICON = f"{STATIC_DIR}/_category.svg"  # generic category medallion
GENERIC_ICON = f"{STATIC_DIR}/_generic.svg"  # achievements without their own artwork yet
# Codes are seeded by migrations, but only well-formed ones are turned into a static path (no
# separators, no leading "_": "_secret" is the placeholder, not an achievement).
_CODE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class Rim(TextChoices):
    """The ring around a medal. Templates use the value as a CSS modifier and the label in texts."""

    BASE = "base", _("Base")
    SILVER = "silver", _("Silver")
    GOLD = "gold", _("Gold")
    SECRET = "secret", _("Secret")  # the grey ring of a secret achievement not earned yet


class IconSource(StrEnum):
    UPLOAD = "upload"
    BUNDLED = "bundled"
    GENERIC = "generic"


def rim_for(level: int) -> Rim:
    """Ring of a level: 0 (not earned) and 1 share the neutral one."""
    if level >= 3:
        return Rim.GOLD
    if level == 2:
        return Rim.SILVER
    return Rim.BASE


@cache
def bundled_icon(code: str) -> str | None:
    """Static path of the artwork bundled for `code`, or None. Looked up through the staticfiles
    finders (the source tree, so it does not depend on collectstatic having run) and cached: the
    bundled files only change with a deploy."""
    if not _CODE.match(code):
        return None
    path = f"{STATIC_DIR}/{code}.svg"
    return path if finders.find(path) else None


def icon_source(achievement: Achievement) -> IconSource:
    if achievement.icon_image:
        return IconSource.UPLOAD
    if bundled_icon(achievement.code):
        return IconSource.BUNDLED
    return IconSource.GENERIC


def icon_url(achievement: Achievement, *, concealed: bool = False) -> str:
    """URL of the medal's picture (never missing: the generic medal is the last resort)."""
    if concealed:
        return static(SECRET_ICON)
    if achievement.icon_image:
        return achievement.icon_image.url
    # static() goes through the configured storage: hashed names with the manifest storage.
    return static(bundled_icon(achievement.code) or GENERIC_ICON)


def category_icon_url(category: AchievementCategory) -> str:
    """URL of a category's medallion: the image uploaded in Gestión, else the generic one."""
    if category.image:
        return category.image.url
    return static(CATEGORY_ICON)


@dataclass(frozen=True)
class Medal:
    image_url: str
    rim: Rim
    level: int  # 0 when not earned
    locked: bool = False  # not earned yet (shown greyed out)

    @property
    def pill(self) -> int | None:
        """The "xN" mark, from level 2 up."""
        return self.level if self.level >= 2 and not self.locked else None


def medal_for(
    achievement: Achievement,
    level: int,
    *,
    concealed: bool = False,
    locked: bool | None = None,
) -> Medal:
    """The medal of `achievement` at `level` (0 = not earned). `concealed`: a secret not earned
    yet, shown as the placeholder without anything of the real one. `locked` (default: level 0)
    greys it out, e.g. a level not reached yet shown with its own ring."""
    if concealed:
        return Medal(image_url=icon_url(achievement, concealed=True), rim=Rim.SECRET, level=0)
    return Medal(
        image_url=icon_url(achievement),
        rim=rim_for(level),
        level=level,
        locked=level == 0 if locked is None else locked,
    )


def preview_medals(achievement: Achievement) -> list[Medal]:
    """The medal at each of its levels (one for achievements without levels), for Gestión. Uses
    `achievement.tiers.all()`, so prefetch the tiers when previewing many."""
    levels = sorted(tier.level for tier in achievement.tiers.all()) or [1]
    return [medal_for(achievement, level) for level in levels]


def top_medal(achievement: Achievement) -> Medal:
    """The medal at its highest level (level 1 without levels), as Gestión's list shows it."""
    return preview_medals(achievement)[-1]
