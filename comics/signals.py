"""Signal receivers of the comics app (connected in ComicsConfig.ready)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.http import HttpRequest

from comics.achievements.engine import (
    schedule,
    schedule_compilation_owners,
    schedule_connecting_owners,
    schedule_edition_owners,
    schedule_reading_arc_owners,
)
from comics.achievements.registry import Event, rule_keys_for
from comics.models import (
    CollectedIssue,
    Collection,
    ConnectingPiece,
    Edition,
    Profile,
    ReadingArcEntry,
    Signature,
)
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


# ---------- Achievement triggers: re-evaluate the rules an event can affect, after commit ----------


@receiver(post_save, sender=Collection, dispatch_uid="comics.achievements.collection_saved")
@receiver(post_delete, sender=Collection, dispatch_uid="comics.achievements.collection_deleted")
def collection_changed(sender: type[Collection], instance: Collection, **kwargs: Any) -> None:
    # Deleting can raise progress too (e.g. deleting a sale gives the piece back to the collection).
    if not kwargs.get("raw"):
        schedule(Event.COLLECTION, instance.collector_id)


@receiver(post_save, sender=Signature, dispatch_uid="comics.achievements.signature_saved")
@receiver(post_delete, sender=Signature, dispatch_uid="comics.achievements.signature_deleted")
def signature_changed(sender: type[Signature], instance: Signature, **kwargs: Any) -> None:
    if not kwargs.get("raw") and rule_keys_for(Event.SIGNATURE):
        collector_id = Collection.objects.filter(pk=instance.collectable_id).values_list("collector_id", flat=True)
        schedule(Event.SIGNATURE, collector_id.first())


@receiver(post_save, sender=User, dispatch_uid="comics.achievements.user_saved")
def user_saved(
    sender: type[User], instance: User, created: bool, raw: bool, update_fields: frozenset[str] | None, **kwargs: Any
) -> None:
    """A new account (registration, admin, createsuperuser), or changed personal info (names,
    email). Signing in only updates last_login: that is the LOGIN event, not a profile change."""
    if raw:
        return
    if created:
        schedule(Event.ACCOUNT, instance.pk)
    elif update_fields is None or set(update_fields) != {"last_login"}:
        schedule(Event.PROFILE, instance.pk)


@receiver(post_save, sender=Profile, dispatch_uid="comics.achievements.profile_saved")
def profile_changed(sender: type[Profile], instance: Profile, raw: bool, **kwargs: Any) -> None:
    if not raw:
        schedule(Event.PROFILE, instance.user_id)


@receiver(user_logged_in, dispatch_uid="comics.achievements.logged_in")
def logged_in(sender: type[User], request: HttpRequest | None, user: User, **kwargs: Any) -> None:
    schedule(Event.LOGIN, user.pk)


@receiver(post_save, sender=Edition, dispatch_uid="comics.achievements.edition_saved")
def edition_rarity_changed(sender: type[Edition], instance: Edition, created: bool, raw: bool, **kwargs: Any) -> None:
    """A rarity change can complete or undo rarity-based progress of the edition's owners.
    (QuerySet.update() sends no signal: rarity is edited one edition at a time, in Gestión or the admin.)"""
    previous = getattr(instance, "loaded_rarity", None)
    instance.loaded_rarity = instance.rarity
    if not (created or raw) and previous != instance.rarity:
        schedule_edition_owners(instance.pk)


@receiver(post_save, sender=ConnectingPiece, dispatch_uid="comics.achievements.piece_saved")
@receiver(post_delete, sender=ConnectingPiece, dispatch_uid="comics.achievements.piece_deleted")
def connecting_piece_changed(sender: type[ConnectingPiece], instance: ConnectingPiece, **kwargs: Any) -> None:
    # Gestión replaces all pieces with a bulk insert (no signals) and schedules the owners itself;
    # these cover the admin and deletions.
    if not kwargs.get("raw"):
        schedule_connecting_owners(instance.connecting_id)


@receiver(post_save, sender=ReadingArcEntry, dispatch_uid="comics.achievements.arc_entry_saved")
@receiver(post_delete, sender=ReadingArcEntry, dispatch_uid="comics.achievements.arc_entry_deleted")
def reading_arc_entry_changed(sender: type[ReadingArcEntry], instance: ReadingArcEntry, **kwargs: Any) -> None:
    if not kwargs.get("raw"):
        schedule_reading_arc_owners(instance.arc_id)


@receiver(post_save, sender=CollectedIssue, dispatch_uid="comics.achievements.collected_issue_saved")
@receiver(post_delete, sender=CollectedIssue, dispatch_uid="comics.achievements.collected_issue_deleted")
def collected_issue_changed(sender: type[CollectedIssue], instance: CollectedIssue, **kwargs: Any) -> None:
    # What a compilation collects counts towards reading arcs.
    if not kwargs.get("raw"):
        schedule_compilation_owners(instance.edition_id)
