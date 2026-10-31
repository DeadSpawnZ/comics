# Backlog

Trabajo pendiente, decisiones abiertas y limitaciones conocidas de Comi. Sirve para que una sesión de
trabajo nueva (humana o de IA) tenga contexto suficiente para retomar cualquier punto sin la
conversación que lo originó. Lo que ya está hecho se describe en el `README.md`, no aquí.

Cómo trabajar un punto: seguir `CLAUDE.md` (flujo backend con `backend-senior` → `code-reviewer`,
tests solo en MySQL, Ruff una vez por feature, migraciones ensayadas en `comic_refactor` antes de la
base real). Quitar el punto de este archivo en el mismo commit que lo resuelve.

Última actualización: 2026-10-31, en la rama `chore/adjustments`.

## Primera tarea de la próxima sesión

### Terminar la tanda de integridad de datos (rama `chore/adjustments`)

Commiteada (bloqueo del borrado de compras vendidas, reglas de compra previa compartidas, bloqueo de
la reversión de serie/número de un issue con ediciones, versión del dueño al escribir filas de
créditos). Falta, en orden: aplicar la migración `0121` a la base real (sin DDL, ensayada en
`comic_refactor`; antes, copia con `mysqldump`), reconstruir la imagen y probar en el navegador los
diálogos bloqueados de borrado y de reversión.

Decisión pendiente del usuario: los receptores de filas de créditos también se aplican a
`CollectedIssue`, lo que cambia dos casos de script fuera de un lote
(`edition.collected_issues.remove(x)` escribe 1 versión en vez de 2;
`edition.collected_entries.all().delete()` con N filas escribe 1 en vez de N). Las rutas de la app
no cambian.

### Probar la fecha de salida propuesta

**Probar en el navegador la fecha de salida propuesta desde el publishing** (commit `7af8afd`).
Antes hay que reconstruir la imagen, porque los estáticos van dentro de ella. El cambio no tiene
migraciones.

En el formulario de alta de ediciones de Gestión (`/gestion/ediciones/nueva/`), la fecha de salida
debe tomar la fecha de inicio del publishing en estos casos:
- al elegir un publishing en el selector;
- al cambiar a otro publishing;
- al crear uno con el diálogo "+";
- al entrar desde "Nueva edición" de la página de un publishing.

En cambio, la fecha **no** debe cambiar en estos casos:
- si la fecha se escribió a mano;
- si la puso el importador de Metron/Comic Vine;
- si viene de un borrador recuperado;
- si el formulario volvió con errores;
- si se está editando una edición existente.

La lógica está en `comics/static/js/custom/edition_form.js` (sección "Release date"). Las fechas
salen de `form_data.publishingDates` y `fillReleaseDate` en `comics/views/manage_editions.py`.

## En curso

### Solicitudes de cambio y catálogos externos (en `develop`)

