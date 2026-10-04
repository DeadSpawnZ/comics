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
    # Accounts (login page, registration) and error pages.
    "Email": "Correo electrónico",
    "Confirm password": "Confirmar contraseña",
    "A user with that email already exists.": "Ya existe un usuario con ese correo electrónico.",
    "Don't have an account?": "¿No tienes cuenta?",
    "Create account": "Crear cuenta",
    "Register to keep track of your collection.": "Regístrate para llevar el control de tu colección.",
    "Already have an account?": "¿Ya tienes cuenta?",
    "Go to my comics": "Ir a mis comics",
    "Access denied": "Acceso denegado",
    "You don't have permission to open this page.": "No tienes permiso para abrir esta página.",
    "Page not found": "Página no encontrada",
    "This page doesn't exist or was moved.": "Esta página no existe o se movió.",
    # Profiles.
    "Enter a valid phone number.": "Introduce un número de teléfono válido.",
    "Phone": "Teléfono",
    "First name": "Nombre",
    "Last name": "Apellidos",
    "The picture is too large (maximum %(size)s MB).": "La imagen es demasiado grande (máximo %(size)s MB).",
    "Upload a JPEG, PNG, WebP or GIF picture.": "Sube una imagen JPEG, PNG, WebP o GIF.",
    "The picture has too many pixels.": "La imagen tiene demasiados píxeles.",
    "Profile picture": "Foto de perfil",
    "Public profile": "Perfil público",
    "Current password": "Contraseña actual",
    "New password": "Nueva contraseña",
    "Confirm new password": "Confirmar nueva contraseña",
    "Personal information saved.": "Información personal guardada.",
    "Profile picture updated.": "Foto de perfil actualizada.",
    "Privacy settings saved.": "Privacidad guardada.",
    "Password changed.": "Contraseña cambiada.",
    "Profile picture removed.": "Foto de perfil eliminada.",
    "Profile": "Perfil",
    "Member since %(date)s": "Miembro desde %(date)s",
    "Private profile": "Perfil privado",
    "View my profile": "Ver mi perfil",
    "Personal information": "Información personal",
    "Your email and phone are only visible to you.": "Tu correo y tu teléfono solo los ves tú.",
    "Privacy": "Privacidad",
    "A public profile can be seen by other signed-in users; a private one only shows your username.": "Un perfil público lo pueden ver los demás usuarios con sesión iniciada; uno privado solo muestra tu nombre de usuario.",
    "Upload a new picture (JPEG, PNG, WebP or GIF, up to 5 MB)": "Sube una imagen nueva (JPEG, PNG, WebP o GIF, hasta 5 MB)",
    "Upload": "Subir",
    "Remove picture": "Quitar foto",
    "Change password": "Cambiar contraseña",
    "Achievements": "Logros",
    "No achievements yet.": "Todavía no hay logros.",
    "Edit profile": "Editar perfil",
    "Your profile is private: other users only see your username.": "Tu perfil es privado: los demás usuarios solo ven tu nombre de usuario.",
    "This profile is private.": "Este perfil es privado.",
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
