"""Notifications under the navbar bell and the announcements from Gestión that create them."""

from __future__ import annotations

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db.models import (
    CASCADE,
    SET_NULL,
    CharField,
    CheckConstraint,
    DateTimeField,
    F,
    ForeignKey,
    Index,
    Manager,
    ManyToManyField,
    Model,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from .achievements import Achievement
from .base import localized
from .change_requests import ChangeRequest


# ---------- Notifications ----------
# What users are told about (achievements, announcements from Gestión, the steps of their change
# requests), shown under the navbar bell. A notification stores references, never texts: it is
# rendered in the viewer's language from what it points to, so later edits show up in it.


def validate_site_path(value: str) -> None:
    """Links stay on this site: a path starting with a single "/" (no scheme, no host)."""
    if not value.startswith("/") or not url_has_allowed_host_and_scheme(value, allowed_hosts=None):
        raise ValidationError(
            _("Enter a path on this site that starts with “/”, e.g. /comics/."), code="external_link"
        )


class Announcement(Model):
    """A message from Gestión to everyone or to some roles. Publishing it creates one notification
    per active user in the audience at that moment; editing it changes what those notifications
    show and deleting it removes them."""

    class Audience(TextChoices):
        EVERYONE = "everyone", _("Everyone")
        ROLES = "roles", _("Some roles")

    title_en = CharField(max_length=120)
    title_es = CharField(max_length=120)
    body_en = TextField(max_length=2000, blank=True)
    body_es = TextField(max_length=2000, blank=True)
    link = CharField(max_length=300, blank=True, validators=[validate_site_path])
    # Chosen when it is created (the notifications are created then) and never changed afterwards.
    audience = CharField(max_length=10, choices=Audience.choices, default=Audience.EVERYONE)
    roles = ManyToManyField(Group, blank=True, related_name="announcements")
    created_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="announcements_created")
    created_at = DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.title

    @property
    def title(self) -> str:
        return localized(self, "title")

    @property
    def body(self) -> str:
        return localized(self, "body")

    def recipients(self) -> QuerySet[User]:
        """Active users in the audience now (for ROLES, members of any of its roles)."""
        users = User.objects.filter(is_active=True)
        if self.audience == self.Audience.ROLES:
            users = users.filter(groups__in=self.roles.all()).distinct()
        return users

    def publish(self) -> int:
        """Notify every recipient once; returns how many. Call it once, right after creating the
        announcement (and its roles), inside the same transaction."""
        recipient_ids = list(self.recipients().values_list("pk", flat=True))
        Notification.objects.bulk_create(
            [
                Notification(recipient_id=pk, kind=Notification.Kind.ANNOUNCEMENT, announcement=self)
                for pk in recipient_ids
            ],
            batch_size=500,
        )
        return len(recipient_ids)


class NotificationQuerySet(QuerySet["Notification"]):
    def for_display(self) -> NotificationQuerySet:
        """With what rendering them reads (achievement and its category, announcement, request)."""
        return self.select_related("achievement__category", "announcement", "change_request")

    def unread(self) -> NotificationQuerySet:
        return self.filter(read_at__isnull=True)

    def mark_read(self) -> int:
        """Mark the unread ones read now; returns how many."""
        return self.unread().update(read_at=timezone.now())


class NotificationManager(Manager.from_queryset(NotificationQuerySet)):  # type: ignore[misc]
    def unread_count(self, user: User, *, limit: int) -> int:
        """Unread notifications of `user`, counted up to `limit` (the badge caps the number
        anyway): one query on the (recipient, read_at) index that never counts more rows."""
        # order_by() drops Meta.ordering, so MySQL does not sort every unread row before the LIMIT.
        return self.filter(recipient=user, read_at__isnull=True).order_by()[:limit].count()

    def achievement_unlocked(self, user: User, achievement: Achievement, level: int) -> Notification:
        return self.create(recipient=user, kind=Notification.Kind.ACHIEVEMENT, achievement=achievement, level=level)

    def achievements_unlocked(self, user: User, count: int) -> Notification:
        """The summary of a retroactive run (one per user and run instead of one per award)."""
        return self.create(recipient=user, kind=Notification.Kind.ACHIEVEMENT_SUMMARY, count=count)

    def add_to_summary(self, pk: int, count: int = 1) -> None:
        self.filter(pk=pk).update(count=F("count") + count)

    def change_request_step(self, change_request: ChangeRequest) -> Notification | None:
        """Tell the author of `change_request` about the step it just took (validated, approved or
        rejected: its current status). None when the request has no author any more."""
        if change_request.author_id is None:
            return None
        kind = Notification.CHANGE_REQUEST_KINDS[change_request.status]
        return self.create(recipient_id=change_request.author_id, kind=kind, change_request=change_request)


