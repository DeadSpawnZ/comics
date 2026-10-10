"""Versioned history of catalogue rows (Issue, Edition), built on django-simple-history.

Every saved state of a tracked row is a version (a historical record): the row's fields and
relations, who wrote it (`history_user`, empty for the system), when, its type (created, changed,
deleted; `reverted_to` marks a revert, `is_initial` the versions created for the rows that existed
before the history) and an optional reason. Versions know nothing about what caused the change: an
administrator in Gestión, the Django admin, a script or anything built on comics.versions.

How versions are written:

- Outside a batch (scripts, the shell, tests using the ORM directly) simple-history's behavior
  applies: one version per save() and per many-to-many change, by `instance._history_user` (else
  nobody) with `instance._change_reason`.
- Inside a batch (every request through HistoryMiddleware, and the services in comics.versions) the
  saves and relation changes of a row are coalesced: one version per row, written when the batch
  ends, from the row as it then is in the database (fields and relations, including changes made
  with QuerySet.update() or bulk inserts), and skipped when nothing changed since the latest
  version. Deletions are written right away (the row is gone at the end of the batch), so a row
  created and deleted inside the same batch only gets its "deleted" version.

When a batch writes its versions:

- A request's batch (HistoryMiddleware) writes them after the view returned, i.e. after the view's
  own transaction committed, in a transaction of its own. Two near-simultaneous requests editing the
  same row are both read from the database at that point, so the request that writes first may be
  credited with the other's change too (the second then finds nothing new). If writing the versions
  fails, the edit is kept without a version; the next change of the row records the full state.
- The services in comics.versions (apply_changes, revert_to_version) end their batch inside their
  own atomic block: the change and its version are committed together, and the version is returned.

This module must not import comics.models (models.py imports it); the services are in
comics.versions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from asgiref.local import Local
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import (
    SET_NULL,
    BooleanField,
    FileField,
    ForeignKey,
    Field,
    ManyToManyField,
    Model,
    QuerySet,
    TextField,
)
from django.dispatch import receiver
from django.http import HttpRequest, HttpResponse
from simple_history.models import HistoricalRecords
from simple_history.signals import pre_create_historical_record
from simple_history.utils import get_m2m_field_name

CREATED = "+"
CHANGED = "~"
DELETED = "-"

# Many-to-many actions after which the relation rows are final.
M2M_DONE_ACTIONS = ("post_add", "post_remove", "post_clear")

_local = Local()


class VersionFields(Model):
    """Columns every version has besides the tracked fields (a base of the historical models)."""

    # The version this one went back to, when it was written by a revert.
    reverted_to = ForeignKey("self", null=True, blank=True, on_delete=SET_NULL, db_constraint=False, related_name="+")
    # Written for a row that existed before the history did (the first known state, not its creation).
    is_initial = BooleanField(default=False)

    class Meta:
        abstract = True


def history_enabled() -> bool:
    return getattr(settings, "SIMPLE_HISTORY_ENABLED", True)


class TrackedHistory(HistoricalRecords):
    """simple-history's HistoricalRecords plus the batches described in the module docstring, the
    VersionFields columns and an unbounded reason. Register a model with
    `history = TrackedHistory(m2m_fields=[...])`: the listed many-to-many fields are versioned too."""

    tracked: dict[type[Model], TrackedHistory] = {}

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("bases", [VersionFields])
        kwargs.setdefault("history_change_reason_field", TextField(null=True, blank=True))
        super().__init__(**kwargs)

    def finalize(self, sender: type[Model], **kwargs: Any) -> None:
        super().finalize(sender, **kwargs)
        if sender is self.cls:
            TrackedHistory.tracked[sender] = self

    def post_save(self, instance: Model, created: bool, using: str | None = None, **kwargs: Any) -> None:
        if kwargs.get("raw"):
            # loaddata: the dump brings the versions. It sets the many-to-many relations right after
            # this raw save, without saying so: m2m_changed must not write versions either.
            instance._history_raw = True
            return
        if not history_enabled() or hasattr(instance, "skip_history_when_saving"):
            return
        batch = current_batch()
        if batch is not None:
            batch.register(instance, CREATED if created else CHANGED)
            return
        super().post_save(instance, created, using=using, **kwargs)

    def post_delete(self, instance: Model, using: str | None = None, **kwargs: Any) -> None:
        batch = current_batch()
        if batch is not None:
            batch.prepare_deletion(instance)
        super().post_delete(instance, using=using, **kwargs)

    def m2m_changed(
        self, instance: Model, action: str, attr: str, pk_set: set[Any] | None, reverse: bool, **kwargs: Any
    ) -> None:
        if not history_enabled() or hasattr(instance, "skip_history_when_saving"):
            return
        if getattr(instance, "_history_raw", False):
            return
        if not reverse:
            if action in M2M_DONE_ACTIONS:
                record_instance_change(instance)
            return
        # Changed from the other side (e.g. artist.covers.add(edition)): `instance` is not tracked,
        # the rows in pk_set (or, for clear(), the ones it had) are.
        model = kwargs["model"]
        if action == "pre_clear":
            accessor = model._meta.get_field(attr).remote_field.get_accessor_name()
            instance._history_cleared_pks = list(getattr(instance, accessor).values_list("pk", flat=True))
        elif action in M2M_DONE_ACTIONS:
            pks = instance.__dict__.pop("_history_cleared_pks", []) if action == "post_clear" else pk_set
            for pk in pks or ():
                record_change(model, pk)


def tracked_history(model: type[Model]) -> TrackedHistory:
    """The TrackedHistory of a tracked model (KeyError for other models)."""
    return TrackedHistory.tracked[model._meta.concrete_model]


def tracked_fields(model: type[Model]) -> list[Field]:
    """The model's fields a version stores (all its concrete fields)."""
    return tracked_history(model).fields_included(model)


