"""`manage.py ensure_achievements`: create the catalogue achievements that are missing."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from comics.achievements.catalogue import ensure_catalogue


class Command(BaseCommand):
    help = (
        "Create the achievements of the catalogue that are missing (e.g. after flush and restoring an "
        "older dump). Idempotent; never changes existing ones. Then run award_achievements."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        created = ensure_catalogue()
        self.stdout.write(self.style.SUCCESS(f"Achievements created: {', '.join(created) or 'none'}."))