class Notification(Model):
    """Something a user is told about. `kind` says which references it uses: an achievement and
    the level reached, an announcement, a number of achievements (summaries) or a change request."""

    class Kind(TextChoices):
        ACHIEVEMENT = "achievement"  # a live award or level-up
        ACHIEVEMENT_SUMMARY = "achievement_summary"  # a retroactive run: "you unlocked N"
        ACHIEVEMENTS_HELD = "achievements_held"  # what users held when notifications arrived (migration)
        ANNOUNCEMENT = "announcement"
        CHANGE_REQUEST_VALIDATED = "change_request_validated"  # the author's request steps
        CHANGE_REQUEST_APPROVED = "change_request_approved"
        CHANGE_REQUEST_REJECTED = "change_request_rejected"

    # The kind telling a request's author about each status their request can reach.
    CHANGE_REQUEST_KINDS = {
        ChangeRequest.Status.VALIDATED: Kind.CHANGE_REQUEST_VALIDATED,
        ChangeRequest.Status.APPROVED: Kind.CHANGE_REQUEST_APPROVED,
        ChangeRequest.Status.REJECTED: Kind.CHANGE_REQUEST_REJECTED,
    }

    recipient = ForeignKey(User, on_delete=CASCADE, related_name="notifications")
    kind = CharField(max_length=30, choices=Kind.choices)
    # Achievements with awards cannot be deleted (UserAchievement PROTECTs them), so in practice
    # this cascade never fires; it keeps a hand-made deletion from failing on notifications.
    achievement = ForeignKey(Achievement, on_delete=CASCADE, null=True, blank=True, related_name="notifications")
    announcement = ForeignKey(Announcement, on_delete=CASCADE, null=True, blank=True, related_name="notifications")
    change_request = ForeignKey(
        ChangeRequest, on_delete=CASCADE, null=True, blank=True, related_name="notifications"
    )
    level = PositiveSmallIntegerField(null=True, blank=True)
    count = PositiveIntegerField(null=True, blank=True)
    created_at = DateTimeField(default=timezone.now)
    read_at = DateTimeField(null=True, blank=True)

    objects = NotificationManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            Index(fields=["recipient", "read_at"], name="notification_unread"),
            Index(fields=["recipient", "-created_at"], name="notification_recent"),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(kind="achievement", achievement__isnull=False, level__isnull=False)
                | Q(kind__in=["achievement_summary", "achievements_held"], count__isnull=False)
                | Q(kind="announcement", announcement__isnull=False)
                | Q(
                    kind__in=["change_request_validated", "change_request_approved", "change_request_rejected"],
                    change_request__isnull=False,
                ),
                name="notification_kind_references",
            )
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id} - {self.kind}"

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    @property
    def icon(self) -> str:
        """Material Symbols name."""
        if self.kind == self.Kind.ANNOUNCEMENT:
            return "campaign"
        if self.kind == self.Kind.CHANGE_REQUEST_VALIDATED:
            return "fact_check"
        if self.kind == self.Kind.CHANGE_REQUEST_APPROVED:
            return "task_alt"
        if self.kind == self.Kind.CHANGE_REQUEST_REJECTED:
            return "cancel"
        if self.kind == self.Kind.ACHIEVEMENT:
            return "emoji_events"
        return "military_tech"

    @property
    def text(self) -> str:
        """The headline, in the active language (plain text: templates escape it)."""
        if self.kind == self.Kind.ACHIEVEMENT:
            achievement = self.achievement
            # Disabled achievements are hidden everywhere else: do not name them here either.
            if achievement is None or not achievement.is_active:
                return gettext("An achievement you unlocked is no longer available.")
            if self.level and self.level > 1:
                return gettext("Achievement unlocked: %(name)s (level %(level)s)") % {
                    "name": achievement.name,
                    "level": self.level,
                }
            return gettext("Achievement unlocked: %(name)s") % {"name": achievement.name}
        if self.kind == self.Kind.ACHIEVEMENT_SUMMARY:
            count = self.count or 0
            return ngettext(
                "You unlocked %(count)d achievement: see it on your profile.",
                "You unlocked %(count)d achievements: see them on your profile.",
                count,
            ) % {"count": count}
        if self.kind == self.Kind.ACHIEVEMENTS_HELD:
            count = self.count or 0
            return ngettext(
                "You have %(count)d achievement: see it on your profile.",
                "You have %(count)d achievements: see them on your profile.",
                count,
            ) % {"count": count}
        if self.change_request is not None:
            texts = {
                self.Kind.CHANGE_REQUEST_VALIDATED: gettext("Your request was validated: %(title)s"),
                self.Kind.CHANGE_REQUEST_APPROVED: gettext("Your request was approved: %(title)s"),
                self.Kind.CHANGE_REQUEST_REJECTED: gettext("Your request was rejected: %(title)s"),
            }
            return texts[self.kind] % {"title": self.change_request.title}
        if self.announcement is not None:
            return self.announcement.title
        return ""

    @property
    def detail(self) -> str:
        """Longer text shown on the notifications page (an announcement's body)."""
        if self.kind == self.Kind.ANNOUNCEMENT and self.announcement is not None:
            return self.announcement.body
        if self.change_request is not None:
            # The note of the step it tells about.
            if self.kind == self.Kind.CHANGE_REQUEST_VALIDATED:
                return self.change_request.validation_note
            return self.change_request.decision_note
        return ""

    @property
    def target_url(self) -> str | None:
        """Where opening it leads (a path on this site), or None."""
        if self.kind == self.Kind.ANNOUNCEMENT:
            return (self.announcement.link or None) if self.announcement is not None else None
        if self.change_request_id is not None:
            return reverse("comics_request_detail", args=[self.change_request_id])
        return f"{reverse('profile')}#achievements"

    def mark_read(self) -> None:
        """Mark it read (once: a notification already read keeps its first read time)."""
        if self.read_at is None:
            self.read_at = timezone.now()
            Notification.objects.filter(pk=self.pk, read_at__isnull=True).update(read_at=self.read_at)
