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
SPANISH_JS_PO = SPANISH_PO.with_name("djangojs.po")

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
    "Change profile picture": "Cambiar foto de perfil",
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
    # Versioned history of editions and issues (Gestión).
    "History": "Historial",
    "Issue history": "Historial del issue",
    "Back": "Volver",
    "History of the edition %(name)s": "Historial de la edición %(name)s",
    "History of the issue %(name)s": "Historial del issue %(name)s",
    "Versions": "Versiones",
    "Reason:": "Motivo:",
    "added": "añadidos",
    "removed": "quitados",
    "order changed": "orden cambiado",
    "Revert to this version": "Volver a esta versión",
    "Revert to this version?": "¿Volver a esta versión?",
    "Revert": "Volver a esta versión",
    "Reason (optional)": "Motivo (opcional)",
    "Restored the version of %(date)s.": "Se restauró la versión del %(date)s.",
    "This version cannot be restored: %(reason)s": "Esta versión no se puede restaurar: %(reason)s",
    "its %(field)s (#%(id)s) no longer exists.": "su %(field)s (#%(id)s) ya no existe.",
    "A deleted state cannot be restored: choose an earlier version.": "No se puede restaurar un estado eliminado: elige una versión anterior.",
    "#%(id)s (deleted)": "#%(id)s (eliminado)",
    "System": "Sistema",
    "Reverted": "Restaurada",
    "Reverted to the version of %(date)s": "Restaurada la versión del %(date)s",
    "Initial version": "Versión inicial",
    "Created": "Creado",
    "Thumbnail": "Miniatura",
    "Collected issues": "Issues recopilados",
    "Synopsis": "Sinopsis",
    "Creators": "Creadores",
    # Notifications and announcements.
    "Enter a path on this site that starts with “/”, e.g. /comics/.": "Introduce una ruta de este sitio que empiece por “/”, p. ej. /comics/.",
    "Everyone": "Todos",
    "Some roles": "Algunos roles",
    "An achievement you unlocked is no longer available.": "Un logro que desbloqueaste ya no está disponible.",
    "Collectors": "Coleccionistas",
    "Reviewers": "Revisores",
    "Administrators": "Administradores",
    "Title (English)": "Título (inglés)",
    "Title (Spanish)": "Título (español)",
    "Message (English)": "Mensaje (inglés)",
    "Message (Spanish)": "Mensaje (español)",
    "Link (optional)": "Enlace (opcional)",
    "A page of this site, e.g. /comics/. Opening the notification goes there.": "Una página de este sitio, p. ej. /comics/. Al abrir la notificación se va ahí.",
    "Roles": "Roles",
    "Send to": "Enviar a",
    "Choose at least one role.": "Elige al menos un rol.",
    "Announcement saved: %(title)s": "Aviso guardado: %(title)s",
    "Announcement deleted: %(title)s": "Aviso eliminado: %(title)s",
    "Announcements": "Avisos",
    "Announcement": "Aviso",
    "Notifications (%(badge)s unread)": "Notificaciones (%(badge)s sin leer)",
    "Notifications": "Notificaciones",
    "The notifications could not be loaded.": "No se pudieron cargar las notificaciones.",
    "Loading…": "Cargando…",
    "See all": "Ver todas",
    "Unread": "Sin leer",
    "Mark all as read": "Marcar todas como leídas",
    "You have no notifications.": "No tienes notificaciones.",
    "Announcements reach their recipients as notifications. Edits to the texts show up in the notifications already sent. Deleting one also removes its notifications.": "Los avisos llegan a sus destinatarios como notificaciones. Los cambios en los textos se ven en las notificaciones ya enviadas. Al eliminar uno también se eliminan sus notificaciones.",
    "New announcement": "Nuevo aviso",
    "There are no announcements yet.": "Todavía no hay avisos.",
    "Delete announcement?": "¿Eliminar aviso?",
    "Back to announcements": "Volver a avisos",
    "It is sent when you save it: one notification for each active user in its audience.": "Se envía al guardarlo: una notificación para cada usuario activo de su audiencia.",
    "Send": "Enviar",
    "Audience": "Audiencia",
    "Only used when sending to some roles. Users in several roles get it once.": "Solo se usa al enviar a algunos roles. Quien tenga varios roles lo recibe una vez.",
    # Change requests.
    "Suggest a correction": "Sugerir una corrección",
    "Suggest a correction to the issue": "Sugerir una corrección del issue",
    "Propose a new edition": "Proponer una edición nueva",
    "Propose a new issue": "Proponer un issue nuevo",
    "My requests": "Mis solicitudes",
    "Change requests": "Solicitudes de cambio",
    "Reviews": "Revisiones",
    "Why this change?": "¿Por qué este cambio?",
    "Evidence link (optional)": "Enlace de evidencia (opcional)",
    "Change at least one value: this is the current data.": "Cambia al menos un valor: estos son los datos actuales.",
    "Pending": "Pendiente",
    "Validated": "Validada",
    "Approved": "Aprobada",
    "Rejected": "Rechazada",
    "Withdrawn": "Retirada",
    "Correction": "Corrección",
    "Correction to the edition %(name)s": "Corrección de la edición %(name)s",
    "New issue %(name)s": "Nuevo issue %(name)s",
    "Your request was approved: %(title)s": "Tu solicitud fue aprobada: %(title)s",
    "Your request was rejected: %(title)s": "Tu solicitud fue rechazada: %(title)s",
    "Your request was validated: %(title)s": "Tu solicitud fue validada: %(title)s",
    "This request cannot be changed that way: it is %(status)s.": "Esta solicitud no se puede cambiar así: está %(status)s.",
    "Change request #%(id)s: %(reason)s": "Solicitud de cambio #%(id)s: %(reason)s",
    "Request sent: a reviewer will look at it soon.": "Solicitud enviada: un revisor la verá pronto.",
    "Request approved: the change is applied.": "Solicitud aprobada: el cambio ya está aplicado.",
    "Conflict": "Conflicto",
    "Approve and apply": "Aprobar y aplicar",
    "Validate": "Validar",
    "Reject": "Rechazar",
    "Withdraw request": "Retirar solicitud",
    "Note for the author (optional)": "Nota para el autor (opcional)",
    "Suggest a correction to the edition %(name)s": "Sugerir una corrección de la edición %(name)s",
    "has had a change request approved": "le han aprobado una solicitud de cambio",
    # External catalogues: import box of the edition form, identification numbers.
    "%(source)s rejected the credentials. Check them in the .env file.": "%(source)s rechazó las credenciales. Revísalas en el archivo .env.",
    "%(source)s is receiving too many requests from Comi. Wait a minute and try again.": "%(source)s está recibiendo demasiadas peticiones de Comi. Espera un minuto y vuelve a intentarlo.",
    "%(source)s is not working right now (error %(status)s). Try again later.": "%(source)s no está funcionando en este momento (error %(status)s). Inténtalo más tarde.",
    "%(source)s sent an answer Comi cannot read.": "%(source)s envió una respuesta que Comi no puede leer.",
    "%(source)s did not answer in time. Try again in a moment.": "%(source)s no respondió a tiempo. Inténtalo de nuevo en un momento.",
    "%(source)s could not be reached. Try again later.": "No se pudo conectar con %(source)s. Inténtalo más tarde.",
    "Covers can only be downloaded from the catalogues Comi imports from.": "Las portadas solo se pueden descargar de los catálogos de los que importa Comi.",
    "The cover could not be downloaded. Try again, or upload it yourself.": "No se pudo descargar la portada. Inténtalo de nuevo o súbela tú.",
    "The cover is too large (maximum %(size)s MB).": "La portada es demasiado grande (máximo %(size)s MB).",
    "%(source)s is not configured.": "%(source)s no está configurado.",
    "%(source)s has no issue with the id %(id)s.": "%(source)s no tiene ningún issue con el id %(id)s.",
    "%(source)s has no series with the id %(id)s.": "%(source)s no tiene ninguna serie con el id %(id)s.",
    "%(source)s is refusing automated requests right now. Try again later.": "%(source)s está rechazando las peticiones automáticas en este momento. Inténtalo más tarde.",
    "%(source)s has no issue with the id %(id)s in the series %(series)s.": "%(source)s no tiene ningún issue con el id %(id)s en la serie %(series)s.",
    "%(source)s also needs the series id.": "%(source)s también necesita el id de la serie.",
    "Check the artist name “%(name)s”: %(source)s's text does not make clear where it ends.": "Revisa el nombre del artista “%(name)s”: el texto de %(source)s no deja claro dónde acaba.",
    "%(field)s: Comi does not accept “%(value)s” from %(source)s, so it was left out. Fill it in yourself.": "%(field)s: Comi no acepta “%(value)s” de %(source)s, así que se ha dejado fuera. Rellénalo tú.",
    "Enter the series' number in the catalogue, e.g. 58451662.": "Escribe el número de la serie en el catálogo, p. ej. 58451662.",
    "Another edition already has this MyComicShop ID.": "Otra edición ya tiene este ID de MyComicShop.",
    "%(issue)s already has one of these ids: saving them on another issue will fail.": "%(issue)s ya tiene uno de estos ids: guardarlos en otro issue fallará.",
    "%(source)s lists the artists of all the covers together: check the cover artists of the cover you save.": "%(source)s lista juntos a los artistas de todas las portadas: revisa los artistas de la portada que guardes.",
    "The cover is on a server Comi does not download from: upload it yourself.": "La portada está en un servidor del que Comi no descarga: súbela tú.",
    "The number “%(number)s” is longer than Comi allows.": "El número “%(number)s” es más largo de lo que Comi permite.",
    "Only the cover date is known: the release date is usually a few months earlier.": "Solo se conoce la fecha de portada: la fecha de salida suele ser unos meses antes.",
    "The cover price is in %(currency)s.": "El precio de portada está en %(currency)s.",
    "The series type “%(type)s” has no matching format: choose it yourself.": "El tipo de serie “%(type)s” no corresponde a ningún formato: elígelo tú.",
    "Another issue already has this Metron ID.": "Otro issue ya tiene este ID de Metron.",
    "Another issue already has this Comic Vine ID.": "Otro issue ya tiene este ID de Comic Vine.",
    "Another issue already has one of these ids.": "Otro issue ya tiene uno de estos ids.",
    "Enter the issue's number in the catalogue, e.g. 15439.": "Escribe el número del issue en el catálogo, p. ej. 15439.",
    "Metron ID (optional)": "ID de Metron (opcional)",
    "Comic Vine ID (optional)": "ID de Comic Vine (opcional)",
    "GCD ID (optional)": "ID de GCD (opcional)",
    "Import from a catalogue": "Importar de un catálogo",
    "Fill in the form with an issue from a catalogue. With MyComicShop, enter the series ID and pick the cover among its variants. Check the values: nothing is saved until you save the form.": "Rellena el formulario con un issue de un catálogo. Con MyComicShop, escribe el ID de la serie y elige la portada entre sus variantes. Revisa los valores: no se guarda nada hasta que guardes el formulario.",
    "MyComicShop series ID (TID)": "ID de la serie en MyComicShop (TID)",
    "Choose the cover to import": "Elige la portada que quieres importar",
    "Import": "Importar",
    "%(edition)s already has this MyComicShop ID: saving it on another edition will fail.": "%(edition)s ya tiene este ID de MyComicShop: guardarlo en otra edición fallará.",
    "not configured": "sin configurar",
    "Source": "Fuente",
    "Issue ID in the catalogue": "ID del issue en el catálogo",
    "Load": "Cargar",
    "Cover to fill in": "Portada que se rellena",
    "Do not use the imported cover": "No usar la portada importada",
    "Saved on the issue above (the edition's own issue unless you chose another one). Only the values you change are written.": "Se guarda en el issue de arriba (el issue propio de la edición, salvo que hayas elegido otro). Solo se escriben los valores que cambies.",
    "No creators yet.": "Todavía no hay creadores.",
    "Identification numbers": "Números de identificación",
    "(opens in a new tab)": "(se abre en una pestaña nueva)",
    "Copy the %(source)s number": "Copiar el número de %(source)s",
    # Creating a publishing (and its title and editorial) from the edition form; series ids.
    "Only the first issue's cover date is known: check the start date.": "Solo se conoce la fecha de portada del primer issue: revisa la fecha de inicio.",
    "The first issue's date is unknown: the start date is January 1, %(year)s. Check it.": "No se conoce la fecha del primer issue: la fecha de inicio es el 1 de enero de %(year)s. Revísala.",
    "The start date of the series is unknown: enter it.": "No se conoce la fecha de inicio de la serie: escríbela.",
    "The language “%(language)s” is not one of Comi's: choose it yourself.": "El idioma “%(language)s” no es uno de los de Comi: elígelo tú.",
    "%(source)s does not say the language of the series: check it.": "%(source)s no indica el idioma de la serie: revísalo.",
    "The publisher's name “%(name)s” is longer than Comi allows.": "El nombre de la editorial “%(name)s” es más largo de lo que Comi permite.",
    "The publisher's country “%(country)s” is not one of Comi's: choose it yourself.": "El país de la editorial “%(country)s” no es uno de los de Comi: elígelo tú.",
    "Metron series ID": "ID de serie en Metron",
    "Comic Vine volume ID": "ID de volumen en Comic Vine",
    "GCD series ID": "ID de serie en GCD",
    "Another publishing already has this Metron series ID.": "Otro publishing ya tiene este ID de serie de Metron.",
    "Another publishing already has this Comic Vine volume ID.": "Otro publishing ya tiene este ID de volumen de Comic Vine.",
    "A title with that name already exists.": "Ya existe un título con ese nombre.",
    "An editorial with that name already exists.": "Ya existe una editorial con ese nombre.",
    "Country": "País",
    "A publishing with these values already exists.": "Ya existe un publishing con estos valores.",
    "Series in external catalogues": "Serie en catálogos externos",
    "Create publishing": "Crear publishing",
    "Check before creating": "Revisa antes de crear",
    "Create title": "Crear título",
    "Edit title": "Editar título",
    "Update and continue editing": "Actualizar y seguir editando",
    "Create editorial": "Crear editorial",
    "The title groups publishings under a letter: choose it, or create it with +.": "El título agrupa publishings bajo una letra: elígelo, o créalo con +.",
    "Create": "Crear",
    "Groups publishings under a letter.": "Agrupa publishings bajo una letra.",
    "This page has unsaved changes from before it was reloaded.": "Esta página tiene cambios sin guardar de antes de recargarla.",
    "Restore them": "Recuperarlos",
    "Discard": "Descartar",
    "Choose a language": "Elige un idioma",
    "The Metron series is %(metron)s, but its Comic Vine volume is %(comicvine)s: the first one is used. Check whether they are the same publishing.": "La serie de Metron es %(metron)s, pero su volumen de Comic Vine es %(comicvine)s: se usa la primera. Comprueba si son el mismo publishing.",
    "%(source)s writes the title as “%(title)s”: check it against the cover (Comi keeps the title as printed).": "%(source)s escribe el título como “%(title)s”: compáralo con la portada (Comi guarda el título tal como está impreso).",
    "Matched “%(series)s” as “%(title)s”: check that it is the same series.": "Se ha emparejado “%(series)s” con “%(title)s”: comprueba que sea la misma serie.",
    # Import helpers: required hints, editorial aliases, creating artists.
    "Required: choose a title or create one with +.": "Obligatorio: elige un título o créalo con +.",
    "Required: choose an editorial or create one with +.": "Obligatorio: elige una editorial o créala con +.",
    "Required: choose the language.": "Obligatorio: elige el idioma.",
    "Required: enter the start date.": "Obligatorio: escribe la fecha de inicio.",
    "Required: choose an option.": "Obligatorio: elige una opción.",
    "Required: fill in this field.": "Obligatorio: rellena este campo.",
    "Editorial": "Editorial",
    "Alias": "Alias",
    "Enter a name.": "Escribe un nombre.",
    "“%(name)s” is longer than %(limit)d characters.": "“%(name)s” tiene más de %(limit)d caracteres.",
    "Send at most %(limit)d names at a time.": "Envía como máximo %(limit)d nombres a la vez.",
    "“%(name)s” is an alias of %(editorial)s: delete the alias first.": "“%(name)s” es un alias de %(editorial)s: borra primero el alias.",
    "“%(name)s” is already an alias of %(editorial)s.": "“%(name)s” ya es un alias de %(editorial)s.",
    "“%(name)s” is the name of an editorial: it needs no alias.": "“%(name)s” es el nombre de una editorial: no necesita alias.",
    "editorial alias": "alias de editorial",
    "editorial aliases": "alias de editoriales",
    "That alias already exists.": "Ese alias ya existe.",
    "The artists could not be created. Try again.": "No se pudieron crear los artistas. Inténtalo de nuevo.",
    "Create artist": "Crear artista",
    "It is added to the list you opened this from. If an artist already has that name, that one is added instead.": "Se añade a la lista desde la que lo abriste. Si un artista ya tiene ese nombre, se añade ese.",
}


