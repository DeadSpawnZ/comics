# Backlog

Trabajo pendiente, decisiones abiertas y limitaciones conocidas de Comi. Sirve para que una sesión de
trabajo nueva (humana o de IA) tenga contexto suficiente para retomar cualquier punto sin la
conversación que lo originó. Lo que ya está hecho se describe en el `README.md`, no aquí.

Cómo trabajar un punto: seguir `CLAUDE.md` (flujo backend con `backend-senior` → `code-reviewer`,
tests solo en MySQL, Ruff una vez por feature, migraciones ensayadas en `comic_refactor` antes de la
base real). Quitar el punto de este archivo en el mismo commit que lo resuelve.

Última actualización: 2026-11-16, en la rama `feat/mycomicshop-batch-import`.

## Primera tarea de la próxima sesión

### Probar la Cacería en el navegador

Feature nueva (2026-11-16), en `feat/mycomicshop-batch-import`. Tres tareas, todas APPROVED:
1 modelo `WantedEdition` + `0123` (2 rondas de revisión); 2 página Mis comics → Cacería, botón "Lo
busco" en lista y detalle de ediciones, nota editable en la página (1 ronda); 3 comprar una edición
buscada completa la marca (`completed_at`, conserva la nota) y crea una notificación
`hunt_completed` con la acción "Seguir buscándolo", que la reactiva sin tocar la compra (2 rondas).
Commiteado en `1fdc3eb` (feature) y `a22e8d6` (Ruff), sin push. Suite en MySQL: 1221 tests OK. La
migración `0123` (crea `comics_wantededition`, añade `notification.edition` y amplía la CHECK de
notificaciones) ya está aplicada en la base real (2026-11-16, copia previa en
`backups/comic-pre-0123-20261116_142810.sql`, ensayada antes en `comic_refactor`: aplicar, revertir
con una notificación de cacería presente, aplicar) y la imagen reconstruida. Falta: **ver las
páginas en el navegador** (nadie las ha visto: fila de la cacería, chips, desplegable de la nota,
icono `travel_explore`, botones en lista y detalle, la acción de la notificación en la página y
dentro del menú de la campana, el texto de la notificación en español).

### Probar los lotes de importación desde MyComicShop (commiteados, sin push)

