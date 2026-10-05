"""`manage.py award_achievements`: evaluate every achievement for existing activity (retroactive).

Safe to run any time and any number of times: awards are computed from the current data, levels
only go up and nothing is revoked. Rules about sign-in times (time windows) only see each user's
last sign-in, so they are not retroactive beyond it. Run it after deploying new achievements.
What it awards is announced to each user with a single summary message instead of one per
achievement."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser

from comics.achievements.engine import evaluate
from comics.achievements.registry import Rule, all_rules
from comics.models import Achievement


class Command(BaseCommand):
    help = "Award the achievements users have already earned (retroactive). Idempotent."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--user", action="append", dest="users", metavar="USERNAME", help="Only these users.")
        parser.add_argument(
            "--code", action="append", dest="codes", metavar="CODE", help="Only these achievements (by code)."
        )

    def handle(
        self, *args: Any, users: list[str] | None = None, codes: list[str] | None = None, **options: Any
    ) -> None:
        rules = all_rules()
        unknown = sorted(set(codes or []) - set(Achievement.objects.values_list("code", flat=True)))
        if unknown:
            raise CommandError(f"Unknown achievements: {', '.join(unknown)}")
        self._warn_about_mismatches(rules)

        queryset = User.objects.filter(is_active=True).order_by("username")
        if users:
            queryset = queryset.filter(username__in=users)
            missing = sorted(set(users) - set(queryset.values_list("username", flat=True)))
            if missing:
                raise CommandError(f"Unknown or inactive users: {', '.join(missing)}")

        total = 0
        for user in queryset.iterator():
            raised = evaluate(user, codes=codes, bulk=True)
            total += len(raised)
            if raised and options["verbosity"] >= 2:
                self.stdout.write(f"{user.username}: {', '.join(raised)}")
        self.stdout.write(self.style.SUCCESS(f"Achievements awarded or raised: {total}."))

    def _warn_about_mismatches(self, rules: dict[str, Rule]) -> None:
        rows = dict(Achievement.objects.values_list("code", "rule"))
        used = set(rows.values())
        for key in sorted(key for key, item in rules.items() if not item.generic and key not in used):
            self.stderr.write(self.style.WARNING(f"Rule {key!r} has no Achievement row: it is not awarded."))
        for code, key in sorted(rows.items()):
            if key not in rules:
                self.stderr.write(
                    self.style.WARNING(f"Achievement {code!r} has no rule {key!r} in code: it is never awarded.")
                )