# JavaScript texts (djangojs.po), checked the same way.
NEW_JS_TRANSLATIONS = {
    # MyComicShop series dialog and the cover values of an import (catalog_import.js).
    "Cover: %(values)s": "Portada: %(values)s",
    "MyComicShop ID": "ID de MyComicShop",
    "Store (exclusive)": "Tienda (exclusiva)",
    "Enter the series ID (TID) or paste its MyComicShop URL.": "Escribe el ID de la serie (TID) o pega su URL de MyComicShop.",
    "Cover #%(label)s": "Portada #%(label)s",
    "Already in Comi": "Ya está en Comi",
    "Limited to %(count)s": "Limitada a %(count)s",
    "Printing: %(printing)s": "Impresión: %(printing)s",
    "Store: %(name)s": "Tienda: %(name)s",
    "Event: %(name)s": "Evento: %(name)s",
    "The catalogue lists no covers for this series.": "El catálogo no tiene portadas de esta serie.",
    "Every cover of this series is already in Comi.": "Todas las portadas de esta serie ya están en Comi.",
    "Cover from %(source)s (downloaded when you save)": "Portada de %(source)s (se descarga al guardar)",
    "Publishing: %(name)s": "Publishing: %(name)s",
    "Publishing not found in Comi: %(name)s. Choose it or create it.": "Publishing que no está en Comi: %(name)s. Elígelo o créalo.",
    "Use %(name)s": "Usar %(name)s",
    "Issue already in Comi: %(name)s": "Issue que ya está en Comi: %(name)s",
    "Issue not in Comi yet: it is created when you save.": "El issue todavía no está en Comi: se crea al guardar.",
    "Editorial not found in Comi: %(name)s": "Editorial que no está en Comi: %(name)s",
    "Creators not found in Comi (not added): %(names)s": "Creadores que no están en Comi (no se añaden): %(names)s",
    "Cover artists not found in Comi (not added): %(names)s": "Artistas de portada que no están en Comi (no se añaden): %(names)s",
    "A · Main cover": "A · Portada principal",
    "Open in %(source)s": "Abrir en %(source)s",
    "Loaded from %(source)s. Check the values, then save.": "Cargado de %(source)s. Revisa los valores y guarda.",
    "Enter the issue's ID in the catalogue.": "Escribe el ID del issue en el catálogo.",
    "The catalogue could not be read. Try again.": "No se pudo leer el catálogo. Inténtalo de nuevo.",
    "Copied": "Copiado",
    "Imported cover (downloaded when you save)": "Portada importada (se descarga al guardar)",
    "Could not copy": "No se pudo copiar",
    "Create publishing": "Crear publishing",
    "It could not be created. Check your session and try again.": "No se pudo crear. Revisa tu sesión e inténtalo de nuevo.",
    "It could not be created. Check your connection and try again.": "No se pudo crear. Revisa tu conexión e inténtalo de nuevo.",
    "Your changes were restored. Check them, then save.": "Se recuperaron tus cambios. Revísalos y guarda.",
    "Choose an editorial…": "Elige una editorial…",
    "Existing editorial": "Editorial existente",
    "Remember “%(source)s” as an alias of %(editorial)s": "Recordar “%(source)s” como alias de %(editorial)s",
    "Remember “%(source)s” as an alias of the chosen editorial": "Recordar “%(source)s” como alias de la editorial elegida",
    "Use this editorial": "Usar esta editorial",
    "Choose an editorial first.": "Elige primero una editorial.",
    "It could not be saved. Check your session and try again.": "No se pudo guardar. Revisa tu sesión e inténtalo de nuevo.",
    "It could not be saved. Check your connection and try again.": "No se pudo guardar. Revisa tu conexión e inténtalo de nuevo.",
    "The publisher “%(name)s” is not in Comi. Choose the editorial it is, or create it with +.": "La editorial “%(name)s” del catálogo no está en Comi. Elige a cuál corresponde, o créala con +.",
    "The artists could not be created. Check your session and try again.": "No se pudieron crear los artistas. Revisa tu sesión e inténtalo de nuevo.",
    "The artists could not be created. Check your connection and try again.": "No se pudieron crear los artistas. Revisa tu conexión e inténtalo de nuevo.",
    "Added to the form: %(names)s.": "Añadidos al formulario: %(names)s.",
    "Create artist %(name)s": "Crear el artista %(name)s",
    "Editorial: %(name)s": "Editorial: %(name)s",
    "Creators not found in Comi (create them with +):": "Creadores que no están en Comi (créalos con +):",
    "Cover artists not found in Comi (create them with +):": "Artistas de portada que no están en Comi (créalos con +):",
    "Create all missing artists": "Crear todos los artistas que faltan",
    "“%(source)s” is now an alias of %(editorial)s: next imports find it.": "“%(source)s” ahora es un alias de %(editorial)s: las próximas importaciones la encuentran.",
    "Editorial chosen: %(editorial)s.": "Editorial elegida: %(editorial)s.",
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
    (
        "You have %(count)d achievement: see it on your profile.",
        "You have %(count)d achievements: see them on your profile.",
    ): (
        "Tienes %(count)d logro: míralo en tu perfil.",
        "Tienes %(count)d logros: míralos en tu perfil.",
    ),
    ("Announcement sent to %(count)d user.", "Announcement sent to %(count)d users."): (
        "Aviso enviado a %(count)d usuario.",
        "Aviso enviado a %(count)d usuarios.",
    ),
    ("Read by %(read)s of %(counter)s recipient", "Read by %(read)s of %(counter)s recipients"): (
        "Leído por %(read)s de %(counter)s destinatario",
        "Leído por %(read)s de %(counter)s destinatarios",
    ),
}


