"""Signal receivers of the comics app (connected in ComicsConfig.ready)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from comics.models import Profile
from comics.roles import assign_default_role


@receiver(post_save, sender=User, dispatch_uid="comics.assign_default_role")
def add_new_user_to_collectors(sender: type[User], instance: User, created: bool, raw: bool, **kwargs: Any) -> None:
    """Every new user is a Collector, whatever created it (registration, createsuperuser, admin).

    `raw` saves come from loaddata, which restores memberships exactly as they were dumped (a dump
    taken with dump.py includes them). Dumps taken before the roles existed have none: run
    `manage.py ensure_roles` after loading one."""
    if created and not raw:
        assign_default_role(instance)


@receiver(post_save, sender=User, dispatch_uid="comics.create_profile")
def create_profile(sender: type[User], instance: User, created: bool, raw: bool, **kwargs: Any) -> None:
    """Every new user gets an (empty) profile. loaddata (`raw`) restores the dumped profiles
    instead; users restored from a dump without them get one on first read
    (Profile.objects.for_user), so no repair command is needed."""
    if created and not raw:
        Profile.objects.get_or_create(user=instance)
