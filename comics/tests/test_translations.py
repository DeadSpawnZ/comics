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
    # Achievement medals.
    "Progress: %(progress)s / %(goal)s": "Progreso: %(progress)s / %(goal)s",
    "Base": "Base",
    "Silver": "Plata",
    "Gold": "Oro",
    "Medal": "Medalla",
    "The medal uses the uploaded image.": "La medalla usa la imagen subida.",
    "The medal uses the artwork included with Comi; an uploaded image replaces it.": "La medalla usa la ilustración incluida en Comi; una imagen subida la reemplaza.",
    # Achievement categories, time windows and achievements created in Gestión.
    "No date window": "Sin ventana de fechas",
    "Every year, between two days": "Cada año, entre dos días",
    "Between two dates": "Entre dos fechas",
    "Enter the day and the month.": "Indica el día y el mes.",
    "That day does not exist in that month.": "Ese día no existe en ese mes.",
    "Enter the start date.": "Indica la fecha de inicio.",
    "Enter the end date.": "Indica la fecha de fin.",
    "The end date cannot be before the start date.": "La fecha de fin no puede ser anterior a la de inicio.",
    "Enter both times, or neither.": "Indica ambas horas, o ninguna.",
    "The end time must be different from the start time.": "La hora de fin debe ser distinta de la de inicio.",
    "has signed in inside the achievement's time window": "ha iniciado sesión dentro de la ventana de tiempo del logro",
    "Medallion image": "Imagen del medallón",
    "Start month": "Mes de inicio",
    "End month": "Mes de fin",
    "Start day": "Día de inicio",
    "End day": "Día de fin",
    "End date": "Fecha de fin",
    "Date window": "Ventana de fechas",
    "From (time)": "Desde (hora)",
    "Until (time)": "Hasta (hora)",
    "Rule": "Regla",
    "This rule only counts inside a time window: set a date window, a time window or both.": "Esta regla solo cuenta dentro de una ventana de tiempo: indica una ventana de fechas, de horas o ambas.",
    "Built-in achievements cannot be deleted: disable it instead.": "Los logros incluidos no se pueden eliminar: desactívalo en su lugar.",
    "Someone has already earned it, so it cannot be deleted: disable it instead.": "Alguien ya lo consiguió, así que no se puede eliminar: desactívalo en su lugar.",
    "It has achievements: move them to another category first.": "Tiene logros: muévelos antes a otra categoría.",
    "Achievement created: %(name)s": "Logro creado: %(name)s",
    "Achievement deleted: %(name)s": "Logro eliminado: %(name)s",
    "“%(name)s” cannot be deleted. %(reason)s": "“%(name)s” no se puede eliminar. %(reason)s",
    "Category created: %(name)s": "Categoría creada: %(name)s",
    "Category saved: %(name)s": "Categoría guardada: %(name)s",
    "Category deleted: %(name)s": "Categoría eliminada: %(name)s",
    "Achievement categories": "Categorías de logros",
    "Achievement category": "Categoría de logros",
    "Categories": "Categorías",
    "New achievement": "Nuevo logro",
    "rule": "regla",
    "Created in Gestión": "Creado en Gestión",
    "No time window": "Sin ventana de tiempo",
    "Its code is made from the name and cannot be changed later.": "Su código se genera a partir del nombre y no se puede cambiar después.",
    "Only generic rules can be used here: they take their settings (such as the time window) from the achievement.": "Aquí solo se pueden usar reglas genéricas: toman su configuración (como la ventana de tiempo) del logro.",
    "Time window": "Ventana de tiempo",
    "The achievement only counts inside its windows (both, when both are set), in the project's time zone. A yearly window may wrap the end of the year (24/12 to 06/01) and a time window may wrap midnight (23:00 to 02:00; the end time is not included).": "El logro solo cuenta dentro de sus ventanas (ambas, si se indican las dos), en la zona horaria del proyecto. Una ventana anual puede cruzar el fin de año (24/12 a 06/01) y una de horas puede cruzar la medianoche (23:00 a 02:00; la hora de fin no se incluye).",
    "Every year: the start and end day and month. 29 February only exists in leap years: in other years a window starting on it starts on 1 March, and one ending on it ends on 28 February.": "Cada año: el día y mes de inicio y de fin. El 29 de febrero solo existe en años bisiestos: los demás años una ventana que empieza ese día empieza el 1 de marzo, y una que termina ese día termina el 28 de febrero.",
    "Between two dates: a one-off event; both days are included.": "Entre dos fechas: un evento único; ambos días se incluyen.",
    "Time of day (optional): leave both empty for the whole day.": "Horas del día (opcional): deja ambas vacías para todo el día.",
    "Delete achievement?": "¿Eliminar logro?",
    "Categories group achievements on profiles. A category can only be deleted when it has no achievements.": "Las categorías agrupan los logros en los perfiles. Una categoría solo se puede eliminar si no tiene logros.",
    "New category": "Nueva categoría",
    "There are no categories yet.": "Todavía no hay categorías.",
    "Back to categories": "Volver a categorías",
    "Medallion image (PNG, JPEG or WebP, up to 2 MB; replaces the generic medallion)": "Imagen del medallón (PNG, JPEG o WebP, hasta 2 MB; reemplaza al medallón genérico)",
    "A disabled category hides all its achievements from profiles and stops awarding them.": "Una categoría desactivada oculta todos sus logros de los perfiles y deja de otorgarlos.",
    "Delete category?": "¿Eliminar categoría?",
    # The Random category, window kinds and the generic medal.
    "a yearly date window": "una ventana anual de fechas",
    "a window between two dates": "una ventana entre dos fechas",
    "a time-of-day window": "una ventana de horas del día",
    "has registered": "se ha registrado",
    "has filled in first name, last name, email, phone and profile picture": "ha llenado nombre, apellidos, correo, teléfono y foto de perfil",
    "has a profile picture": "tiene foto de perfil",
    "has purchases in every month of the achievement's yearly window, in the same year": "tiene compras en cada mes de la ventana anual del logro, en el mismo año",
    "This rule does not use %(kind)s.": "Esta regla no usa %(kind)s.",
    "This rule needs %(kinds)s.": "Esta regla necesita %(kinds)s.",
    "Icon image (PNG, JPEG or WebP, up to 2 MB; replaces the medal artwork)": "Imagen del ícono (PNG, JPEG o WebP, hasta 2 MB; reemplaza la ilustración de la medalla)",
    "The medal uses the generic artwork until it has its own; an uploaded image replaces it.": "La medalla usa la ilustración genérica hasta tener la suya; una imagen subida la reemplaza.",
    # Achievements by category (challenges).
    "%(measure)s: %(count)s": "%(measure)s: %(count)s",
    "Nothing earned in this category yet.": "Todavía no hay nada conseguido en esta categoría.",
}