NEW_JS_PLURALS = {
    ("%(count)s cover. Choose the one to import.", "%(count)s covers. Choose the one to import."): (
        "%(count)s portada. Elige la que quieres importar.",
        "%(count)s portadas. Elige la que quieres importar.",
    ),
}

NEW_CONTEXT_TRANSLATIONS = {
    ("rarities", "All"): "Todas",
    ("challenge", "Earned"): "Conseguido",
    ("challenge", "Not earned yet"): "Aún no conseguido",
    ("change request", "New"): "Nueva",
    ("requests", "All"): "Todas",
    ("save button", "Update"): "Actualizar",
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

    def test_js_po_file_has_the_new_entries(self) -> None:
        source = SPANISH_JS_PO.read_text(encoding="utf-8")
        for msgid, msgstr in NEW_JS_TRANSLATIONS.items():
            with self.subTest(msgid=msgid):
                self.assertIn(f'\nmsgid "{msgid}"\nmsgstr "{msgstr}"\n', source)

    @unittest.skipUnless(shutil.which("msgfmt"), "GNU gettext (msgfmt) is not installed")
    def test_compiled_js_catalog_translates_the_new_texts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compiled = Path(tmp) / "djangojs.mo"
            result = subprocess.run(
                ["msgfmt", "--check", "-o", str(compiled), str(SPANISH_JS_PO)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with compiled.open("rb") as fh:
                catalog = gettext.GNUTranslations(fh)
        for source, spanish in NEW_JS_TRANSLATIONS.items():
            with self.subTest(msgid=source):
                self.assertEqual(catalog.gettext(source), spanish)
        for (singular, plural), forms in NEW_JS_PLURALS.items():
            for count, spanish in [(1, forms[0]), (2, forms[1])]:
                with self.subTest(msgid=singular, count=count):
                    self.assertEqual(catalog.ngettext(singular, plural, count), spanish)
