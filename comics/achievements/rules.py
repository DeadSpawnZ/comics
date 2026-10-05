"""How each achievement is earned. Names, texts, icons and tiers are data (Gestión); a rule only
measures progress. Every rule needs a row with the same code, seeded by a data migration."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from comics.models import Collection, Profile

from .registry import Event, Kind, rule


@rule("origin", kind=Kind.BOOLEAN, events=[Event.COLLECTION], measure=_("has registered a piece"))
def origin(user: User) -> int:
    return int(Collection.objects.filter(collector=user).exists())


@rule("great_power", kind=Kind.COUNT, events=[Event.COLLECTION], measure=_("comics in the collection"))
def great_power(user: User) -> int:
    return Collection.objects.owned_by(user).count()


@rule("veteran", kind=Kind.COUNT, events=[Event.LOGIN], measure=_("full years as a member"))
def veteran(user: User) -> int:
    today = timezone.localdate()
    joined = timezone.localdate(user.date_joined)
    years = today.year - joined.year
    if (today.month, today.day) < (joined.month, joined.day):
        years -= 1
    return max(years, 0)


@rule("secret_identity", kind=Kind.BOOLEAN, events=[Event.PROFILE], measure=_("has made the profile private"))
def secret_identity(user: User) -> int:
    return int(Profile.objects.filter(user=user, is_public=False).exists())
