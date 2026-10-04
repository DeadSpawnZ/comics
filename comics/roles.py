"""User roles, modelled as Django groups.

- Collectors: every registered user. Mis comics and collectables only need a signed-in user, so
  the group carries no permissions; it makes the role explicit and is where collector-only
  permissions will go.
- Reviewers: no permissions yet. Placeholder for the change-request feature (Reviewers validate
  changes to issues/editions/connectings, Administrators approve them); its permissions will be
  declared on the change-request model and added here and in a migration.
- Administrators: access to Gestión (`comics.access_management`).
- Superusers (a user flag, not a group): implicitly hold every permission, so they also pass
  Gestión checks; they are the only users allowed into the Django admin (see comics/admin_site.py).

Views check permissions, never group names, so a role can change its permissions (in a migration
or in the admin) without touching code.

The groups are created by migration 0093 and can be rebuilt with `manage.py ensure_roles`
(after restoring a dump taken before the roles existed, or after `flush`)."""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.db import transaction

COLLECTORS = "Collectors"
REVIEWERS = "Reviewers"
ADMINISTRATORS = "Administrators"

ACCESS_MANAGEMENT = "comics.access_management"

# Permissions ("app_label.codename") each role grants. The data migration that creates the groups
# keeps its own frozen copy; a test checks the database still matches this table.
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    COLLECTORS: (),
    REVIEWERS: (),
    ADMINISTRATORS: (ACCESS_MANAGEMENT,),
}


def assign_default_role(user: User) -> None:
    """Make `user` a Collector, the role every registered user has. Safe to call more than once.

    Called for every new user by the post_save signal in comics/signals.py, so callers creating
    users (registration, createsuperuser, the admin, scripts) do not need to call it themselves."""
    collectors, _created = Group.objects.get_or_create(name=COLLECTORS)
    user.groups.add(collectors)


def can_access_management(user: User | AnonymousUser) -> bool:
    """Whether `user` may open Gestión (superusers always may; anonymous and inactive users never)."""
    return user.has_perm(ACCESS_MANAGEMENT)


def ensure_roles() -> None:
    """Create the role groups with their permissions and place every user, like migration 0093:
    everyone is a Collector and superusers are also Administrators. Only adds, never removes, so
    it is safe to run any number of times and keeps memberships and permissions added by hand."""
    with transaction.atomic():
        for name, permissions in ROLE_PERMISSIONS.items():
            group, _created = Group.objects.get_or_create(name=name)
            group.permissions.add(*(_permission(perm) for perm in permissions))
        Group.objects.get(name=COLLECTORS).user_set.add(*User.objects.values_list("pk", flat=True))
        superusers = User.objects.filter(is_superuser=True).values_list("pk", flat=True)
        Group.objects.get(name=ADMINISTRATORS).user_set.add(*superusers)


def _permission(perm: str) -> Permission:
    """The Permission row for "app_label.codename". The app's model permissions are created first if
    missing (they normally exist: post_migrate creates them on migrate and on flush)."""
    app_label, codename = perm.split(".")
    create_permissions(apps.get_app_config(app_label), verbosity=0)
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)
