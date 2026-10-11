"""Change requests: collectors propose new issues and editions, or corrections to existing ones;
Reviewers validate them and Administrators approve them (the state machine is
ChangeRequest.allowed_actions). Approving applies the proposal through comics.versions, so the
change gets a version like any other (by the approver, with a reason naming the request), and the
request points to that version. The history never refers to requests.

Every step runs in a transaction that locks the request first, so two reviewers acting at once
cannot both move it; the author is notified of each step in the same transaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Field, ForeignKey, ManyToManyField, Model
from django.utils import timezone
from django.utils.translation import gettext as _

from comics.achievements.engine import schedule
from comics.achievements.registry import Event
from comics.history import history_model, versioned_m2m_fields, version_relation_rows, versions_of
from comics.models import ChangeRequest, Edition, Issue, Notification
from comics.roles import can_approve_change_requests, can_review_change_requests, can_validate_change_requests
from comics.versions import EMPTY, RelatedNames, apply_changes, display_value, field_label

VALIDATE = "validate"
APPROVE = "approve"
REJECT = "reject"
WITHDRAW = "withdraw"

KINDS = {Edition: ChangeRequest.Kind.EDITION, Issue: ChangeRequest.Kind.ISSUE}


def target_label(obj: Model) -> str:
    """How a request names its issue or edition (e.g. "Spawn #1 B", "Spawn #1")."""
    label = obj.short_name if isinstance(obj, Edition) else str(obj)
    return label[: ChangeRequest._meta.get_field("target_label").max_length]


# ---------- Proposed values as JSON ----------


def encode_value(model_field: Field, value: Any) -> Any:
    """A form's cleaned value as `ChangeRequest.changes` stores it (JSON)."""
    if isinstance(model_field, ManyToManyField):
        return sorted(getattr(item, "pk", item) for item in value)
    if isinstance(model_field, ForeignKey):
        return getattr(value, "pk", value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def decode_changes(model: type[Model], changes: Mapping[str, Any]) -> dict[str, Any]:
    """`changes` (JSON) as comics.versions.apply_changes takes them: foreign keys and many-to-many
    fields by id, the other fields as Python values."""
    values = {}
    for name, value in changes.items():
        model_field = model._meta.get_field(name)
        if isinstance(model_field, ManyToManyField):
            values[name] = list(value)
        elif value is None:
            values[name] = None
        else:
            values[name] = model_field.to_python(value)
    return values


# ---------- Submitting ----------


def submit(
    *,
    author: User,
    model: type[Model],
    changes: Mapping[str, Any],
    label: str,
    reason: str,
    evidence_url: str = "",
    target: Model | None = None,
) -> ChangeRequest:
    """Create a pending request: to create a `model` row with `changes` (every field), or, with
    `target`, to change the fields in `changes` (only those that differ). The values were validated
    by the proposal form (comics.forms), with the Gestión form's rules; they are checked again
    against the model when the request is approved."""
    base = versions_of(model, target.pk).first() if target is not None else None
    return ChangeRequest.objects.create(
        kind=KINDS[model],
        action=ChangeRequest.Action.UPDATE if target is not None else ChangeRequest.Action.CREATE,
        target_pk=target.pk if target is not None else None,
        target_label=label,
        changes=dict(changes),
        base_history_id=base.history_id if base is not None else None,
        reason=reason,
        evidence_url=evidence_url,
        author=author,
    )


# ---------- Steps ----------


class InvalidTransition(ValidationError):
    """The step is not possible for the request in its current status."""


def _locked(change_request: ChangeRequest, user: User, action: str) -> ChangeRequest:
    """The request re-read and locked for the rest of the transaction, once `user` may take
    `action` on it now. PermissionDenied when the user's role never allows `action`;
    InvalidTransition when the request's status does not (e.g. it was decided meanwhile)."""
    needed = {
        VALIDATE: can_validate_change_requests,
        APPROVE: can_approve_change_requests,
        REJECT: can_review_change_requests,
    }.get(action)
    if needed is not None and not needed(user):
        raise PermissionDenied
    locked = ChangeRequest.objects.select_for_update().get(pk=change_request.pk)
    if action not in locked.allowed_actions(user):
        raise InvalidTransition(
            _("This request cannot be changed that way: it is %(status)s.")
            % {"status": locked.get_status_display().lower()},
            code="invalid_transition",
        )
    return locked


def validate(change_request: ChangeRequest, *, user: User, note: str = "") -> ChangeRequest:
    """pending -> validated (Reviewers, Administrators). Returns the updated request."""
    with transaction.atomic():
        locked = _locked(change_request, user, VALIDATE)
        locked.status = ChangeRequest.Status.VALIDATED
        locked.validated_by, locked.validated_at, locked.validation_note = user, timezone.now(), note
        locked.save(update_fields=["status", "validated_by", "validated_at", "validation_note"])
        Notification.objects.change_request_step(locked)
    return locked


def reject(change_request: ChangeRequest, *, user: User, note: str = "") -> ChangeRequest:
    """pending -> rejected (Reviewers, Administrators) or validated -> rejected (Administrators)."""
    with transaction.atomic():
        locked = _locked(change_request, user, REJECT)
        _decide(locked, ChangeRequest.Status.REJECTED, user, note)
        Notification.objects.change_request_step(locked)
    return locked


def withdraw(change_request: ChangeRequest, *, user: User) -> ChangeRequest:
    """pending -> withdrawn, by its author. Nobody is notified."""
    with transaction.atomic():
        locked = _locked(change_request, user, WITHDRAW)
        _decide(locked, ChangeRequest.Status.WITHDRAWN, user, "")
    return locked


def approve(change_request: ChangeRequest, *, user: User, note: str = "") -> Model:
    """pending or validated -> approved (Administrators): apply the proposal and return the version
    it wrote. For a correction, only the requested fields are written, even if the target changed
    since it was submitted (the review page shows those conflicts first).

    Raises ValidationError, changing nothing, when the proposal can no longer be applied: the
    target was deleted, a row it points to no longer exists, or the result breaks a model rule
    (e.g. it duplicates another edition)."""
    with transaction.atomic():
        locked = _locked(change_request, user, APPROVE)
        model = locked.model
        values = decode_changes(model, locked.changes)
        if locked.action == ChangeRequest.Action.UPDATE:
            instance = model._default_manager.select_for_update().filter(pk=locked.target_pk).first()
            if instance is None:
                raise ValidationError(
                    _("The %(kind)s this request changes no longer exists: it can only be rejected.")
                    % {"kind": locked.get_kind_display().lower()},
                    code="target_deleted",
                )
        else:
            instance = model()
        _check_relations_exist(model, values)
        reason = _("Change request #%(id)s: %(reason)s") % {"id": locked.pk, "reason": locked.reason}
        try:
            version = apply_changes(instance, values, user=user, reason=reason)
        except IntegrityError as exc:
            # Another approval created or changed a row with the same unique values in the meantime
            # (the validation in apply_changes ran before that one committed).
            raise ValidationError(
                _("Another %(kind)s with these values was just saved: review the request again.")
                % {"kind": locked.get_kind_display().lower()},
                code="duplicate",
            ) from exc
        saved = model._default_manager.get(pk=instance.pk)
        _log_activity(saved, created=locked.action == ChangeRequest.Action.CREATE, user=user)
        locked.target_pk = instance.pk
        locked.target_label = target_label(saved)
        locked.applied_history_id = version.history_id
        _decide(locked, ChangeRequest.Status.APPROVED, user, note)
        Notification.objects.change_request_step(locked)
        schedule(Event.CHANGE_REQUEST, locked.author_id)  # Reportero, after the commit
    return version


# Activity-log wording, like the other Gestión entries (feminine for editions, masculine for issues).
_ACTIVITY_LABELS = {Edition: ("Creada.", "Modificada."), Issue: ("Creado.", "Modificado.")}


def _log_activity(obj: Model, *, created: bool, user: User) -> None:
    """Record the applied change in the admin's activity log (LogEntry: the admin's History and the
    Gestión home), like any other change, so that log is complete whatever the change's origin. The
    entry does not mention the request: auditing requests is done through the request model."""
    created_label, changed_label = _ACTIVITY_LABELS[type(obj)]
    LogEntry.objects.log_actions(
        user_id=user.pk,
        queryset=[obj],
        action_flag=ADDITION if created else CHANGE,
        change_message=created_label if created else changed_label,
        single_object=True,
    )


def _decide(change_request: ChangeRequest, status: str, user: User, note: str) -> None:
    change_request.status = status
    change_request.decided_by, change_request.decided_at, change_request.decision_note = user, timezone.now(), note
    change_request.save()


def _check_relations_exist(model: type[Model], values: Mapping[str, Any]) -> None:
    """The many-to-many targets in `values` still exist (apply_changes validates foreign keys)."""
    for m2m_field in versioned_m2m_fields(model):
        pks = values.get(m2m_field.name)
        if not pks:
            continue
        existing = set(m2m_field.related_model._default_manager.filter(pk__in=pks).values_list("pk", flat=True))
        missing = [pk for pk in pks if pk not in existing]
        if missing:
            raise ValidationError(
                _("%(field)s: #%(id)s no longer exists.")
                % {"field": field_label(model, m2m_field.name), "id": missing[0]},
                code="missing_relation",
            )


# ---------- Reviewing ----------


@dataclass
class FieldReview:
    """One requested field as the review page shows it: its value in the version the request was
    based on, now, and proposed. `conflict`: the field changed since the request was submitted."""

    label: str
    base: str
    current: str
    proposed: str
    conflict: bool
    already_applied: bool  # the current value is already the proposed one


@dataclass
class RequestReview:
    """What the review pages show about a request: its fields and its target (loaded once)."""

    fields: list[FieldReview]
    target: Model | None  # None for a new object, or for a target deleted since

    @property
    def has_conflicts(self) -> bool:
        return any(review.conflict for review in self.fields)


def review_request(change_request: ChangeRequest) -> RequestReview:
    """The requested fields, with what they were, are and would be. Without a base version (a
    target that had none) or a target (a new object, or one deleted since) those columns are empty
    and no conflict is reported. Conflicts are only reported while the request is open: once
    approved, the target holds the proposed values, which naturally differ from the base ones.
    A fixed number of queries."""
    model = change_request.model
    proposed = decode_changes(model, change_request.changes)
    target = change_request.target() if change_request.action == ChangeRequest.Action.UPDATE else None
    base_version = None
    if change_request.base_history_id is not None and change_request.action == ChangeRequest.Action.UPDATE:
        base_version = history_model(model)._default_manager.filter(history_id=change_request.base_history_id).first()
    base_relations = version_relation_rows(base_version) if base_version is not None else {}

    raw: list[tuple[Field, Any, Any, Any]] = []  # (field, base, current, proposed), comparable values
    for name, value in proposed.items():
        model_field = model._meta.get_field(name)
        if isinstance(model_field, ManyToManyField):
            base = sorted(row[0] for row in base_relations[name]) if base_version is not None else None
            current = sorted(getattr(target, name).values_list("pk", flat=True)) if target is not None else None
            value = sorted(value)
        else:
            base = getattr(base_version, model_field.attname) if base_version is not None else None
            current = getattr(target, model_field.attname) if target is not None else None
        raw.append((model_field, base, current, value))

    names = RelatedNames(_related_ids(raw))
    compare = change_request.is_open and base_version is not None and target is not None
    reviews = []
    for model_field, base, current, value in raw:
        reviews.append(
            FieldReview(
                label=field_label(model, model_field.name),
                base=_display(model_field, base, names) if base_version is not None else EMPTY,
                current=_display(model_field, current, names) if target is not None else EMPTY,
                proposed=_display(model_field, value, names),
                conflict=compare and base != current,
                already_applied=target is not None and current == value,
            )
        )
    return RequestReview(fields=reviews, target=target)


def _related_ids(raw: list[tuple[Field, Any, Any, Any]]) -> dict[type[Model], set[Any]]:
    ids: dict[type[Model], set[Any]] = {}
    for model_field, *values in raw:
        if isinstance(model_field, ManyToManyField):
            for value in values:
                ids.setdefault(model_field.related_model, set()).update(value or [])
        elif isinstance(model_field, ForeignKey):
            ids.setdefault(model_field.related_model, set()).update(value for value in values if value is not None)
    return ids


def _display(model_field: Field, value: Any, names: RelatedNames) -> str:
    if isinstance(model_field, ManyToManyField):
        if not value:
            return EMPTY
        return ", ".join(names.label(model_field.related_model, pk) for pk in value)
    return display_value(model_field, value, names)