Todo está en `develop` (PR #5, que también trajo `chore/codegraph` y `feat/mycomicshop-import`) y
desplegado en local. Falta:

- **Probar en el navegador:**
  - el importador de Metron/Comic Vine y el panel "Números de identificación" de issues y
    publishings;
  - los diálogos para crear publishing, título y editorial desde el formulario de edición, y el
    borrador del formulario (`js/custom/form_draft.js`);
  - las ayudas del importador: campos obligatorios marcados, recordar alias de editoriales y crear
    los artistas que faltan.
- **Publicar:** PR `develop` → `master`. Antes, llevar a `develop` la rama `chore/adjustments`
  (collectables con catálogo de artículos, coincidencias y "Vincular" de MyComicShop, TID en
  `Publishing`; migraciones `0117`–`0120`, ya aplicadas en la base real).
- **Confirmar con el usuario** tres decisiones que tomó el implementador: nadie revisa sus propias
  solicitudes; los revisores no pueden rechazar una solicitud ya validada; el autor no ve los nombres
  de quienes la revisaron.

## Próximas features

### Solicitudes de cambio, siguientes iteraciones
- Solicitudes para **Connectings**.
- **Strikes** para los usuarios cuyas solicitudes se rechacen por infundadas (cada solicitud guarda
  quién decidió y el resultado, así que los strikes se pueden calcular a partir de ellas).
- Ampliar lo que se puede proponer (recortes de la v1): imágenes de portada, el contenido de las
  compilaciones y, en un issue, algo más que sinopsis y creadores (hoy la serie y el número se
  corrigen desde la edición).

### Catálogos externos
- **Importación masiva** de un issue o de una serie completa, convertida en **solicitudes de
  cambio**, reutilizando el mapeador (`comics/catalog_sources/mapper.py`).
- **Alias de artistas** (aplazado por el usuario el 2026-10-12), como los de editoriales. El
  emparejamiento ya ignora mayúsculas, acentos y espacios, pero no nombres distintos de la misma
  persona entre fuentes ("Jim Lee" / "James Lee", "J. Scott Campbell" / "J Scott Campbell").
  Propuesta: modelo `ArtistAlias` con clave normalizada (como `EditorialAlias`), emparejar por nombre
  o alias, y en el resumen de la importación elegir un artista existente con "Recordar como alias".
- **Portada importada descargada de nuevo al reenviar:** la descarga ocurre en `clean()` del
  formulario de edición, así que si el formulario vuelve con errores (p. ej. de la sección del issue)
  la portada se descarga otra vez en cada envío. Valorar guardarla temporalmente (o en caché) la
  primera vez, o descargarla solo cuando todo el formulario es válido.
- Con Metron y Comic Vine, impresión, ratio (salvo cuando el nombre de la variante lo dice, p. ej.
  "1:25"), exclusivas de tienda/evento y país siguen siendo manuales (MyComicShop sí los propone).

### Importar desde MyComicShop (en `develop`)

Implementado (ver README) y en `develop` con el PR #5. Falta:

- **Probar en el navegador** (la migración `0116` ya está aplicada en la base real) con los TID `57389539`
  (Batman Spawn) y `65211284` (Vampirella X Witchblade): modal, teclado, importar una RI, una
  exclusiva con tirada y una 2.ª impresión, re-importar otra portada, portada ya existente
  deshabilitada, errores (TID `1`, texto no numérico) y que Metron/Comic Vine sigan igual.
- **Fragilidad:** solo funciona `POST https://www.mycomicshop.com/search?TID=<id>`; un `GET` (también
  a `search?q=`) devuelve un reto JS de Imperva. Si el POST deja de funcionar: subir el HTML guardado
  desde el navegador y pasarlo por el mismo parser, o valorar Playwright (~400 MB más en la imagen).
  **Límite acordado con el usuario:** nada de plugins "stealth", falseo de huella ni proxies.
- **Límites conocidos del parser** (`comics/catalog_sources/mycomicshop.py`):
  - Formato "(W) (A) (CA)": si tres o más variantes de un número tienen artistas de portada con el
    mismo apellido ("Jae Lee", "Jim Lee", "Stan Lee") y ninguna usa el formato de frases, el nombre se
    corta en el apellido sin marcarse como dudoso.
  - Una nota que termina en una palabra de una letra ("… Variant Cover B. Written by X.") no se corta
    ahí y el guionista acaba dentro de la nota.
  - Si un crédito en frase va antes de los códigos ("Cover by X. (W) A (A) B …"), los códigos acaban
    en la sinopsis.
  - "48 pages, full color. Rated Teen" se queda al final de la sinopsis.
  - Una exclusiva conjunta de varias tiendas que pase de 60 caracteres se descarta con aviso.
- **Menores:** re-importar en una edición su propio IVID avisa de que ya existe una edición con ese
  ID (la consulta no sabe qué edición está abierta); el panel "Identification numbers" no muestra el
  IVID; la caché de la serie y el `throttle` usan la `LocMemCache` por proceso (sin `CACHES`
  configurado), así que con varios workers la búsqueda tras el modal puede volver a pedir la página.
- **Coincidencia probable + "Vincular"** (rama `chore/adjustments`, ver README): falta probarlo en el
  navegador con el TID `19609941` (Image United). Límites: no compara qué tienda o evento es (solo
  si es exclusiva o no) ni normaliza números ("01" ≠ "1"); sin publishing emparejado (o con varios)
  no hay candidatas; las exclusivas casi nunca tendrán candidata, porque el sufijo de MyComicShop
  ("B&N", "CUSTOM.B") rara vez coincide con la letra de Comi (solo se pierden coincidencias, nunca
  se inventan). `catalog_import.js` pasa de 900 líneas: separar el diálogo de series en su propio
  módulo.
- **TID en `Publishing`** (rama `chore/adjustments`, migración `0120` ya aplicada en la base real,
  ver README). Falta: guardar la edición importada desde MyComicShop en un publishing ya existente
  (envío del formulario) no rellena el TID; habría que llevarlo en un campo oculto del formulario de
  ediciones. El enlace del panel de identificadores abre `search?TID=` por GET (sin probar si el
  reto de Imperva lo deja pasar en un navegador real).
  Menores de revisión: un publishing emparejado por título que ya tiene **otro** TID (otra serie de
  MyComicShop) se acepta igual y el modal ofrece sus ediciones como probables (descartarlo o avisar;
  Metron/Comic Vine se comportan igual); `link_mycomicshop_edition` devuelve `(Edition, bool, bool)`
  (pasar a un `NamedTuple`); la URL de la serie está duplicada en `Publishing` y
  `mycomicshop.series_url` (un test la vigila); en `test_publishing_mycomicshop_tid.py`, helpers
  con `Any` y un parche global de `QuerySet.exists`.
- **Para más adelante:** búsqueda por nombre (`search?q=` por POST, sin probar), importar varias
  variantes de golpe o como solicitudes de cambio (enlaza con la "Importación masiva" de arriba).

### Logros que esperan otra feature
- **Popular / Super Popular** (20 / 100 seguidores): necesitan un sistema de seguidores.
- **Logros de collectables:** los collectables ya tienen dueño (`Collectable.collector`, sobre el
  catálogo `Article`), así que se pueden crear logros de collectables por usuario; aún no hay ninguno.

### Evaluación diaria de logros (aplazada por el usuario)
Los logros por fecha (Veterano) solo se comprueban al iniciar sesión o con `award_achievements`. El
usuario **no** quiere programadores del sistema (Programador de tareas de Windows / cron del host):
cualquier tarea periódica debe vivir dentro de la app o del stack de Docker. Opciones comentadas:
evaluar las reglas por fecha en la primera visita del día de cada usuario (recomendada) o un servicio
`scheduler` en `docker-compose`. Al hacerlo, actualizar la sección "Logros" del README.

## Deuda técnica

### Historial (de la revisión de la tarea 1)
- Revertir una edición puede restaurar un issue borrado; si uno de los creadores de ese issue ya no
  existe, el mensaje de rechazo menciona "Creators", lo que confunde en la reversión de una edición.
- `IssueCreator.__str__` y `EditionCoverArtist.__str__` consultan la base (issue → publishing,
  `edition.short_name`); la página de "objetos protegidos" del admin hace ~2 consultas por crédito.
- Faltan tests de `process_image` para reemplazar una portada existente y para filas antiguas con
  miniatura pero sin imagen.
- No hay forma desde la interfaz de llegar al historial de una edición borrada (el servicio sí puede
  restaurarla).
- Versión del dueño al escribir filas de créditos (menores de revisión):
  - `VERSIONED_RELATION_ROWS` (`comics/signals.py`) copia a mano los `m2m_fields` de
    `TrackedHistory`; derivarlo de `TrackedHistory.tracked` + `versioned_m2m_fields()`.
  - Si un `remove()`/`clear()` falla fuera de un lote, su marcador queda vivo en el hilo y silencia
    borrados directos posteriores de ese dueño; limpiarlo en el siguiente `pre_remove`/`pre_clear`
    o documentar el límite en `note_manager_removal`.
  - Tipos: `relation_owner_field -> ForeignKey`; `origin` como `Model | QuerySet[Model] | None`;
    helpers del test sin `Any`.
  - `relation_row_saving` hace un SELECT extra aunque `update_fields` no incluya el FK del dueño.
  - El test de consultas (`assertLess(len(queries), 20)`) debería comparar 1 vs 5 créditos.
- Los respaldos anteriores a la existencia del historial se restauran sin versión inicial; un comando
  `ensure_history` podría crearlas.
- **Renombrar un issue desde el admin:** `IssueAdmin` deja cambiar `publishing`/`number` de un issue
  al que siguen ediciones, lo que las desincroniza (Gestión, las solicitudes y las importaciones no
  lo permiten). Además, tras un renombrado así, revertir una edición a una versión anterior restaura
  el número viejo con el mismo `issue` (`restoring=True` no pasa por `assign_default_issue`) y la
  desincroniza. Propuesta: campos de solo lectura en el admin si `following_editions_count() > 0`,
  y en la reversión de ediciones rechazarla o volver a asignar el issue por defecto.
- Bloqueo de reversiones de issues (menores de revisión): la regla está escrita dos veces en
  `comics/versions.py` (`revert_to_version` y `version_entries`; sacar un helper); `version_entries`
  pasa un `Issue | None` a `changes_issue_identity` y lee la fila del issue dos veces; falta un test
  con una edición cuyo número tiene espacios ("5 ").

### Catálogos externos (menores de revisión)
- Con Comic Vine (no da número de volumen) el diálogo de publishing conserva la serie por defecto
  `1st`; valorar tratarla como el idioma y la fecha (vacía y obligatoria).
- El borrador guarda el campo oculto `next` (añadirle `data-draft-skip`) y el texto del aviso de
  recuperación ("antes de recargar") no es exacto cuando se vuelve otro día a la misma página.
- Al apilar diálogos, el de abajo sigue en el árbol de accesibilidad: marcarlo `inert`.
- "Duplicated publishing" de `Publishing.validate_duplicates` sin traducir (solo en carreras).
- Admin: dos alias nuevos en el mismo guardado que se normalizan igual (o que la intercalación de
  MySQL considera iguales) dan un `IntegrityError` (500); añadir un `clean()` al formset del inline
  que rechace `name_key` repetidos. Renombrar una editorial a uno de sus alias, borrando ese alias en
  el mismo guardado, requiere dos guardados.
- Un artista creado con el "+" del selector no se refleja en el resumen de la importación (sigue como
  "no encontrado" hasta reimportar); emitir un evento `comi:artists-created` que
  `catalog_import.js` escuche.
- La clave del alias se trunca a 255 caracteres al guardar, pero las búsquedas usan la clave
  completa; unificar en un helper o rechazar nombres demasiado largos.
- `NameListField` acepta nombres formados solo por marcas combinantes (clave vacía); rechazarlos.
- Tipos: `_create(...) -> Model | None` genérico con `TypeVar`; `UniqueNameMixin` con `Any`;
  mensajes de duplicado repetidos en las vistas; `Publishing.IDENTIFIER_FIELDS` e
  `Issue.IDENTIFIER_FIELDS` sin usar.
- Código duplicado: índice de artistas por `name_key` (`ensure_named` y `ArtistMatcher`), envío POST
  con CSRF y lectura de errores en `catalog_import.js` y `publishing_dialogs.js`; `max_length` fijo
  en `EditorialAliasForm`; `RepeatableTextInput.format_value` sin uso real.

### General
- Bloqueo del borrado de compras vendidas (menores de revisión): `collectable_delete` pasa a
  `date_format` el resultado de `first_sale_date` sin comprobar `None` (hoy no ocurre, porque solo
  una venta protege un collectable; devolver el mensaje genérico si pasa); el texto "no se puede
  borrar… se vendió el …" se arma igual en `collection_delete` y `collectable_delete` (extraer un
  helper en `views/utils.py`); `delete_dialog.html` lee `event.relatedTarget.dataset` sin
  comprobar que exista (abrir el diálogo desde JS sin botón fallaría).
