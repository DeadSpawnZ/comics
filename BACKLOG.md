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
- **Desplegado en local el 2026-10-11 (todo lo commiteado hasta `98cab5e`):** registro de actividad al
  aprobar solicitudes, paginación del historial, miniatura sin imagen, artistas protegidos (`0112`)
  e integración con Metron/Comic Vine (`0113`). Respaldo `backups/comic-20261010-220738.sql`, imagen
  anterior `comi:pre-catalog`. Falta probar en el navegador el importador y el panel de identificadores.
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

### Catálogos externos: Metron (principal) y Comic Vine (respaldo)
- **Hecho y desplegado (commit `98cab5e`):** precarga del formulario de
  edición de Gestión desde Metron o Comic Vine (`comics/catalog_sources/`, endpoint
  `/gestion/api/catalogo/`), ids externos en `Issue` (`metron_id`, `comicvine_id` únicos; `gcd_id`
  indexado; migración `0113`, aditiva), sección "Datos del issue" en el formulario de edición
  (ids, sinopsis y creadores) y panel "Números de identificación". Credenciales en `.env`
  (`METRON_USERNAME`, `METRON_PASSWORD`, `COMICVINE_API_KEY`), cargado como `env_file` opcional del
  servicio `web`.
- **Hecho en la rama (commit `e93b119`, desplegado el 2026-10-12): crear publishing, título y editorial desde el
  formulario de edición.** Diálogos apilados ("Crear publishing" → "+" título / "+" editorial) que
  envían a `/gestion/api/publishings/nuevo/`, `/gestion/api/titulos/nuevo/` y
  `/gestion/api/editoriales/nueva/` (`comics/views/manage_publishings.py`); el importador los
  precarga con la serie de Metron (fecha = la del primer issue: tienda, si no portada, si no 1 de enero
  del año de inicio con aviso; Comic Vine siempre cae al año de inicio). El título nunca se elige solo.
  Ids de serie en `Publishing` (`metron_series_id`, `comicvine_volume_id` únicos; `gcd_series_id`
  indexado; migración `0114`, aditiva, aplicada; respaldo `backups/comic-20261011-232724.sql`, imagen anterior `comi:pre-dialogs`), con
  panel de números de identificación en la página del publishing; el mapeador empareja publishings
  por esos ids primero (también por el id de Comic Vine que da la serie de Metron, lo que cuesta una
  petición más, cacheada; si apuntan a publishings distintos gana Metron con un aviso). En el
  diálogo, idioma y fecha de inicio empiezan vacíos y son obligatorios. Borrador del formulario en
  `sessionStorage` con oferta de recuperarlo tras recargar (`js/custom/form_draft.js`). **Falta probarlo en el navegador.**
- **Hecho en la rama (sin commit): ayudas del importador.** Campos obligatorios de los diálogos con
  asterisco y, mientras están vacíos, contorno y pista (`RequiredHintsMixin`, `aria-required` /
  `aria-describedby`); **alias de editoriales** (`EditorialAlias`, migración `0115`, aditiva, sin
  aplicar: clave normalizada única, en el admin como inline de la editorial y en el respaldo por
  clave natural) con los que el mapeador empareja la editorial, y que el resumen de la importación
  y el diálogo de publishing ofrecen recordar al elegir una editorial existente
  (`/gestion/api/editoriales/alias/nuevo/`); **crear artistas que faltan** desde el resumen ("+" por
  artista y "Crear todos", `/gestion/api/artistas/nuevos/`, idempotente por nombre) y "+" junto a
  los selectores de artistas. **Falta probarlo en el navegador.**
- **Siguiente:** **importación masiva** de un issue o de una serie completa convertida en
  **solicitudes de cambio**, reutilizando el mismo mapeador (`comics/catalog_sources/mapper.py`).
