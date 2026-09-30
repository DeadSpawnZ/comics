"""Spanish translations of the texts added with the bug fixes, checked against OUR catalog only.

Going through Django's translation machinery would not prove anything: Django merges its own `es`
catalog as a fallback, and that one already translates e.g. "Unknown" as "Desconocido". So the
project's .po is compiled here with msgfmt (the same tool compilemessages uses) and read with the
standard gettext module, with no fallback. It needs GNU gettext: it runs in the Docker image (the
authoritative test run) and is skipped where msgfmt is not installed."""

import gettext
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SPANISH_PO = Path(settings.BASE_DIR) / "locale" / "es" / "LC_MESSAGES" / "django.po"

NEW_TRANSLATIONS = {
    "Unknown": "Desconocido",
    "Your username and password didn't match.": "El usuario y la contraseña no coinciden.",
    "Log in to continue.": "Inicia sesión para continuar.",
}


class SpanishCatalogTests(SimpleTestCase):
    def test_po_file_has_the_new_entries(self) -> None:
        # Runs everywhere (no gettext tools needed); the compiled check below is the stronger one.
        source = SPANISH_PO.read_text(encoding="utf-8")
        for msgid, msgstr in NEW_TRANSLATIONS.items():
            with self.subTest(msgid=msgid):
                self.assertIn(f'\nmsgid "{msgid}"\nmsgstr "{msgstr}"\n', source)

    @unittest.skipUnless(shutil.which("msgfmt"), "GNU gettext (msgfmt) is not installed")
    def test_compiled_catalog_translates_the_new_texts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compiled = Path(tmp) / "django.mo"
            # --check also validates the whole file (format strings, headers), like a real build.
            result = subprocess.run(
                ["msgfmt", "--check", "-o", str(compiled), str(SPANISH_PO)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with compiled.open("rb") as fh:
                catalog = gettext.GNUTranslations(fh)

        for source, spanish in NEW_TRANSLATIONS.items():
            with self.subTest(msgid=source):
                self.assertEqual(catalog.gettext(source), spanish)
