"""What Gestión may do with achievements and categories beyond editing them.

- Achievements: built-in ones (the catalogue) are never created nor deleted here: their rows
  belong to rules in code. Achievements created in Gestión (generic rules) can be deleted while
  nobody has earned them; once earned they can only be disabled (awards are never revoked).
- Categories: deleted only while they have no achievements (the foreign key is PROTECT too)."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils.translation import gettext as _

from comics.models import Achievement, AchievementCategory, PictureModel

from .catalogue import BUILTIN_CODES


def _delete_with_picture(item: PictureModel) -> None:
    """Delete `item` and, once that is committed, its uploaded picture file."""
    picture = getattr(item, item.PICTURE_FIELD)
    name, storage = picture.name, picture.storage
    with transaction.atomic():
        item.delete()
        if name:
            transaction.on_commit(lambda: storage.delete(name))


class DeletionRefusedError(Exception):
    """Raised with the reason, ready to show, when something cannot be deleted."""


def achievement_deletion_blocker(achievement: Achievement) -> str | None:
    """Why `achievement` cannot be deleted, or None when it can."""
    if achievement.code in BUILTIN_CODES:
        return _("Built-in achievements cannot be deleted: disable it instead.")
    if achievement.awards.exists():
        return _("Someone has already earned it, so it cannot be deleted: disable it instead.")
    return None


def delete_achievement(achievement: Achievement) -> None:
    """Delete an achievement created in Gestión, with its levels. Raises DeletionRefusedError."""
    reason = achievement_deletion_blocker(achievement)
    if reason:
        raise DeletionRefusedError(reason)
    try:
        _delete_with_picture(achievement)
    except (ProtectedError, IntegrityError) as exc:  # earned in the meantime (PROTECT or the DB's FK)
        raise DeletionRefusedError(
            _("Someone has already earned it, so it cannot be deleted: disable it instead.")
        ) from exc


def category_deletion_blocker(category: AchievementCategory) -> str | None:
    if category.achievements.exists():
        return _("It has achievements: move them to another category first.")
    return None


def delete_category(category: AchievementCategory) -> None:
    """Delete an empty category. Raises DeletionRefusedError."""
    reason = category_deletion_blocker(category)
    if reason:
        raise DeletionRefusedError(reason)
    try:
        _delete_with_picture(category)
    except (ProtectedError, IntegrityError) as exc:  # an achievement was moved into it in the meantime
        raise DeletionRefusedError(_("It has achievements: move them to another category first.")) from exc