- **Limitaciones conocidas de la v1:** la lista de issues de una serie en Metron solo se lee en su primera página (comprobado el
  2026-10-11 con dos series: viene ordenada por número, el #1 primero); el país de la editorial solo lo da Metron; Metron lista juntos a los artistas de todas las portadas (para
  la A se proponen los que también dibujan el interior, para las variantes los que aparecen en su
  nombre); las letras de variante (B, C…) son una sugerencia; las impresiones a partir de la 7.ª no
  existen en Comi; el límite propio de peticiones por minuto se cuenta en la caché local de cada
  proceso. Comic Vine no tiene variantes, precio, idioma ni número de volumen.
- **Menores de la revisión de "crear publishing" (pendientes):**
  - Con Comic Vine (no da número de volumen) el diálogo conserva la serie por defecto `1st`; valorar
    tratarla como el idioma y la fecha (vacía y obligatoria).
  - El borrador guarda el campo oculto `next` (añadirle `data-draft-skip`) y el texto del aviso de
    recuperación ("antes de recargar") no es exacto cuando se vuelve otro día a la misma página.
  - Al apilar diálogos, el de abajo sigue en el árbol de accesibilidad: marcarlo `inert`.
  - "Duplicated publishing" de `Publishing.validate_duplicates` sin traducir (solo en carreras).
  - Tipos: `_create(...) -> Model | None` genérico con `TypeVar`; `UniqueNameMixin` con `Any`;
    mensajes de duplicado repetidos en las vistas; `Publishing.IDENTIFIER_FIELDS` e
    `Issue.IDENTIFIER_FIELDS` sin usar.
- **Desplegado el 2026-10-12 (commit `a724dde`):** alias de editoriales (migración `0115`, aplicada),
  alta de artistas que faltan desde la importación y marcado de campos obligatorios en los diálogos.
  Respaldo `backups/comic-20261012-001048.sql`, imagen anterior `comi:pre-aliases`.
- **Alias de artistas (aplazado por el usuario el 2026-10-12):** igual que los de editoriales. El
  emparejamiento ya ignora mayúsculas, acentos y espacios, pero no nombres distintos de la misma
  persona entre fuentes ("Jim Lee" / "James Lee", "J. Scott Campbell" / "J Scott Campbell").
  Propuesta: modelo `ArtistAlias` con clave normalizada (como `EditorialAlias`), emparejar por nombre
  o alias, y en el resumen de la importación elegir un artista existente con "Recordar como alias".
- **Menores de la revisión de alias de editoriales y alta de artistas (pendientes):**
  - Admin: dos alias nuevos en el mismo guardado que se normalizan igual (o que la intercalación de
    MySQL considera iguales) dan un `IntegrityError` (500); añadir un `clean()` al formset del inline
    que rechace `name_key` repetidos. Renombrar una editorial a uno de sus alias, borrando ese alias
    en el mismo guardado, requiere dos guardados.
  - Un artista creado con el "+" del selector no se refleja en el resumen de la importación (sigue
    como "no encontrado" hasta reimportar); emitir un evento `comi:artists-created` que
    `catalog_import.js` escuche.
  - La clave del alias se trunca a 255 caracteres al guardar, pero las búsquedas usan la clave
    completa; unificar en un helper o rechazar nombres demasiado largos.
  - `NameListField` acepta nombres formados solo por marcas combinantes (clave vacía); rechazarlos.
  - Código duplicado: índice de artistas por `name_key` (`ensure_named` y `ArtistMatcher`), envío
    POST con CSRF y lectura de errores en `catalog_import.js` y `publishing_dialogs.js`;
    `max_length` fijo en `EditorialAliasForm`; `RepeatableTextInput.format_value` sin uso real.
- Impresión, ratio (salvo cuando el nombre de la variante lo dice, p. ej. "1:25"), exclusivas de
  tienda/evento y país siguen siendo manuales.
- **Portada importada descargada de nuevo al reenviar:** la descarga ocurre en `clean()` del
  formulario de edición, así que si el formulario vuelve con errores (p. ej. de la sección del issue)
  la portada se descarga otra vez en cada envío. Valorar guardarla temporalmente (o en caché) la
  primera vez, o descargarla solo cuando todo el formulario es válido.

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