Rama `feat/mycomicshop-batch-import` (sale de `develop` tras el PR #6; sin push). Spec en "Próximas
features → Lotes de importación desde MyComicShop". Todas las tareas están APPROVED y en staged
(2026-11-01/02): 1 modelos + `0122` (sin aplicar); 2 servicio del lote (`batches.py`); 3 alta de
ediciones en `comics/editions.py`; 4 creación de filas (`batch_creation.py`, "preseleccionar, pero
nunca inventar"); 5a página del lote (resolver nombres, crear, vincular, cerrar); 5b elegir/cambiar
publishing con recálculo y selección de filas; 5c valores del lote y de cada fila
(`comics/import_batch_values.py`); 6 casillas e "Importar seleccionadas" en el diálogo de series
(`catalog_series_dialog.js`, separado de `catalog_import.js`) y "Nuevo lote" en Gestión →
Importaciones. Suite MySQL: 1137 OK. No hay tests de JS: el diálogo no se ha probado en un navegador.

Commiteado en `55dfcc1` (feature) y `c316d32` (Ruff). La migración `0122` ya está aplicada en la base
real (2026-11-01, copia previa en `backups/comic-pre-0122-20261101_185727.sql`, ensayada antes en
`comic_refactor`) y la imagen reconstruida. Falta: probar en el navegador (crear un lote desde el
formulario de ediciones y desde Importaciones, resolver nombres, editar valores, crear, vincular);
push y PR a `develop`.

MINOR que quedaron de las revisiones (para después):
- En el formulario de ediciones, "Importar seleccionadas" navega a la página del lote y pierde lo que
  no se haya guardado del formulario; avisarlo en el diálogo o pedir confirmación si hay cambios.
- `manage_imports.py` importa `catalog_answer` y `series_dialog_data` de `manage_editions.py`;
  moverlos a `comics/views/utils.py` o a un `comics/views/catalog_api.py`.
- `series_dialog_data()` devuelve `dict[str, Any]`; usar un `TypedDict`.
- `comics/diagrams.py` no tiene componente para los servicios de primer nivel
  (`comics.import_batch_values`, `comics.editions`), así que el diagrama del README no los muestra.
- `row.is_edited` solo mira `EDITED_KEYS`, pero "Reset to proposal" borra todas las claves (también
  `cover_url`): revisarlo si algo empieza a escribir otras claves.
- El candado del lote se mantiene durante la descarga de cada portada: una acción enviada durante una
  creación espera (si pasa `innodb_lock_wait_timeout` se muestra "el lote está ocupado").
- Carrera conocida: dos transacciones que crean a la vez el mismo issue nuevo pueden escribir los
  créditos de historia de la segunda sobre el issue de la primera (app local, un usuario).
- `djangojs.po` tiene dos entradas que ya no usa ningún JS ("Creators not found in Comi (not added)…"
  y "Cover artists not found in Comi (not added)…").

### Terminar la tanda de integridad de datos (rama `chore/adjustments`)

Commiteada (bloqueo del borrado de compras vendidas, reglas de compra previa compartidas, bloqueo de
la reversión de serie/número de un issue con ediciones, versión del dueño al escribir filas de
créditos). La migración `0121` ya está aplicada en la base real (2026-10-31, copia previa en
`backups/comic-pre-0121-20261031_174524.sql`) y la imagen reconstruida. Falta probar en el navegador
los diálogos bloqueados de borrado (hay 15 compras de cómics vendidas; ningún collectable vendido) y
de reversión.

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
- **Publicar:** PR `develop` → `master` (`chore/adjustments` ya está en `develop` por el PR #6).
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
- **Para más adelante:** búsqueda por nombre (`search?q=` por POST, sin probar), importar como
  solicitudes de cambio. Importar varias variantes de golpe: ver "Lotes de importación desde
  MyComicShop".

### Lotes de importación desde MyComicShop (diseño aprobado el 2026-10-31, implementado: ver arriba)

Crear varias ediciones de una serie de MyComicShop (TID) con un paso de revisión antes de guardar.
Resuelve tres límites de hacerlo desde el formulario de una edición o en automático: cada edición
tiene sus propios artistas de portada; los créditos salen de una regex sobre texto libre y pueden
venir cortados o mal; y en automático no se puede configurar nada antes de guardar.

Rama: `feat/mycomicshop-batch-import` desde `develop`, **después** de fusionar el PR #6 (usa el modal
de variantes, "Vincular" y el TID de `chore/adjustments`).

Decisiones del usuario:
- El lote se guarda en la base (sobrevive a recargas; se puede dejar a medias).
- Las variantes "probables" se pueden vincular **o** crear como edición nueva (por si la coincidencia
  es falsa); salen sin marcar. Las que ya tienen su IVID (`existing`) no se pueden importar.
- Si el issue de un número ya existe, sus artistas de historia **no se tocan** (solo se muestran).
- Solo Gestión (`management_required`, Administrators) de momento.
- `ArtistAlias` (recordar correcciones de nombres entre lotes) va en la fase 2.

Datos que ya hay: una página de serie (`mycomicshop.fetch_series`, cacheada) trae todas las
variantes con su texto ya leído (`McsDetails`), así que el lote hace una sola petición a MyComicShop;
las portadas se descargan al crear cada edición (`catalog_cover_url`, `download_cover`). Los artistas
de historia son del issue (`Issue.creators`), los de portada de cada edición
(`Edition.cover_artists`); `SourceCredit.uncertain` marca los nombres adivinados y
`ArtistMatcher.match` separa los que no coinciden con ningún artista.

Flujo:
1. Modal de variantes: una casilla por variante y "Importar seleccionadas" crea el lote y lleva a su
   página. Límite propuesto: 30 variantes por lote (las portadas se descargan en la misma petición).
2. Página del lote (Gestión → Importaciones): valores del lote (publishing, formato, rareza, fecha de
   salida, notas) que heredan todas las filas; un grupo por número con su issue (existente o
   nuevo) y sus artistas de historia, una vez; una fila por variante con letra, impresión, ratio,
   tirada, exclusivas, precio, miniatura, artistas de portada, el texto original de MyComicShop y el
   aviso "posible coincidencia" con su "Vincular". Cada campo de fila hereda del lote salvo que se
   cambie. "Guardar cambios" guarda la revisión sin crear nada.
3. Nombres: cada nombre sin coincidencia o adivinado se resuelve **una vez por lote** (asociar a un
   artista existente, crear artista nuevo o descartar) y se aplica a todas sus filas. Una fila con
   nombres sin resolver no se puede crear.
4. "Crear seleccionadas": cada fila pasa por `EditionManageForm` (+ `IssueSectionForm` para los
   artistas de historia de un issue nuevo), con su propia transacción, `history_batch` y `LogEntry`.
   Las válidas se crean, las que fallan se quedan con su error, las creadas enlazan a su edición. Los
   artistas nuevos se crean antes, una vez. Al terminar, el TID se rellena en el publishing si estaba
   vacío (como en `link_mycomicshop_edition`).

Tareas (una por `backend-senior`, en orden):
1. Modelos y migración: `ImportBatch` (creado por, TID, título/año/editorial de la serie, publishing,
   valores del lote en JSON, resoluciones de nombres en JSON, estado abierto/cerrado, fechas),
   `ImportBatchNumber` (lote, número, issue existente o None, artistas de historia propuestos) e
   `ImportBatchRow` (lote, IVID único por lote, etiqueta, orden, propuesta en JSON, cambios en JSON,
   tipo de coincidencia y ediciones probables, seleccionada, estado pendiente/creada/error,
   errores, edición creada con `SET_NULL`). Sin historial.
2. Servicio `comics/catalog_sources/batches.py`: crear el lote desde un TID y una lista de IVIDs
   (una `fetch_series`; por variante `to_source_issue` + las piezas del mapper (`edition_values`), sin
   `build_proposal`, que vuelve a buscar el publishing y el issue; un solo `ArtistMatcher`; consultas
   fijas, no por variante), valores efectivos de una fila (lote + cambios), nombres
   pendientes y aplicar una resolución.
3. Sacar el alta de ediciones de la vista (`_save_edition` y la lógica de issue de `edition_form` en
   `comics/views/manage_editions.py`) a un servicio reutilizable, sin cambiar el comportamiento.
4. Crear las filas del lote con ese servicio (lo del paso 4 del flujo):
   `comics/catalog_sources/batch_creation.py` (`create_rows`, `link_row`). Los artistas nuevos se
   crean con el nombre corregido de la resolución (`new_name`) y la resolución pasa a "artist".
   "Preseleccionar, pero nunca inventar": si el catálogo no dice nada de impresión/formato/rareza,
   la fila toma lo que preselecciona la página de Gestión (1st, single issue, common); si dio un
   valor que Comi rechazó (7th+, tipo de serie sin formato; `proposal["rejected"]`), la fila falla
   en ese campo hasta que lo den los valores del lote o la fila. Fecha de salida y precio nunca se
   rellenan. La fila que crea el issue de un número lo deja como issue del número. Vincular no
   guarda su error en la fila (se responde al momento).
5. Vistas, URL y plantilla de la página del lote; lista de lotes en Gestión.
6. Modal: casillas, "Importar seleccionadas" y JS (separar antes el diálogo de series de
   `catalog_import.js`, que pasa de 900 líneas).

Fase 2: `ArtistAlias` (texto de origen → artista, como `EditorialAlias`) rellenado con las
resoluciones y usado por `ArtistMatcher`; "Editar completa" de una fila en un diálogo con todos los
campos de la edición.

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
- **`makemessages` daña el catálogo:** el comando documentado en el README (sección Traducciones),
  corrido sobre este árbol, reescribe y reordena todo `django.po` (unas 7,000 líneas) y
  `djangojs.po`, marca como obsoletas (`#~`) entradas que se siguen usando ("Community" y
  "Seniority", que `test_translations.py` comprueba, además de "Treasures", "Sagas", "Details" y una
  cadena de JS) y propone traducciones fuzzy equivocadas. Por eso las 14 cadenas de la Cacería se
  añadieron a mano al final de `django.po`. Averiguar por qué no extrae esas cadenas y arreglar el
  comando (o documentar el procedimiento manual) antes de volver a regenerar el catálogo.
- Cacería (menores de revisión):
  - El texto de ayuda "Up to 500 characters" de `WantedEditionNoteForm` lleva el límite escrito a
    mano; usar un msgid con marcador que lea `max_length` del modelo.
  - `comics/views/hunt.py` repite `PAGE_SIZE = 25`; importarla de `manage_editions` como hacen
    `collection_entries.py` y `manage_collectables.py`.
  - `hunt_list.html` copia la fila de `edition_list.html` (miniatura, título, metadatos, chip "You
    own") y el chip "Looking for it" está en tres plantillas; sacarlos a includes. La página de
    Cacería no muestra el chip de compilación que sí tiene la lista.
  - `my_comics.edition_detail` consulta `WantedEdition` con el ORM en la vista; añadir
    `WantedEditionQuerySet.is_wanted_by`. `owned_counts` es una consulta de dominio que vive en
    `views/my_comics.py` y ya la importa `hunt.py`; moverla al QuerySet de `Collection`.
  - La altura del `textarea` de la nota va en `attrs={"style": ...}` del formulario; pasarla a
    `.hunt-note-edit__form textarea` en `collections.css`.
  - Falta un test de que la nota se renderiza escapada (hoy lo está, por autoescape).
  - Guardar vacía una nota que ya estaba vacía muestra "Note removed."; comparar con
    `form.has_changed()`.
  - Si una nota se rechaza se pierde lo escrito (solo ocurre con una petición manipulada: el
    navegador limita a 500 caracteres y es igual o más estricto que el servidor).
  - Las marcas completadas (`completed_at`) no se pueden borrar desde la interfaz: se acumulan hasta
    que se borra la edición o el usuario. Inofensivo hoy; si molesta, borrarlas al dejar de buscar o
    mostrarlas como historial de cacerías completadas.
  - `set_wanted(True)` reactiva una marca completada con una actualización condicionada al estado
    leído, sin test de concurrencia (se consideró desproporcionado).
  - `NotificationAdmin` no tiene `change_request` en `raw_id_fields`: el formulario del admin carga
    un select con todas las solicitudes (ya estaba así antes de la Cacería).
- El listado de ediciones de Mis comics calcula `recent` (las últimas 10 ediciones, en
  `edition_catalog_context`) aunque su plantilla no lo muestra: una consulta de más en la primera
  página.
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
- `edition_form` en `comics/views/manage_editions.py`, `evaluate` en
  `comics/achievements/engine.py`, `edition_values` en `comics/catalog_sources/mapper.py`,
  `resolved_credits` en `comics/catalog_sources/batches.py` y `_create_new_artists_once` en
  `comics/catalog_sources/batch_creation.py` son demasiado complejas (`# noqa: C901`); dividirlas
  ahora que hay tests.
- Las excepciones de `comics/catalog_sources/errors.py` y `InvalidTransition` no llevan el sufijo
  `Error` (`# noqa: N818`); renombrarlas si se toca esa zona.

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
