"""Template context processors of the comics app."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject, cached_property

from comics.models import Notification


class NotificationBadge:
    """The unread count of the navbar bell. Lazy: the count query only runs when a template reads
    it (the base layout does, once per page), and then only once per request."""

    CAP = 9  # beyond this the badge shows "9+"

    def __init__(self, user: User | AnonymousUser) -> None:
        self._user = user

    @cached_property
    def unread(self) -> int:
        """Unread notifications, counted up to CAP + 1 (enough to know whether to show "9+")."""
        if not self._user.is_authenticated:
            return 0
        return Notification.objects.unread_count(self._user, limit=self.CAP + 1)

    @property
    def label(self) -> str:
        return f"{self.CAP}+" if self.unread > self.CAP else str(self.unread)


def notifications(request: HttpRequest) -> dict[str, object]:
    # Lazy as well: building the badge reads request.user, which may not be needed by the page.
    return {"notification_badge": SimpleLazyObject(lambda: NotificationBadge(request.user))}
