"""Helpers shared by the model modules: the picture base model, localized fields and generated codes."""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.db.models import Model
from django.utils.translation import get_language


class PictureModel(Model):
    """Base for models with one uploaded picture that is re-encoded when it changes.

    Subclasses name the field (PICTURE_FIELD) and implement normalize_picture(). Saving re-encodes
    a new picture and, once the save is committed, deletes the file it replaced. A save whose
    update_fields leave the picture out does neither: a stale in-memory value must not be
    re-encoded nor replace (and delete) the picture stored now."""

    PICTURE_FIELD = ""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.PICTURE_FIELD not in update_fields:
            super().save(*args, **kwargs)
            return
        old_name = self._stored_picture()
        picture = getattr(self, self.PICTURE_FIELD)
        if picture and picture.name != old_name:
            setattr(self, self.PICTURE_FIELD, self.normalize_picture(picture))
        super().save(*args, **kwargs)
        picture = getattr(self, self.PICTURE_FIELD)
        if old_name and old_name != picture.name:
            storage = picture.storage
            transaction.on_commit(lambda: storage.delete(old_name))

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        raise NotImplementedError

    def remove_picture(self) -> None:
        """Drop the picture (save() deletes the file once committed)."""
        if getattr(self, self.PICTURE_FIELD):
            setattr(self, self.PICTURE_FIELD, "")
            self.save(update_fields=[self.PICTURE_FIELD])

    def _stored_picture(self) -> str:
        """File name currently saved in the database ("" when none or not saved yet)."""
        if not self.pk:
            return ""
        stored = type(self)._default_manager.filter(pk=self.pk).values_list(self.PICTURE_FIELD, flat=True)
        return stored.first() or ""


def _random_name(prefix: str) -> str:
    # Random file names: media files are served without a session check.
    return f"{prefix}_{secrets.token_hex(8)}"


def localized(obj: object, field: str) -> str:
    """`obj.<field>_<language>` in the active language, falling back to the other languages."""
    active = (get_language() or settings.LANGUAGE_CODE).split("-")[0]
    codes = [active] + [code for code, _name in settings.LANGUAGES if code != active]
    for code in codes:
        value = getattr(obj, f"{field}_{code}", "")
        if value:
            return value
    return ""


# Codes generated in Gestión start with this, so they never collide with (nor pick up the bundled
# artwork of) a built-in code a future data migration seeds. Built-in codes never use it.
GENERATED_CODE_PREFIX = "g_"


def unique_code(name: str, taken: Iterable[str], *, max_length: int = 50) -> str:
    """A code made from `name` (lowercase ASCII letters, digits and _, after GENERATED_CODE_PREFIX)
    that is not in `taken`: "Noche de brujas" -> "g_noche_de_brujas", then "g_noche_de_brujas_2"...
    Codes are identifiers (natural keys, bundled artwork names), so they never change once given."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    room = max_length - len(GENERATED_CODE_PREFIX)
    base = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:room].strip("_") or "item"
    taken = set(taken)
    code, number = f"{GENERATED_CODE_PREFIX}{base}", 1
    while code in taken:
        number += 1
        suffix = f"_{number}"
        code = f"{GENERATED_CODE_PREFIX}{base[: room - len(suffix)].rstrip('_')}{suffix}"
    return code
