"""Change requests: collectors' proposals of new or corrected issues and editions."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser, User
from django.core.validators import URLValidator
from django.db.models import (
    SET_NULL,
    CharField,
    CheckConstraint,
    DateTimeField,
    ForeignKey,
    Index,
    JSONField,
    Model,
    PositiveBigIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
    URLField,
)
from django.utils import timezone
from django.utils.translation import gettext, pgettext_lazy
from django.utils.translation import gettext_lazy as _

from ..roles import APPROVE_CHANGE_REQUESTS, VALIDATE_CHANGE_REQUESTS
from .catalog import Edition, Issue


# ---------- Change requests ----------
# Collectors propose new issues and editions, or corrections to existing ones; Reviewers validate
# the proposals and Administrators approve them (see comics.change_requests). The history of the
# catalogue (comics.history) knows nothing about requests: an approved request is applied through
# comics.versions and points to the version it produced, so auditing requests is querying this table.


class ChangeRequestQuerySet(QuerySet["ChangeRequest"]):
    def open(self) -> ChangeRequestQuerySet:
        """Waiting for a decision: pending or validated."""
        return self.filter(status__in=ChangeRequest.OPEN_STATUSES)


class ChangeRequest(Model):
    """A proposal to create an issue or an edition, or to change some fields of an existing one.

    Rows are referenced by id (`target_pk`, and the versions by their `history_id`), not by foreign
    keys, as the versions themselves reference them: a request is an audit record that outlives its
    target (an edition deleted since keeps its requests, which then cannot be approved; restoring it
    from its history, with the same id, makes them applicable again).

    `changes` maps field names to proposed values as JSON (foreign keys and many-to-many fields by
    id, dates in ISO format, decimals as strings): for UPDATE only the fields that differ from the
    values when it was submitted, for CREATE every field of the proposed object. `base_history_id`
    is the latest version of the target when it was submitted: what reviewers compare against to
    tell whether the target changed since (conflicts). Each step keeps who took it, when and an
    optional note: the validation (`validated_*`) and the outcome (`decided_*`: approved, rejected,
    or withdrawn by its author)."""

    class Kind(TextChoices):
        EDITION = "edition", _("Edition")
        ISSUE = "issue", _("Issue")

    class Action(TextChoices):
        CREATE = "create", pgettext_lazy("change request", "New")
        UPDATE = "update", _("Correction")

    class Status(TextChoices):
        PENDING = "pending", _("Pending")
        VALIDATED = "validated", _("Validated")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    OPEN_STATUSES = (Status.PENDING, Status.VALIDATED)

    kind = CharField(max_length=10, choices=Kind.choices)
    action = CharField(max_length=10, choices=Action.choices)
    status = CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # The issue or edition: the one to change (UPDATE), or the one created on approval (CREATE).
    target_pk = PositiveBigIntegerField(null=True, blank=True)
    # Its name when the request was submitted (and when it was approved), so lists need no lookups
    # and still name a target deleted since.
    target_label = CharField(max_length=200, blank=True)
    changes = JSONField(default=dict)
    base_history_id = PositiveBigIntegerField(null=True, blank=True)
    applied_history_id = PositiveBigIntegerField(null=True, blank=True)  # the version the approval wrote
    reason = TextField(max_length=1000)
    evidence_url = URLField(max_length=500, blank=True, validators=[URLValidator(schemes=["http", "https"])])
    author = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="change_requests")
    created_at = DateTimeField(default=timezone.now)
    validated_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    validated_at = DateTimeField(null=True, blank=True)
    validation_note = TextField(max_length=1000, blank=True)
    decided_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    decided_at = DateTimeField(null=True, blank=True)
    decision_note = TextField(max_length=1000, blank=True)

    objects = ChangeRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [
            ("validate_changerequest", "Can validate or reject pending change requests"),
            ("approve_changerequest", "Can approve or reject change requests"),
        ]
        indexes = [
            Index(fields=["status", "-created_at"], name="change_request_status"),
            Index(fields=["author", "-created_at"], name="change_request_author"),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(action="create") | Q(target_pk__isnull=False),
                name="change_request_update_has_target",
            ),
            CheckConstraint(
                condition=~Q(status="approved") | Q(target_pk__isnull=False, applied_history_id__isnull=False),
                name="change_request_approved_has_version",
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.title}"

    @property
    def model(self) -> type[Model]:
        return Edition if self.kind == self.Kind.EDITION else Issue

    @property
    def title(self) -> str:
        """What the request is about, e.g. "Correction to the edition Spawn #1 A"."""
        titles = {
            (self.Action.UPDATE, self.Kind.EDITION): gettext("Correction to the edition %(name)s"),
            (self.Action.UPDATE, self.Kind.ISSUE): gettext("Correction to the issue %(name)s"),
            (self.Action.CREATE, self.Kind.EDITION): gettext("New edition %(name)s"),
            (self.Action.CREATE, self.Kind.ISSUE): gettext("New issue %(name)s"),
        }
        return titles[(self.action, self.kind)] % {"name": self.target_label}

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    def target(self) -> Model | None:
        """The issue or edition it is about; None if it does not exist (yet, or any more)."""
        if self.target_pk is None:
            return None
        return self.model._default_manager.filter(pk=self.target_pk).first()

    def allowed_actions(self, user: User | AnonymousUser) -> frozenset[str]:
        """What `user` may do with it now: the state machine of a request.

        pending -> validated (validate permission) -> approved or rejected (approve permission). An
        approver can also approve or reject straight from pending, and a validator can reject a
        pending request. Only its author can withdraw it, while it is pending. Nobody reviews their
        own request (it would let them award themselves the Reportero achievement)."""
        if not user.is_authenticated:
            return frozenset()
        actions: set[str] = set()
        own = self.author_id is not None and self.author_id == user.pk
        if own:
            if self.status == self.Status.PENDING:
                actions.add("withdraw")
            return frozenset(actions)
        can_validate = user.has_perm(VALIDATE_CHANGE_REQUESTS)
        can_approve = user.has_perm(APPROVE_CHANGE_REQUESTS)
        if self.status == self.Status.PENDING:
            if can_validate:
                actions |= {"validate", "reject"}
            if can_approve:
                actions |= {"approve", "reject"}
        elif self.status == self.Status.VALIDATED and can_approve:
            actions |= {"approve", "reject"}
        return frozenset(actions)