def history_model(model: type[Model]) -> type[Model]:
    """The historical model (the versions table) of a tracked model."""
    return getattr(model._meta.concrete_model, tracked_history(model).manager_name).model


def versions_of(model: type[Model], pk: Any) -> QuerySet[Model]:
    """The versions of one row, newest first (also after the row was deleted)."""
    return history_model(model)._default_manager.filter(**{model._meta.pk.attname: pk}).order_by("-history_id")


def versioned_m2m_fields(model: type[Model]) -> list[ManyToManyField]:
    return tracked_history(model).get_m2m_fields_from_model(model)


def relation_columns(field: ManyToManyField) -> list[str]:
    """The through-model columns that make a relation row, besides the owner and the row's id
    (e.g. ["artist_id"], or ["issue_id", "order"] for the collected issues)."""
    owner = get_m2m_field_name(field)
    through = field.remote_field.through
    return [f.attname for f in through._meta.concrete_fields if not f.primary_key and f.name != owner]


def relation_rows(instance: Model) -> dict[str, list[tuple[Any, ...]]]:
    """The current relation rows of a tracked row, per versioned many-to-many field (sorted)."""
    rows = {}
    for field in versioned_m2m_fields(type(instance)):
        through = field.remote_field.through
        filters = {get_m2m_field_name(field): instance.pk}
        rows[field.name] = sorted(through._default_manager.filter(**filters).values_list(*relation_columns(field)))
    return rows


def version_relation_rows(version: Model) -> dict[str, list[tuple[Any, ...]]]:
    """The relation rows stored in a version, like relation_rows()."""
    model = version.instance_type
    rows = {}
    for field in versioned_m2m_fields(model):
        m2m_model = getattr(type(version), field.name).model
        stored = m2m_model._default_manager.filter(history=version).values_list(*relation_columns(field))
        rows[field.name] = sorted(stored)
    return rows


def stored_value(obj: Model, field: Field) -> Any:
    """A tracked field's value as a version stores it (files by their path, "" when empty)."""
    value = getattr(obj, field.attname)
    if isinstance(field, FileField):
        return getattr(value, "name", value) or ""
    return value


def field_values(obj: Model, model: type[Model]) -> dict[str, Any]:
    """The tracked field values of a row or of one of its versions."""
    return {field.attname: stored_value(obj, field) for field in tracked_fields(model)}


def matches_latest_version(instance: Model) -> bool:
    """Whether the row (fields and relations) is exactly as its latest version stored it."""
    model = instance._meta.concrete_model
    latest = versions_of(model, instance.pk).first()
    if latest is None or latest.history_type == DELETED:
        return False
    return field_values(instance, model) == field_values(latest, model) and relation_rows(
        instance
    ) == version_relation_rows(latest)


def write_version(instance: Model, history_type: str) -> Model:
    """Write a version of `instance` now and return it."""
    tracked_history(type(instance)).create_historical_record(instance, history_type)
    return instance._history_last_version


@receiver(pre_create_historical_record, dispatch_uid="comics.history.fill_version_fields")
def fill_version_fields(sender: type[Model], instance: Model, history_instance: Model, **kwargs: Any) -> None:
    if isinstance(history_instance, VersionFields):
        history_instance.reverted_to = getattr(instance, "_history_reverted_to", None)
        # simple-history does not return the version it writes; write_version() reads it here.
        instance._history_last_version = history_instance


# ---------- Batches ----------


@dataclass
class PendingVersion:
    history_type: str
    user: User | None = None
    reason: str | None = None
    force: bool = False
    reverted_to: Model | None = None