- **Compra ya vendida editable:** las reglas de compra previa (`PreviousTradeRules`) solo se
  comprueban desde la venta. Una compra ya vendida todavía puede cambiar de coleccionista, de
  edición/artículo o pasar a venta, y su venta queda apuntando a una compra inválida (al volver a
  guardarla falla o pierde el vínculo). Rechazar esos cambios si la pieza tiene `next_trades`.
- Reglas de compra previa (menores de revisión): los `clean()` de `CollectionEntryForm` y
  `CollectableEntryForm` son idénticos (sacar un mixin de formulario); en el admin,
  `CollectionForm.filter_previous_trade` usa el coleccionista y la edición guardados, no los
  enviados, así que cambiar ambos y elegir una compra del nuevo da "Select a valid choice";
  `PreviousTradeRules` no declara los atributos que usa (como hace `CurrentValuesMixin`); el test de
  bloqueo compara el SQL exacto; si la carrera se detecta en el `save()` del admin, sale un 500 en
  vez de un error de formulario.
- Collectables: el autocompletado de "compra previa" del admin solo muestra el nombre del artículo,
  así que varias piezas del mismo artículo se ven iguales.
- Collectables, pendientes de la feature:
  - Los coleccionistas solo pueden dar de alta sus piezas; editarlas o borrarlas solo se puede desde
    Gestión (igual que las piezas de cómics).
  - `Article` no tiene miniatura: las listas cargan la imagen completa (en diferido). Añadir una si
    el catálogo crece.
  - Gestión no puede quitar la imagen de un artículo, solo reemplazarla (`FileInput`).
  - Código repetido: las pestañas por tipo de operación (`TYPE_TABS` y sus contadores) están copiadas
    entre `manage_collectables.collectable_list` y `collection_entries.collection_list`, y
    `manage_collectables` importa constantes de otros tres módulos de vistas; moverlas a
    `views/utils.py` o a `TradeChoices`.
