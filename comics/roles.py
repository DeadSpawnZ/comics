"""User roles, modelled as Django groups.

- Collectors: every registered user. Mis comics and collectables only need a signed-in user, so
  the group carries no permissions; it makes the role explicit and is where collector-only
  permissions will go.
- Reviewers: validate (or reject) the change requests collectors submit (`comics.validate_changerequest`),
  in the review area. They do not get Gestión.
- Administrators: access to Gestión (`comics.access_management`), and validate and approve change
  requests (`comics.approve_changerequest`).
- Superusers (a user flag, not a group): implicitly hold every permission, so they also pass
  Gestión checks; they are the only users allowed into the Django admin (see comics/admin_site.py).

Views check permissions, never group names, so a role can change its permissions (in a migration
or in the admin) without touching code.

The groups are created by migration 0093 (the change-request permissions are added by 0110) and can be rebuilt with `manage.py ensure_roles`
(after restoring a dump taken before the roles existed, or after `flush`)."""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.db import transaction
from django.utils.translation import gettext_lazy as _

COLLECTORS = "Collectors"
REVIEWERS = "Reviewers"
ADMINISTRATORS = "Administrators"

ACCESS_MANAGEMENT = "comics.access_management"
VALIDATE_CHANGE_REQUESTS = "comics.validate_changerequest"
APPROVE_CHANGE_REQUESTS = "comics.approve_changerequest"

# How each role is called on screen (group names are identifiers and stay in English).
ROLE_LABELS = {
    COLLECTORS: _("Collectors"),
    REVIEWERS: _("Reviewers"),
    ADMINISTRATORS: _("Administrators"),
}

# Permissions ("app_label.codename") each role grants. The data migrations that grant them (0093,
# 0110) keep their own frozen copies; a test checks the database still matches this table.
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    COLLECTORS: (),
    REVIEWERS: (VALIDATE_CHANGE_REQUESTS,),
    ADMINISTRATORS: (ACCESS_MANAGEMENT, VALIDATE_CHANGE_REQUESTS, APPROVE_CHANGE_REQUESTS),
}


def assign_default_role(user: User) -> None:
    """Make `user` a Collector, the role every registered user has. Safe to call more than once.

    Called for every new user by the post_save signal in comics/signals.py, so callers creating
    users (registration, createsuperuser, the admin, scripts) do not need to call it themselves."""
    collectors, _created = Group.objects.get_or_create(name=COLLECTORS)
    user.groups.add(collectors)


def role_label(name: str) -> str:
    """A role's name on screen (any other group keeps its own name)."""
    return str(ROLE_LABELS.get(name, name))


def can_access_management(user: User | AnonymousUser) -> bool:
    """Whether `user` may open Gestión (superusers always may; anonymous and inactive users never)."""
    return user.has_perm(ACCESS_MANAGEMENT)


def can_validate_change_requests(user: User | AnonymousUser) -> bool:
    """Whether `user` may validate or reject pending change requests (Reviewers, Administrators)."""
    return user.has_perm(VALIDATE_CHANGE_REQUESTS)


def can_approve_change_requests(user: User | AnonymousUser) -> bool:
    """Whether `user` may approve or reject change requests, pending or validated (Administrators)."""
    return user.has_perm(APPROVE_CHANGE_REQUESTS)


def can_review_change_requests(user: User | AnonymousUser) -> bool:
    """Whether `user` may open the review area (the change-request queue): either permission."""
    return can_validate_change_requests(user) or can_approve_change_requests(user)


def ensure_roles() -> None:
    """Create the role groups with their permissions and place every user, like migration 0093:
    everyone is a Collector and superusers are also Administrators. Only adds, never removes, so
    it is safe to run any number of times and keeps memberships and permissions added by hand."""
    with transaction.atomic():
        # Model permissions normally exist (post_migrate creates them on migrate and on flush);
        # create any that are missing so the lookups below cannot fail.
        for app_label in {perm.split(".")[0] for perms in ROLE_PERMISSIONS.values() for perm in perms}:
            create_permissions(apps.get_app_config(app_label), verbosity=0)
        for name, permissions in ROLE_PERMISSIONS.items():
            group, _created = Group.objects.get_or_create(name=name)
            group.permissions.add(*(_permission(perm) for perm in permissions))
        Group.objects.get(name=COLLECTORS).user_set.add(*User.objects.values_list("pk", flat=True))
        superusers = User.objects.filter(is_superuser=True).values_list("pk", flat=True)
        Group.objects.get(name=ADMINISTRATORS).user_set.add(*superusers)


def _permission(perm: str) -> Permission:
    """The Permission row for "app_label.codename"."""
    app_label, codename = perm.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)
