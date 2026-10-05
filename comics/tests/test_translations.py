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
    # Achievements.
    "Use a Material Symbols icon name: lowercase letters, digits and _.": "Usa un nombre de ícono de Material Symbols: minúsculas, dígitos y _.",
    "Community": "Comunidad",
    "Seniority": "Antigüedad",
    "has registered a piece": "ha registrado una pieza",
    "comics in the collection": "cómics en la colección",
    "full years as a member": "años completos como miembro",
    "has made the profile private": "ha hecho privado su perfil",
    "Achievement unlocked: %(name)s (level %(level)s)": "Logro desbloqueado: %(name)s (nivel %(level)s)",
    "Achievement unlocked: %(name)s": "Logro desbloqueado: %(name)s",
    "Upload a JPEG, PNG or WebP picture.": "Sube una imagen JPEG, PNG o WebP.",
    "Icon image": "Imagen del ícono",
    "Name (English)": "Nombre (inglés)",
    "Name (Spanish)": "Nombre (español)",
    "Description (English)": "Descripción (inglés)",
    "Description (Spanish)": "Descripción (español)",
    "Hint (English)": "Pista (inglés)",
    "Hint (Spanish)": "Pista (español)",
    "Category": "Categoría",
    "Icon (Material Symbols name)": "Ícono (nombre de Material Symbols)",
    "Order": "Orden",
    "Secret": "Secreto",
    "Enabled": "Activo",
    "Level": "Nivel",
    "Threshold": "Umbral",
    "Label (English)": "Etiqueta (inglés)",
    "Label (Spanish)": "Etiqueta (español)",
    "This achievement is earned or not: it has no levels.": "Este logro se gana o no: no tiene niveles.",
    "Add at least one level.": "Agrega al menos un nivel.",
    "Levels must be numbered 1, 2, 3… with no gaps or repeats.": "Los niveles deben ir numerados 1, 2, 3… sin huecos ni repeticiones.",
    "Each level needs a higher threshold than the previous one.": "Cada nivel necesita un umbral mayor que el anterior.",
    "Achievement saved: %(name)s": "Logro guardado: %(name)s",
    "Achievement": "Logro",
    "%(earned)s of %(total)s": "%(earned)s de %(total)s",
    "Level %(level)s": "Nivel %(level)s",
    "Secret achievement.": "Logro secreto.",
    "Earned on %(date)s": "Obtenido el %(date)s",
    "Names, texts, icons and levels are edited here; how each one is earned is defined in code.": "Aquí se editan nombres, textos, íconos y niveles; cómo se gana cada uno está definido en el código.",
    "Rules in code without an achievement (they award nothing):": "Reglas en el código sin logro (no otorgan nada):",
    "Disabled": "Desactivado",
    "No rule in code": "Sin regla en el código",
    "No levels": "Sin niveles",
    "There are no achievements yet.": "Todavía no hay logros.",
    "Back to achievements": "Volver a logros",
    "Counts: %(measure)s": "Cuenta: %(measure)s",
    "There is no rule in code for this achievement: it is never awarded.": "No hay una regla en el código para este logro: nunca se otorga.",
    "Texts": "Textos",
    "Hints are shown instead of the description while a secret achievement is not earned. Leave them empty to show nothing but ???.": "Las pistas se muestran en lugar de la descripción mientras un logro secreto no se ha ganado. Déjalas vacías para mostrar solo ???.",
    "Appearance": "Apariencia",
    "Icon image (PNG, JPEG or WebP, up to 2 MB; replaces the icon)": "Imagen del ícono (PNG, JPEG o WebP, hasta 2 MB; reemplaza al ícono)",
    "Levels": "Niveles",
    "Each level is reached when the count gets to its threshold. To add a level, fill in the empty row.": "Cada nivel se alcanza cuando la cuenta llega a su umbral. Para agregar un nivel, llena la fila vacía.",
    "This achievement is earned or not: it has a single level.": "Este logro se gana o no: tiene un solo nivel.",
    # Edition rarity and the achievement catalogue.
    "Common": "Común",
    "Rare": "Rara",
    "Very rare": "Muy rara",
    "Legendary": "Legendaria",
    "Treasures": "Tesoros",
    "Sagas": "Sagas",
    "Rarity": "Rareza",
    "very rare or legendary editions owned": "ediciones muy raras o legendarias en la colección",
    "signed pieces owned": "piezas firmadas en la colección",
    "most editions owned of one issue": "máximo de ediciones de un mismo número",
    "most purchases on a single day": "máximo de compras en un solo día",
    "has bought again an issue they sold": "ha vuelto a comprar un número que vendió",
    "connectings completed": "connectings completos",
    "reading arcs completed": "arcos de lectura completos",
    "has signed in on October 31": "ha iniciado sesión un 31 de octubre",
}


NEW_PLURALS = {
    (
        "You unlocked %(count)d achievement: see it on your profile.",
        "You unlocked %(count)d achievements: see them on your profile.",
    ): ("Desbloqueaste %(count)d logro: míralo en tu perfil.", "Desbloqueaste %(count)d logros: míralos en tu perfil."),
    ("%(counter)s user", "%(counter)s users"): ("%(counter)s usuario", "%(counter)s usuarios"),
}


NEW_CONTEXT_TRANSLATIONS = {("rarities", "All"): "Todas"}


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
        for (context, source), spanish in NEW_CONTEXT_TRANSLATIONS.items():
            with self.subTest(context=context, msgid=source):
                self.assertEqual(catalog.pgettext(context, source), spanish)
        for (singular, plural), forms in NEW_PLURALS.items():
            for count, spanish in [(1, forms[0]), (2, forms[1])]:
                with self.subTest(msgid=singular, count=count):
                    self.assertEqual(catalog.ngettext(singular, plural, count), spanish)
