# Backlog

Trabajo pendiente, decisiones abiertas y limitaciones conocidas de Comi. Sirve para que una sesión de
trabajo nueva (humana o de IA) tenga contexto suficiente para retomar cualquier punto sin la
conversación que lo originó.

Cómo trabajar un punto: seguir `CLAUDE.md` (flujo backend con `backend-senior` → `code-reviewer`,
tests solo en MySQL, Ruff una vez por feature, migraciones ensayadas en `comic_refactor` antes de la
base real). Quitar el punto de este archivo en el mismo commit que lo resuelve.

Última actualización: 2026-10-10, en la rama `feat/change-requests`.

## En curso

### Solicitudes de cambio (rama `feat/change-requests`)

Los usuarios solicitan correcciones o altas de Issues y Ediciones; los Revisores validan y los
Administradores aprueban. Diseño acordado con el usuario:

- **El historial es agnóstico.** Cada estado guardado de un Issue o una Edición es una versión
  (quién, cuándo, tipo, motivo, relaciones) mediante `django-simple-history` 3.12.0, sea cual sea su
  origen (Gestión, admin de Django, scripts o una solicitud aprobada). Se puede restaurar cualquier
  versión; una restauración es a su vez otra versión. Hecho en el commit `2c68464` (tarea 1):
  `comics/history.py`, `comics/versions.py` (`apply_changes`, `revert_to_version`), páginas de
  historial en Gestión, migraciones `0107`/`0108`.
- **Las solicitudes son un modelo aparte** que apunta a la versión del historial que generó su
  aprobación. Auditar solicitudes se hace consultando el modelo de solicitudes; el historial nunca
  hace referencia a ellas.
- **Tarea 2, hecha en el commit `1e1990d`:** flujo de solicitudes (pendiente → validada →
  aprobada/rechazada, retirar), permisos para Revisores/Administradores (migración `0110`), área de
  revisión `/revisiones/` para revisores sin acceso a Gestión, detección de conflictos contra la
  versión base, notificaciones al autor, logro "Reportero" (`0111`). Servicio:
  `comics/change_requests.py`.
- **Desplegado en local el 2026-10-10:** migraciones `0107`–`0111` ensayadas en `comic_refactor` y
  aplicadas a la base real (respaldo `backups/comic-20261010-013936.sql`, imagen anterior etiquetada
  `comi:pre-requests`).
- **Commiteado pero sin desplegar:** `d4d1dfd` (aprobar una solicitud también escribe en el registro
  de actividad `LogEntry`, para que el cambio aparezca en el historial de la portada de Gestión y en
  el "Historial" del admin). No tiene migraciones; basta con reconstruir y reiniciar `web`.
- **Pendiente para cerrar la rama:** la pasada de Ruff y los PR `feat/change-requests` → `develop` →
  `master`.
- **Decisiones tomadas por el implementador, pendientes de confirmar por el usuario:** nadie revisa
  sus propias solicitudes; los revisores no pueden rechazar una solicitud ya validada; el autor no ve
  los nombres de quienes la revisaron.
- **Recortes de alcance de la v1 (ampliar si se quiere):** no se proponen imágenes de portada; no se
  editan los issues recopilados de una compilación; las correcciones de un issue se limitan a
  sinopsis y creadores (la serie y el número se corrigen desde la edición).

## Próximas features

### Solicitudes de cambio, siguientes iteraciones
- Solicitudes para **Connectings**.
- **Strikes** para los usuarios cuyas solicitudes se rechacen por infundadas (cada solicitud guarda
  quién decidió y el resultado, así que los strikes se pueden calcular a partir de ellas).
- Propuestas de imagen (portada) y edición del contenido de compilaciones en las solicitudes
  (fuera de la v1).
- `ReadingArcForm` todavía duplica el bucle de estilos de los widgets que ahora está en
  `MaterialFieldsMixin`.

### Integración con ComicVine (orden acordado)
1. **Precarga en el formulario de edición de Gestión:** un campo opcional con el id de ComicVine y un
   botón que carga los datos en el formulario; el issue se puede elegir antes o después de cargar.
   El formulario es el paso de revisión, así que un mal mapeo se detecta antes de guardar.
2. **Importación masiva** de un issue o de un volumen completo, convertida en **solicitudes de
   cambio** para que cada cambio se pueda auditar y aprobar antes de aplicarse.
- Ambas comparten un único mapeador (ComicVine → campos de Comi). Necesita una API key en una
  variable de entorno (gratuita, unas 200 peticiones por recurso y hora, cabecera User-Agent).
  Guardar `comicvine_id` en los modelos importados para evitar duplicados y poder volver a
  sincronizar.
- ComicVine no tiene datos de las variantes, impresiones, ratio, exclusivas de tienda/evento ni del
  país/idioma de la edición de Comi: esos campos seguirán siendo manuales.