- Reemplazar o borrar la imagen de una edición o de un artículo deja el archivo viejo en `media/`.
- Menores de la división de `models.py` / `forms.py` en paquetes: `_random_name`
  (`comics/models/base.py`) ya se usa desde dos módulos, renombrarlo a `random_name`;
  `assertNoLogs("comics.models")` en `test_edition_images.py` podría apuntar a
  `comics.models.catalog`; las referencias `#: comics/models.py:NNN` / `comics/forms.py:NNN` del `.po`
  se regenerarán con el próximo `makemessages`. El orden de imports de `comics/models/__init__.py`
  (`# isort: off`) reproduce el orden de registro de los modelos: no reordenarlo.
- `ReadingArcForm` todavía duplica el bucle de estilos de los widgets que ahora está en
  `MaterialFieldsMixin`.
- Dos archivos JS tienen las URLs `/ajax/...` escritas a mano en lugar de usar `{% url %}`:
  `comics/static/js/custom/collection_form_events.js`, `fill_release_date.js`.
- `edition_form` y `_clean_content` en `comics/views/manage_editions.py`, `evaluate` en
  `comics/achievements/engine.py` y `_edition_values` en `comics/catalog_sources/mapper.py` son
  demasiado complejas (`# noqa: C901`); dividirlas ahora que hay tests.
