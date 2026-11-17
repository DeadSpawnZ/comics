"""The Mermaid diagrams of the README (comics.diagrams and `manage.py diagrams`)."""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from comics.diagrams import modules_markdown, schema_markdown
from comics.management.commands.diagrams import replace_section

README = Path(settings.BASE_DIR) / "README.md"


class SchemaDiagramTests(SimpleTestCase):
    def test_leaves_out_historical_models_and_lists_them_as_tracked(self):
        schema = schema_markdown()
        self.assertNotIn("Historical", schema.replace("`Historical*`", ""))
        self.assertIn("`Issue` (con creators)", schema)

    def test_draws_explicit_through_models_instead_of_their_many_to_many_field(self):
        schema = schema_markdown()
        self.assertIn('Issue ||--o{ IssueCreator : "issue"', schema)
        self.assertNotIn('"creators"', schema)
        # An auto-created through table is drawn as the many-to-many relation itself.
        self.assertIn('Publishing }o--o{ Editorial : "editorials"', schema)

    def test_marks_nullable_and_unique_columns(self):
        schema = schema_markdown()
        self.assertIn('ForeignKey issue FK "null"', schema)
        self.assertIn("OneToOneField user FK, UK", schema)


class ModulesDiagramTests(SimpleTestCase):
    def test_resolves_relative_imports_between_components(self):
        modules = modules_markdown()
        # comics.models.catalog imports `..history` (a relative import).
        self.assertIn("models --> history", modules)
        self.assertIn("urls --> views_manage", modules)


class DiagramsCommandTests(SimpleTestCase):
    def test_replaces_only_the_text_between_the_markers(self):
        text = "a\n<!-- diagrams:schema:start -->\nold\n<!-- diagrams:schema:end -->\nb\n"
        self.assertEqual(
            replace_section(text, "schema", "new"),
            "a\n<!-- diagrams:schema:start -->\nnew\n<!-- diagrams:schema:end -->\nb\n",
        )

    def test_fails_when_the_markers_are_missing(self):
        with self.assertRaises(CommandError):
            replace_section("no markers", "schema", "new")

    def test_check_fails_on_an_outdated_readme_without_writing_it(self):
        with TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            markers = "<!-- diagrams:{0}:start -->\n<!-- diagrams:{0}:end -->\n"
            readme.write_text(markers.format("schema") + markers.format("modules"), encoding="utf-8")
            before = readme.read_text(encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("diagrams", readme=readme, check=True, stdout=StringIO())
            self.assertEqual(readme.read_text(encoding="utf-8"), before)

    # The README is not copied into the Docker image (.dockerignore); it is there with the working
    # tree mounted, which is how the suite is run.
    @skipUnless(README.exists(), "README.md is not available")
    def test_readme_diagrams_are_up_to_date(self):
        call_command("diagrams", check=True, stdout=StringIO())