### Logros que esperan otra feature
- **Popular / Super Popular** (20 / 100 seguidores): necesitan un sistema de seguidores.
- `GeekCollectable` no tiene dueño, así que no hay logros de collectables por usuario.

### Evaluación diaria de logros (aplazada por el usuario)
Los logros por fecha (Veterano) solo se comprueban al iniciar sesión o con `award_achievements`. El
usuario **no** quiere programadores del sistema (Programador de tareas de Windows / cron del host):
cualquier tarea periódica debe vivir dentro de la app o del stack de Docker. Opciones comentadas:
evaluar las reglas por fecha en la primera visita del día de cada usuario (recomendada) o un servicio
`scheduler` en `docker-compose`. La sección "Logros" del README todavía muestra ejemplos de
cron/`schtasks` y habrá que reescribirla cuando se haga.

## Deuda técnica

### Historial (de la revisión de la tarea 1)
- Revertir una edición puede restaurar un issue borrado; si uno de los creadores de ese issue ya no
  existe, el mensaje de rechazo menciona "Creators", lo que confunde en la reversión de una edición.
- Revertir la serie/número de un Issue mientras hay ediciones que apuntan a él puede dejarlas
  desincronizadas; valorar impedirlo.
- Crear o borrar directamente filas de `IssueCreator` / `EditionCoverArtist` (p. ej. en un script:
  `IssueCreator.objects.filter(artist=a).delete()`, la forma natural de desvincular un artista ahora
  que `PROTECT` lo exige) no escribe versión del issue/edición. Las rutas de la app sí lo hacen.
  Añadir receptores `post_save`/`post_delete` como los de `CollectedIssue` (ojo: un `post_delete`
  desactiva el borrado rápido y `.remove()` fuera de un lote escribiría dos versiones).
- `IssueCreator.__str__` y `EditionCoverArtist.__str__` consultan la base (issue → publishing,
  `edition.short_name`); la página de "objetos protegidos" del admin hace ~2 consultas por crédito.
- Faltan tests de `process_image` para reemplazar una portada existente y para filas antiguas con
  miniatura pero sin imagen.
- No hay forma desde la interfaz de llegar al historial de una edición borrada (el servicio sí puede
  restaurarla).
- Los respaldos anteriores a la existencia del historial se restauran sin versión inicial; un comando
  `ensure_history` podría crearlas.

### General
- Dos archivos JS tienen las URLs `/ajax/...` escritas a mano en lugar de usar `{% url %}`:
  `comics/static/js/custom/collection_form_events.js`, `fill_release_date.js`.
- `edition_form` y `_clean_content` en `comics/views/manage_editions.py` son demasiado complejas
  (`# noqa: C901`); dividirlas ahora que hay tests.
- La re-evaluación de logros tras cambiar el contenido de una edición está duplicada en
  `manage_editions._schedule_owners_after_content_change` y `versions.schedule_edition_owners`.
- **Pasada de Ruff pendiente** de todo lo fusionado desde la última (el trabajo de
  `feat/user-profile` entró en `develop` sin ella). Hacerla una vez al terminar la feature actual y
  commitearla como `style: apply ruff format and fixes`.

## Limitaciones conocidas y seguridad

- **Sin SMTP:** no hay verificación de correo al registrarse ni recuperación de contraseña por correo.
- **Sin límite de intentos** en el login ni en el registro (fuerza bruta, registros de spam).
- **`/media/` se sirve sin autenticación:** cualquiera que tenga la URL (aleatoria, imposible de
  adivinar) puede ver las fotos de perfiles privados.
- **El tamaño de las subidas** se comprueba después de recibir el archivo; un límite duro debe ir en
  el servidor web o proxy.
- **Configuración de producción:** `DJANGO_DEBUG` vale `True` por defecto y `DJANGO_ALLOWED_HOSTS`
  vale `*` si no se definen las variables de entorno (`comic/settings.py`); un despliegue real debe
  definir ambas, además de `DJANGO_SECRET_KEY`.
- Las re-evaluaciones de logros que lanza Gestión se ejecutan dentro de la propia petición
  (`schedule_for_everyone`); vale para unos cientos de usuarios, más allá habría que pasarlas a una
  tarea en segundo plano.

## Decisiones de producto abiertas

- Los perfiles son **públicos por defecto**; el usuario quizá prefiera privados.
- Los mensajes de contraseña de Django en español usan "usted", mientras que la app usa "tú".
- Los superusuarios nuevos creados con `createsuperuser` solo entran en Coleccionistas (aun así
  acceden a Gestión).
- `speed_force` cuenta las compras que comparten fecha, así que catalogar de una sentada una
  colección antigua puede desbloquearlo.