- Las excepciones de `comics/catalog_sources/errors.py` y `InvalidTransition` no llevan el sufijo
  `Error` (`# noqa: N818`); renombrarlas si se toca esa zona.
- La re-evaluación de logros tras cambiar el contenido de una edición está duplicada en
  `manage_editions._schedule_owners_after_content_change` y `versions.schedule_edition_owners`.

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
- **Catálogos externos (v1):** de Metron solo se lee la primera página de la lista de issues de una
  serie (comprobado el 2026-10-11: viene ordenada por número, el #1 primero); el país de la
  editorial solo lo da Metron; Metron lista juntos a los artistas de todas las portadas (para la A se
  proponen los que también dibujan el interior, para las variantes los que aparecen en su nombre);
  las letras de variante (B, C…) son una sugerencia; las impresiones a partir de la 7.ª no existen en
  Comi; el límite propio de peticiones por minuto se cuenta en la caché local de cada proceso. Comic
  Vine no tiene variantes, precio, idioma ni número de volumen.

## Decisiones de producto abiertas

- Los perfiles son **públicos por defecto**; el usuario quizá prefiera privados.
- Los mensajes de contraseña de Django en español usan "usted", mientras que la app usa "tú".
- Los superusuarios nuevos creados con `createsuperuser` solo entran en Coleccionistas (aun así
  acceden a Gestión).
- `speed_force` cuenta las compras que comparten fecha, así que catalogar de una sentada una
  colección antigua puede desbloquearlo.
