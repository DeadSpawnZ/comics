"""Services over the versions of issues and editions (see comics.history for how they are written):
apply a change and get the version it produced, go back to any version, and describe the versions
(with what changed in each one) for Gestión."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Field, FileField, ForeignKey, ManyToManyField, Model
from django.utils import formats, timezone
from django.utils.functional import Promise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from simple_history.utils import get_m2m_field_name, get_m2m_reverse_field_name

from comics.achievements.engine import schedule_compilation_owners, schedule_for_owners
from comics.achievements.registry import Event
from comics.history import (
    DELETED,
    history_batch,
    record_change,
    relation_columns,
    tracked_fields,
    versioned_m2m_fields,
    versions_of,
)
from comics.models import Artist, Collection, Edition, Issue, Publishing

# Human labels of the versioned fields (the Gestión form's, where it has one).
FIELD_LABELS: dict[str, str | Promise] = {
    "publishing": "Publishing",
    "number": gettext_lazy("Number"),
    "variant": gettext_lazy("Variant"),
    "printing": gettext_lazy("Printing"),
    "ratio": "Ratio",
    "limited_to": gettext_lazy("Limited to"),
    "retailer_exclusive": gettext_lazy("Store (exclusive)"),
    "event_exclusive": gettext_lazy("Event (exclusive)"),
    "cover_price": gettext_lazy("Cover price"),
    "format": gettext_lazy("Format"),
    "rarity": gettext_lazy("Rarity"),
    "release_date": gettext_lazy("Release date"),
    "image": gettext_lazy("Cover"),
    "thumbnail": gettext_lazy("Thumbnail"),
    "notes": gettext_lazy("Notes"),
    "issue": "Issue",
    "cover_artists": gettext_lazy("Cover artists"),
    "collected_issues": gettext_lazy("Collected issues"),
    "synopsis": gettext_lazy("Synopsis"),
    "creators": gettext_lazy("Creators"),
    "metron_id": "Metron ID",
    "comicvine_id": "Comic Vine ID",
    "gcd_id": "GCD ID",
    "mycomicshop_id": "MyComicShop ID",
}
EMPTY = "—"


def field_label(model: type[Model], name: str) -> str:
    label = FIELD_LABELS.get(name)
    return str(label) if label else str(model._meta.get_field(name).verbose_name).capitalize()


# ---------- Applying changes ----------


def apply_changes(instance: Model, changes: Mapping[str, Any], *, user: User | None, reason: str = "") -> Model:
    """Apply `changes` to a tracked row (an Issue or an Edition) and return the version this wrote.
    The row may also be unsaved (e.g. `Edition()`): it is then created with `changes`, and the
    version is a "created" one (only the fields in `changes` are validated, so give them all).

    `changes` maps field names to their new values: for foreign keys the related object or its id;
    for the versioned many-to-many fields (creators, cover_artists, collected_issues) an iterable of
    related objects or ids, in reading order for collected issues (an empty one makes the edition a
    single-issue edition again). The changed values are validated as the model does (field
    validators, clean(), unique fields and constraints, the model's own save() checks); on
    ValidationError the database is left unchanged. A version (by `user`, with `reason`) is always
    written, even when the values were already those: it is the record of the change."""
    model = instance._meta.concrete_model
    m2m_fields = {f.name: f for f in versioned_m2m_fields(model)}
    targets: dict[str, list[Any]] = {}
    for name, value in changes.items():
        model_field = model._meta.get_field(name)
        if name in m2m_fields:
            targets[name] = [getattr(item, "pk", item) for item in value]
        elif model_field.concrete and not model_field.many_to_many and not model_field.primary_key:
            use_id = isinstance(model_field, ForeignKey) and not isinstance(value, Model)
            setattr(instance, model_field.attname if use_id else model_field.name, value)
        else:
            raise ValueError(f"{model.__name__}.{name} cannot be changed with apply_changes()")
    untouched = [f.name for f in model._meta.fields if f.name not in changes]
    instance.clean_fields(exclude=untouched)
    instance.clean()
    instance.validate_unique(exclude=untouched)  # unique fields, e.g. an issue's Metron ID
    instance.validate_constraints()

    collected = targets.get("collected_issues")
    with transaction.atomic():
        with history_batch(user=user, reason=reason) as batch:
            old_issue_id = None
            if isinstance(instance, Edition) and instance.pk:
                old_issue_id = Edition.objects.filter(pk=instance.pk).values_list("issue_id", flat=True).first()
            if isinstance(instance, Edition) and collected == [] and instance.pk:
                # Before save(): otherwise it still counts as a compilation and keeps no issue.
                instance.collected_entries.all().delete()
            instance.save()
            for name, pks in targets.items():
                replace_relation(instance, m2m_fields[name], rows_for_targets(m2m_fields[name], pks))
            if isinstance(instance, Edition):
                if collected is not None:
                    instance.sync_compilation_state()
                schedule_edition_owners(instance, old_issue_id, contents_changed=collected is not None)
            batch.force(instance)
        return batch.forced_version_of(instance)


def rows_for_targets(m2m_field: ManyToManyField, pks: Iterable[Any]) -> list[dict[str, Any]]:
    """Relation rows (see comics.history.relation_columns) linking to `pks`, numbered in that order
    when the relation has an `order` column."""
    through = m2m_field.remote_field.through
    target = through._meta.get_field(get_m2m_reverse_field_name(m2m_field)).attname
    extra = set(relation_columns(m2m_field)) - {target}
    if extra - {"order"}:
        raise ValueError(f"{m2m_field.name}: relation rows with {sorted(extra)} need explicit values")
    if "order" in extra:
        return [{target: pk, "order": order} for order, pk in enumerate(pks, start=1)]
    return [{target: pk} for pk in pks]


def replace_relation(instance: Model, m2m_field: ManyToManyField, rows: list[dict[str, Any]]) -> bool:
    """Make the relation rows of `instance` exactly `rows`; returns whether they changed."""
    through = m2m_field.remote_field.through
    owner = through._meta.get_field(get_m2m_field_name(m2m_field)).attname
    columns = relation_columns(m2m_field)
    current_rows = through._default_manager.filter(**{owner: instance.pk})
    if sorted(current_rows.values_list(*columns)) == sorted(tuple(row[c] for c in columns) for row in rows):
        return False
    if len(columns) == 1:
        # A plain link to the related row (creators, cover artists): the related manager keeps the
        # unchanged rows and sends m2m_changed, which records the change.
        getattr(instance, m2m_field.name).set([row[columns[0]] for row in rows])
    else:
        # Delete and insert: rows swapping their order would violate the unique constraints if updated.
        current_rows.delete()
        through._default_manager.bulk_create(through(**{owner: instance.pk}, **row) for row in rows)
        record_change(type(instance), instance.pk)  # bulk inserts send no signal
    return True


def schedule_edition_owners(edition: Edition, old_issue_id: int | None, *, contents_changed: bool) -> None:
    """What an edition contains counts for issue-based achievements (as after a Gestión edit)."""
    if contents_changed:
        schedule_compilation_owners(edition.pk)
    if edition.issue_id != old_issue_id:
        schedule_for_owners(Event.COLLECTION, Collection.objects.filter(edition=edition))


# ---------- Reverting ----------


def cannot_restore(message: str) -> ValidationError:
    return ValidationError(_("This version cannot be restored: %(reason)s") % {"reason": message})


def revert_to_version(version: Model, *, user: User | None, reason: str = "") -> Model:
    """Bring a row back to the state stored in one of its versions (fields and relations) and
    return the version this writes, marked `reverted_to=version`, by `user`, with `reason` (it can
    be reverted too). A deleted row is recreated with its id.

    Raises ValidationError, changing nothing, when that state cannot be restored: the version is a
    deletion, a row it points to no longer exists, or another row now has its unique values. An issue
    it points to that was deleted since (e.g. left orphaned when its edition moved to another number)
    is restored from its own history first, or replaced by the issue that now has its publishing and
    number. Image paths are restored as stored: the files are never deleted, so they still exist."""
    model = version.instance_type
    if version.history_type == DELETED:
        raise ValidationError(_("A deleted state cannot be restored: choose an earlier version."))
    pk = getattr(version, model._meta.pk.attname)
    with transaction.atomic():
        with history_batch(user=user, reason=reason) as batch:
            instance = model._default_manager.select_for_update().filter(pk=pk).order_by().first() or model(pk=pk)
            values = restorable_values(version, user=user, reason=reason)
            relations = restorable_relations(version, user=user, reason=reason)
            old_issue_id = instance.issue_id if isinstance(instance, Edition) else None
            for attname, value in values.items():
                setattr(instance, attname, value)
            try:
                instance.validate_unique()  # unique fields, e.g. an issue's Metron ID taken since
                instance.validate_constraints()
            except ValidationError as exc:
                raise cannot_restore(" ".join(exc.messages)) from exc
            try:
                if isinstance(instance, Edition):
                    instance.save(restoring=True)  # keep its images and issue as stored
                else:
                    instance.save()
            except ValidationError as exc:
                raise cannot_restore(" ".join(exc.messages)) from exc
            changed = [replace_relation(instance, f, relations[f.name]) for f in versioned_m2m_fields(model)]
            if isinstance(instance, Edition):
                schedule_edition_owners(instance, old_issue_id, contents_changed=any(changed))
            batch.force(instance, reverted_to=version)
        return batch.forced_version_of(instance)


def restorable_values(version: Model, *, user: User | None, reason: str) -> dict[str, Any]:
    """The field values (by attname) a version stores, its foreign keys checked to exist."""
    model = version.instance_type
    values = {}
    for model_field in tracked_fields(model):
        value = getattr(version, model_field.attname)
        if isinstance(model_field, FileField) and not value:
            value = None if model_field.null else ""  # a version stores "no file" as ""
        if isinstance(model_field, ForeignKey) and value is not None:
            label = field_label(model, model_field.name)
            value = existing_target(model_field.related_model, value, label, user=user, reason=reason)
        values[model_field.attname] = value
    return values


def restorable_relations(version: Model, *, user: User | None, reason: str) -> dict[str, list[dict[str, Any]]]:
    """The relation rows a version stores, per many-to-many field, their targets checked to exist."""
    model = version.instance_type
    relations = {}
    for m2m_field in versioned_m2m_fields(model):
        stored_model = getattr(type(version), m2m_field.name).model
        columns = relation_columns(m2m_field)
        through_fields = {f.attname: f for f in m2m_field.remote_field.through._meta.concrete_fields}
        label = field_label(model, m2m_field.name)
        rows = []
        for row in stored_model._default_manager.filter(history=version).values(*columns):
            for column in columns:
                through_field = through_fields[column]
                if isinstance(through_field, ForeignKey) and row[column] is not None:
                    row[column] = existing_target(
                        through_field.related_model, row[column], label, user=user, reason=reason
                    )
            rows.append(row)
        relations[m2m_field.name] = rows
    return relations


def existing_target(related_model: type[Model], pk: Any, label: str, *, user: User | None, reason: str) -> Any:
    """`pk` if that row exists; for a deleted issue, the issue restored (or its replacement)."""
    if related_model._default_manager.filter(pk=pk).exists():
        return pk
    if related_model is Issue:
        restored = restore_deleted_issue(pk, user=user, reason=reason)
        if restored is not None:
            return restored
    raise cannot_restore(_("its %(field)s (#%(id)s) no longer exists.") % {"field": label, "id": pk})


def restore_deleted_issue(pk: int, *, user: User | None, reason: str) -> int | None:
    """The id of the issue that takes the place of deleted issue `pk`: the one that now has its
    publishing and number, else `pk` itself restored from its last version. None without versions."""
    last_state = versions_of(Issue, pk).exclude(history_type=DELETED).first()
    if last_state is None:
        return None
    same_issue = Issue.objects.filter(publishing_id=last_state.publishing_id, number=last_state.number)
    replacement = same_issue.values_list("pk", flat=True).first()
    if replacement is not None:
        return replacement
    return revert_to_version(last_state, user=user, reason=reason).id


# ---------- Describing the versions ----------


@dataclass
class FieldChange:
    label: str
    old: str
    new: str


@dataclass
class RelationChange:
    label: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reordered: bool = False


@dataclass
class VersionEntry:
    """A version as the Gestión history shows it, with what changed since the previous one."""

    version: Model
    kind: str  # created, changed, deleted, reverted or initial
    kind_label: str
    user_label: str
    reason: str
    changes: list[FieldChange]
    relation_changes: list[RelationChange]
    can_revert: bool


class RelatedNames:
    """Names of the rows the versions point to, read with one query per related model."""

    def __init__(self, ids_by_model: dict[type[Model], set[Any]]) -> None:
        self.objects: dict[type[Model], dict[Any, Model]] = {}
        for related_model, ids in ids_by_model.items():
            queryset = related_model._default_manager.all()
            if related_model is Issue:
                queryset = queryset.select_related("publishing")
            self.objects[related_model] = queryset.in_bulk(ids)

    def label(self, related_model: type[Model], pk: Any) -> str:
        obj = self.objects.get(related_model, {}).get(pk)
        if obj is None:
            return _("#%(id)s (deleted)") % {"id": pk}
        if isinstance(obj, Publishing):
            year = f" ({obj.year})" if obj.year else ""
            return f"{obj.publishing_title}{year} {obj.serie}"
        if isinstance(obj, Artist):
            return obj.name
        return str(obj)


def display_value(model_field: Field, value: Any, names: RelatedNames) -> str:
    if value is None or value == "":
        return EMPTY
    if isinstance(model_field, ForeignKey):
        return names.label(model_field.related_model, value)
    if model_field.choices:
        return str(dict(model_field.flatchoices).get(value, value))
    if isinstance(model_field, FileField):
        return PurePosixPath(str(value)).name
    if isinstance(value, (date, datetime, Decimal)):
        return str(formats.localize(value))
    return str(value)


def version_entries(model: type[Model], pk: Any) -> list[VersionEntry]:
    """Every version of a row, newest first, each with what changed since the one before (for the
    first one, or one that recreated the row, what it set). A fixed number of queries."""
    versions = list(versions_of(model, pk).select_related("history_user"))
    if not versions:
        return []
    m2m_fields = versioned_m2m_fields(model)
    rows = stored_relation_rows(versions, m2m_fields)
    names = RelatedNames(related_ids(model, versions, rows, m2m_fields))
    by_id = {version.history_id: version for version in versions}
    exists = model._default_manager.filter(pk=pk).exists()

    entries = []
    for index, version in enumerate(versions):
        previous = versions[index + 1] if index + 1 < len(versions) else None
        if previous is not None and previous.history_type == DELETED:
            previous = None  # recreated: what it set
        changes, relation_changes = [], []
        if version.history_type != DELETED:
            changes = field_changes(model, previous, version, names)
            for m2m_field in m2m_fields:
                old_rows = rows[previous.history_id][m2m_field.name] if previous else []
                change = relation_change(model, m2m_field, old_rows, rows[version.history_id][m2m_field.name], names)
                if change is not None:
                    relation_changes.append(change)
        kind, kind_label = version_kind(version, by_id)
        entries.append(
            VersionEntry(
                version=version,
                kind=kind,
                kind_label=kind_label,
                user_label=version.history_user.username if version.history_user else _("System"),
                reason=version.history_change_reason or "",
                changes=changes,
                relation_changes=relation_changes,
                can_revert=version.history_type != DELETED and not (index == 0 and exists),
            )
        )
    return entries


def version_kind(version: Model, by_id: dict[int, Model]) -> tuple[str, str]:
    if version.reverted_to_id:
        target = by_id.get(version.reverted_to_id)
        if target is None:
            return "reverted", _("Reverted")
        when = formats.date_format(timezone.localtime(target.history_date), "DATETIME_FORMAT")
        return "reverted", _("Reverted to the version of %(date)s") % {"date": when}
    if version.is_initial:
        return "initial", _("Initial version")
    return {
        "+": ("created", _("Created")),
        "~": ("changed", _("Changed")),
        DELETED: ("deleted", _("Deleted")),
    }[version.history_type]


def stored_relation_rows(
    versions: list[Model], m2m_fields: list[ManyToManyField]
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """{history_id: {field name: [row, ...]}} for every version, one query per field."""
    rows: dict[int, dict[str, list[dict[str, Any]]]] = {
        version.history_id: {m2m_field.name: [] for m2m_field in m2m_fields} for version in versions
    }
    history_ids = list(rows)
    for m2m_field in m2m_fields:
        stored_model = getattr(type(versions[0]), m2m_field.name).model
        stored = stored_model._default_manager.filter(history_id__in=history_ids)
        for row in stored.values("history_id", *relation_columns(m2m_field)).order_by("pk"):
            rows[row.pop("history_id")][m2m_field.name].append(row)
    return rows


def relation_target(m2m_field: ManyToManyField) -> ForeignKey:
    """The through-model foreign key to the related rows (e.g. to the artist)."""
    return m2m_field.remote_field.through._meta.get_field(get_m2m_reverse_field_name(m2m_field))


def related_ids(
    model: type[Model],
    versions: list[Model],
    rows: dict[int, dict[str, list[dict[str, Any]]]],
    m2m_fields: list[ManyToManyField],
) -> dict[type[Model], set[Any]]:
    ids: dict[type[Model], set[Any]] = {}
    for model_field in tracked_fields(model):
        if isinstance(model_field, ForeignKey):
            values = {getattr(version, model_field.attname) for version in versions}
            ids.setdefault(model_field.related_model, set()).update(values - {None})
    for m2m_field in m2m_fields:
        target = relation_target(m2m_field)
        for relations in rows.values():
            ids.setdefault(target.related_model, set()).update(row[target.attname] for row in relations[m2m_field.name])
    return ids


def field_changes(model: type[Model], previous: Model | None, version: Model, names: RelatedNames) -> list[FieldChange]:
    changes = []
    for model_field in tracked_fields(model):
        if model_field.primary_key:
            continue
        new = getattr(version, model_field.attname)
        old = getattr(previous, model_field.attname) if previous is not None else None
        if isinstance(model_field, FileField):  # no file: None or "", the same
            old, new = old or "", new or ""
        if new == old or (previous is None and new in (None, "")):
            continue
        changes.append(
            FieldChange(
                label=field_label(model, model_field.name),
                old=display_value(model_field, old, names) if previous is not None else EMPTY,
                new=display_value(model_field, new, names),
            )
        )
    return changes


def relation_change(
    model: type[Model],
    m2m_field: ManyToManyField,
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    names: RelatedNames,
) -> RelationChange | None:
    """What was added to and removed from a relation (and whether its order changed), or None."""
    target = relation_target(m2m_field)

    def sequence(rows: list[dict[str, Any]]) -> list[Any]:
        if rows and "order" in rows[0]:
            rows = sorted(rows, key=lambda row: row["order"])
        return [row[target.attname] for row in rows]

    old, new = sequence(old_rows), sequence(new_rows)
    added = [pk for pk in new if pk not in old]
    removed = [pk for pk in old if pk not in new]
    kept_old = [pk for pk in old if pk in new]
    kept_new = [pk for pk in new if pk in old]
    reordered = kept_old != kept_new
    if not (added or removed or reordered):
        return None
    label = names.label
    return RelationChange(
        label=field_label(model, m2m_field.name),
        added=[label(target.related_model, pk) for pk in added],
        removed=[label(target.related_model, pk) for pk in removed],
        reordered=reordered,
    )


def object_label(model: type[Model], pk: Any) -> str:
    """How Gestión names a row with versions: its current name, or its last one if it was deleted."""
    obj = model._default_manager.filter(pk=pk).first()
    if obj is None:
        last_state = versions_of(model, pk).exclude(history_type=DELETED).first()
        obj = last_state.instance if last_state is not None else None
    if obj is None:
        return f"#{pk}"
    return obj.short_name if isinstance(obj, Edition) else str(obj)