class HistoryBatch:
    """The rows changed inside a history_batch() block, written as one version each when it ends."""

    def __init__(self, user: User | None = None, reason: str | None = None) -> None:
        self.user = user
        self.reason = reason or None
        self.pending: dict[tuple[type[Model], Any], PendingVersion] = {}
        self.versions: dict[tuple[type[Model], Any], Model] = {}

    @staticmethod
    def key(model: type[Model], pk: Any) -> tuple[type[Model], Any]:
        return model._meta.concrete_model, pk

    def register_pk(self, model: type[Model], pk: Any, history_type: str) -> PendingVersion:
        entry = self.pending.setdefault(self.key(model, pk), PendingVersion(history_type))
        if history_type == CREATED:
            entry.history_type = CREATED
        return entry

    def register(self, instance: Model, history_type: str) -> PendingVersion:
        entry = self.register_pk(type(instance), instance.pk, history_type)
        entry.user = getattr(instance, "_history_user", None) or entry.user
        entry.reason = getattr(instance, "_change_reason", None) or entry.reason
        return entry

    def force(self, instance: Model, *, reverted_to: Model | None = None) -> None:
        """Write a version of `instance` at the end even if nothing changed (a service's result)."""
        entry = self.register(instance, CHANGED)
        entry.force = True
        entry.reverted_to = reverted_to

    def prepare_deletion(self, instance: Model) -> None:
        """The row is being deleted: its version is written now (by the batch's user and reason)."""
        self.pending.pop(self.key(type(instance), instance.pk), None)
        if self.user is not None and not hasattr(instance, "_history_user"):
            instance._history_user = self.user
        if self.reason and not hasattr(instance, "_change_reason"):
            instance._change_reason = self.reason

    def version_of(self, instance: Model) -> Model | None:
        """The version written for `instance` when the batch ended (None if none was needed)."""
        return self.versions.get(self.key(type(instance), instance.pk))

    def forced_version_of(self, instance: Model) -> Model:
        """The version written for `instance`, which force() guarantees once the batch has ended."""
        version = self.version_of(instance)
        if version is None:
            raise LookupError(f"No version was written for {instance._meta.label} {instance.pk}")
        return version

    def flush(self) -> None:
        if not self.pending:
            return  # most requests change nothing: no transaction, no queries
        with transaction.atomic():
            for (model, pk), entry in self.pending.items():
                instance = model._default_manager.filter(pk=pk).order_by().first()
                if instance is None:  # deleted or rolled back afterwards
                    if entry.force:
                        raise LookupError(f"{model._meta.label} {pk} was forced a version but no longer exists")
                    continue
                if not entry.force and entry.history_type == CHANGED and matches_latest_version(instance):
                    continue
                user = self.user or entry.user
                if user is not None:
                    instance._history_user = user
                reason = self.reason or entry.reason
                if reason:
                    instance._change_reason = reason
                instance._history_reverted_to = entry.reverted_to
                self.versions[(model, pk)] = write_version(instance, entry.history_type)
        self.pending.clear()


def _batches() -> list[HistoryBatch]:
    if not hasattr(_local, "batches"):
        _local.batches = []
    return _local.batches


def current_batch() -> HistoryBatch | None:
    batches = _batches()
    return batches[-1] if batches else None


@contextmanager
def history_batch(*, user: User | None = None, reason: str | None = None) -> Iterator[HistoryBatch]:
    """Coalesce the changes made inside the block into one version per row, written when it ends
    (see the module docstring). `user` and `reason` apply to every version it writes; without a
    user, the request's (HistoryMiddleware) or the instance's `_history_user`. Nested batches
    write their own rows when they end. If the block raises, nothing is written."""
    batch = HistoryBatch(user=user, reason=reason)
    batches = _batches()
    batches.append(batch)
    try:
        yield batch
    finally:
        batches.remove(batch)
    batch.flush()


def record_change(model: type[Model], pk: Any) -> None:
    """A tracked row changed without a save() of its own (its relation rows were inserted or
    deleted, or it was updated with QuerySet.update()): note it in the current batch, or write a
    version now outside one. Does nothing for models without history."""
    concrete = model._meta.concrete_model
    if concrete not in TrackedHistory.tracked or not history_enabled():
        return
    batch = current_batch()
    if batch is not None:
        batch.register_pk(concrete, pk, CHANGED)
        return
    instance = concrete._default_manager.filter(pk=pk).order_by().first()
    if instance is not None:
        write_version(instance, CHANGED)


def record_instance_change(instance: Model) -> None:
    """record_change() for an instance at hand (keeps its `_history_user` and `_change_reason`)."""
    batch = current_batch()
    if batch is not None:
        batch.register(instance, CHANGED)
    else:
        write_version(instance, CHANGED)


class HistoryMiddleware:
    """Every request is a history batch, by the signed-in user: one version per row it changed.
    Replaces simple-history's HistoryRequestMiddleware (it sets the same request context). Needs
    the authentication middleware before it."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        HistoricalRecords.context.request = request
        try:
            with history_batch():
                return self.get_response(request)
        finally:
            del HistoricalRecords.context.request
