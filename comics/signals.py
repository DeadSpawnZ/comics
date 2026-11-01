"""Signal receivers of the comics app (connected in ComicsConfig.ready)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.db.models import Model
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.http import HttpRequest

from comics import history
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
    EditionCoverArtist,
    IssueCreator,
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


# The User fields achievement rules read (aplicado).
PERSONAL_INFO_FIELDS = frozenset({"first_name", "last_name", "email"})


@receiver(post_save, sender=User, dispatch_uid="comics.achievements.user_saved")
def user_saved(
    sender: type[User], instance: User, created: bool, raw: bool, update_fields: frozenset[str] | None, **kwargs: Any
) -> None:
    """A new account (registration, admin, createsuperuser), or changed personal info (names,
    email). A save limited to other fields (signing in updates last_login, a password change or
    hash upgrade updates password) is not a profile change; a full save may be one."""
    if raw:
        return
    if created:
        schedule(Event.ACCOUNT, instance.pk)
    elif update_fields is None or PERSONAL_INFO_FIELDS & set(update_fields):
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


# ---------- Versions (comics.history) ----------


# The rows of the versioned many-to-many relations: part of their issue's or edition's versions,
# but the admin (inline rows) and scripts save and delete them one by one, without saving the owner.
# See "Relation rows changed on their own" in comics.history.
VERSIONED_RELATION_ROWS: tuple[type[Model], ...] = (IssueCreator, EditionCoverArtist, CollectedIssue)


def relation_row_saving(sender: type[Model], instance: Model, raw: bool, **kwargs: Any) -> None:
    if not raw:
        history.relation_row_saving(instance)


def relation_row_saved(sender: type[Model], instance: Model, raw: bool, **kwargs: Any) -> None:
    if not raw:
        history.relation_row_saved(instance)


def relation_row_deleting(sender: type[Model], instance: Model, **kwargs: Any) -> None:
    history.relation_rows_deleting()


def relation_row_deleted(sender: type[Model], instance: Model, origin: object = None, **kwargs: Any) -> None:
    history.relation_row_deleted(instance, origin)


def connect_relation_rows() -> None:
    for model in VERSIONED_RELATION_ROWS:
        uid = f"comics.history.{model._meta.model_name}"
        pre_save.connect(relation_row_saving, sender=model, dispatch_uid=f"{uid}_saving")
        post_save.connect(relation_row_saved, sender=model, dispatch_uid=f"{uid}_saved")
        pre_delete.connect(relation_row_deleting, sender=model, dispatch_uid=f"{uid}_deleting")
        post_delete.connect(relation_row_deleted, sender=model, dispatch_uid=f"{uid}_deleted")


connect_relation_rows()
