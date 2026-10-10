"""`manage.py ensure_roles`: rebuild the role groups (see comics.roles.ensure_roles)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from comics.roles import ROLE_PERMISSIONS, ensure_roles


class Command(BaseCommand):
    help = (
        "Create the role groups with their permissions, add every user to Collectors and superusers "
        "to Administrators. Idempotent: run it after restoring a dump taken before the roles existed "
        "or after flush."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        ensure_roles()
        self.stdout.write(self.style.SUCCESS(f"Roles ready: {', '.join(ROLE_PERMISSIONS)}."))
