"""Users' profiles and the area permissions holder."""

from __future__ import annotations

import re
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db.models import CASCADE, BooleanField, CharField, ImageField, Manager, Model, OneToOneField
from django.utils.translation import gettext_lazy as _

from ..helper import square_jpeg
from .base import PictureModel, _random_name


class AreaAccess(Model):
    """Holder of the permissions that open a whole area of the site instead of acting on one model.

    Django ties every permission to a content type, so area permissions need a model to hang from.
    It has no default add/change/delete/view permissions: it exists only so `comics.access_management`
    is declared in code, created by Django's own post_migrate handler on every database, and listed
    in the admin like any other permission. Its table stays empty; it is a real (managed) table so
    every model Django iterates (dumpdata, flush) exists in the database. See comics/roles.py for
    the groups that use these permissions."""

    class Meta:
        default_permissions = ()
        permissions = [("access_management", "Can access the management area (Gestión)")]
        verbose_name = "area access"
        verbose_name_plural = "area access"

    def __str__(self) -> str:
        return str(self._meta.verbose_name)


PHONE_ALLOWED = re.compile(r"^\+?[0-9 ().-]+$")


def validate_phone(value: str) -> None:
    """Light phone check (no library): digits with the usual separators, an optional leading +,
    and 7 to 15 digits (15 is the international maximum, E.164)."""
    digits = sum(char.isdigit() for char in value)
    if not PHONE_ALLOWED.match(value) or not 7 <= digits <= 15:
        raise ValidationError(_("Enter a valid phone number."), code="invalid_phone")


class ProfileManager(Manager["Profile"]):
    def for_user(self, user: User) -> Profile:
        """The user's profile, created on the spot when missing. New users get one from a signal
        and existing ones from a migration, but loaddata skips signals (an old dump restores users
        without profiles), so reads go through here and never fail."""
        profile, _created = self.get_or_create(user=user)
        return profile

    def get_by_natural_key(self, username: str) -> Profile:
        return self.get(user__username=username)


class Profile(PictureModel):
    """What a user adds about themselves. Email and phone are private: only the owner sees them."""

    AVATAR_SIZE = 512  # pixels, square
    PICTURE_FIELD = "avatar"

    user = OneToOneField(User, on_delete=CASCADE, related_name="profile")
    avatar = ImageField(upload_to="avatars/", blank=True)
    phone = CharField(max_length=30, blank=True, validators=[validate_phone])
    is_public = BooleanField(default=True)  # other signed-in users can see the profile

    objects = ProfileManager()

    def __str__(self) -> str:
        return self.user.username

    def natural_key(self) -> tuple[str]:
        # Dumps refer to the profile by its user's username, like the user itself (see dump.py).
        return self.user.natural_key()

    natural_key.dependencies = ["auth.user"]

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_jpeg(_random_name(f"user{self.user_id}"), upload, self.AVATAR_SIZE)

    def remove_avatar(self) -> None:
        self.remove_picture()
