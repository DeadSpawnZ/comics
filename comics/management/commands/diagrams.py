"""`manage.py diagrams`: regenerate the Mermaid diagrams of the README (see comics.diagrams).

Each diagram lives between a pair of markers in the README, `<!-- diagrams:<name>:start -->` and
`<!-- diagrams:<name>:end -->`; only that text is replaced. With `--check` nothing is written and
the command fails if a diagram is out of date. It needs neither the database nor Docker:

    .venv/Scripts/python.exe manage.py diagrams --settings=comic.settings_test
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from comics.diagrams import modules_markdown, schema_markdown

DIAGRAMS: dict[str, Callable[[], str]] = {
    "schema": schema_markdown,
    "modules": modules_markdown,
}


def replace_section(text: str, name: str, content: str) -> str:
    """`text` with the part between the markers of `name` replaced by `content`."""
    pattern = re.compile(
        rf"(<!-- diagrams:{name}:start -->\n).*?(<!-- diagrams:{name}:end -->)",
        flags=re.DOTALL,
    )
    if not pattern.search(text):
        raise CommandError(f"Markers for the '{name}' diagram not found.")
    return pattern.sub(lambda match: f"{match.group(1)}{content}\n{match.group(2)}", text, count=1)


class Command(BaseCommand):
    help = "Regenerate the Mermaid diagrams (schema and modules) between their markers in the README."
    requires_system_checks = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--readme", type=Path, default=Path(settings.BASE_DIR) / "README.md")
        parser.add_argument("--check", action="store_true", help="Fail if a diagram is out of date; write nothing.")

    def handle(self, *args: Any, readme: Path, check: bool, **options: Any) -> None:
        current = readme.read_text(encoding="utf-8")
        updated = current
        for name, render in DIAGRAMS.items():
            updated = replace_section(updated, name, render())
        if updated == current:
            self.stdout.write(self.style.SUCCESS("Diagrams up to date."))
        elif check:
            raise CommandError(f"Diagrams out of date in {readme}; run `manage.py diagrams`.")
        else:
            readme.write_text(updated, encoding="utf-8", newline="\n")
            self.stdout.write(self.style.SUCCESS(f"Diagrams updated in {readme}."))