NEW_PLURALS = {
    (
        "You unlocked %(count)d achievement: see it on your profile.",
        "You unlocked %(count)d achievements: see them on your profile.",
    ): ("Desbloqueaste %(count)d logro: míralo en tu perfil.", "Desbloqueaste %(count)d logros: míralos en tu perfil."),
    ("%(counter)s user", "%(counter)s users"): ("%(counter)s usuario", "%(counter)s usuarios"),
    ("%(counter)s achievement", "%(counter)s achievements"): ("%(counter)s logro", "%(counter)s logros"),
    ("%(earned)s of %(counter)s challenge", "%(earned)s of %(counter)s challenges"): (
        "%(earned)s de %(counter)s reto",
        "%(earned)s de %(counter)s retos",
    ),
    ("Have at least %(count)s comic in your collection", "Have at least %(count)s comics in your collection"): (
        "Ten al menos %(count)s cómic en tu colección",
        "Ten al menos %(count)s cómics en tu colección",
    ),
    ("Be a member for at least %(count)s full year", "Be a member for at least %(count)s full years"): (
        "Sé miembro durante al menos %(count)s año completo",
        "Sé miembro durante al menos %(count)s años completos",
    ),
    (
        "Own at least %(count)s very rare or legendary edition",
        "Own at least %(count)s very rare or legendary editions",
    ): (
        "Ten al menos %(count)s edición muy rara o legendaria",
        "Ten al menos %(count)s ediciones muy raras o legendarias",
    ),
    ("Own at least %(count)s signed piece", "Own at least %(count)s signed pieces"): (
        "Ten al menos %(count)s pieza firmada",
        "Ten al menos %(count)s piezas firmadas",
    ),
    ("Own %(count)s different edition of the same issue", "Own %(count)s different editions of the same issue"): (
        "Ten %(count)s edición distinta del mismo número",
        "Ten %(count)s ediciones distintas del mismo número",
    ),
    ("Buy %(count)s comic on the same day", "Buy %(count)s comics on the same day"): (
        "Compra %(count)s cómic el mismo día",
        "Compra %(count)s cómics el mismo día",
    ),
    ("Complete %(count)s connecting", "Complete %(count)s connectings"): (
        "Completa %(count)s connecting",
        "Completa %(count)s connectings",
    ),
    ("Complete %(count)s reading arc", "Complete %(count)s reading arcs"): (
        "Completa %(count)s arco de lectura",
        "Completa %(count)s arcos de lectura",
    ),
}


NEW_CONTEXT_TRANSLATIONS = {
    ("rarities", "All"): "Todas",
    ("challenge", "Earned"): "Conseguido",
    ("challenge", "Not earned yet"): "Aún no conseguido",
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
        for (context, source), spanish in NEW_CONTEXT_TRANSLATIONS.items():
            with self.subTest(context=context, msgid=source):
                self.assertEqual(catalog.pgettext(context, source), spanish)
        for (singular, plural), forms in NEW_PLURALS.items():
            for count, spanish in [(1, forms[0]), (2, forms[1])]:
                with self.subTest(msgid=singular, count=count):
                    self.assertEqual(catalog.ngettext(singular, plural, count), spanish)
