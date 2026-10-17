# Backlog

Trabajo pendiente, decisiones abiertas y limitaciones conocidas de Comi. Sirve para que una sesión de
trabajo nueva (humana o de IA) tenga contexto suficiente para retomar cualquier punto sin la
conversación que lo originó. Lo que ya está hecho se describe en el `README.md`, no aquí.

Cómo trabajar un punto: seguir `CLAUDE.md` (flujo backend con `backend-senior` → `code-reviewer`,
tests solo en MySQL, Ruff una vez por feature, migraciones ensayadas en `comic_refactor` antes de la
base real). Quitar el punto de este archivo en el mismo commit que lo resuelve.

Última actualización: 2026-10-17, en la rama `feat/mycomicshop-import`.

## Primera tarea de la próxima sesión

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

### Solicitudes de cambio y catálogos externos (rama `feat/change-requests`)

Todo está commiteado y desplegado en local. Falta:

- **Probar en el navegador:**
  - el importador de Metron/Comic Vine y el panel "Números de identificación" de issues y
    publishings;
  - los diálogos para crear publishing, título y editorial desde el formulario de edición, y el
    borrador del formulario (`js/custom/form_draft.js`);
  - las ayudas del importador: campos obligatorios marcados, recordar alias de editoriales y crear
    los artistas que faltan.
- **Cerrar la rama:** PR `feat/change-requests` → `develop` → `master`.
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
- Impresión, ratio (salvo cuando el nombre de la variante lo dice, p. ej. "1:25"), exclusivas de
  tienda/evento y país siguen siendo manuales.

### Importar desde MyComicShop (rama `feat/mycomicshop-import`)

**Objetivo:** importar los issues y variantes que no existen en Metron ni en Comic Vine (retailer
incentive, exclusivas de tienda o convención…). La app muestra las variantes de una serie de
MyComicShop en una vista propia y el usuario elige cuál importar. No se trata de rastrear el
catálogo: cada consulta la lanza el usuario a mano.

**Estado de la rama:** sale de `chore/codegraph`, porque el usuario quiere probar codegraph con los
agentes en esta feature, y lleva `feat/change-requests` fusionada (commit de merge `70f6cec`): el
importador de Metron/Comic Vine en el que se apoya esta feature solo existe en esa rama.

**Hallazgos (2026-10-17):**
- `POST https://www.mycomicshop.com/search?TID=<id>`, sin cuerpo ni cookies, devuelve 200 y
  `text/html` de ~226 KB. La página se genera en el servidor (ColdFusion detrás de Imperva) y no trae
  JSON ni datos embebidos para el JS. Es el mismo método que usa el formulario de la propia página.
- Un `GET` a la misma URL, o a `search?q=…`, devuelve en cambio un reto JS de Imperva (212 bytes, que
  cargan `/_Incapsula_Resource`), aunque se manden cabeceras de navegador y cookies. Que el POST
  funcione depende de cómo tienen configurado Imperva y puede dejar de funcionar sin aviso.
- `robots.txt` permite `/search` a `*`. Solo bloquea cart, account, login, wantlist, auctions,
  newlistings, restocked, etc.
- No hay paginación: todas las variantes de la serie llegan en una sola página.
- Estructura: `#resultstab > ul.results-detailed > li.issue`, un `li` por variante:
  - `<a name="<IVID>">`. En `.imgcol` va el texto "Issue #1A" y la portada: `a.fancyboxthis[href]`
    apunta a `https://media.mycomicshop.com/n_iv/600/<imgid>.jpg`, y la miniatura usa `/n_iv/120/`
    con el mismo id.
  - `.othercolleft .title`: serie con el año y la editorial abreviada, p. ej. "(2023 IDW)", más
    `span.issuenum strong` con "#1A". En `.indentrow` van los tags (enlaces `IVGroupID`).
  - `.othercolright`: la fecha va en el enlace `minyrmo=YYYYMM` (solo mes y año) y la editorial en
    `pl=<nombre>`.
  - `#tab-addtocart-<IVID>`: si hay stock, una `table.issuestock` con una celda de microdatos
    schema.org `Product` por copia en venta (`sku` = ItemID, `price`, grado). Es stock de la tienda
    y no interesa al catálogo. Si no hay stock, un `<p>` con "not in stock". Después viene un `<p>` de
    **texto libre** con esta forma: [nota de variante, p. ej. "Limited 1 for 25 Retailer Incentive
    Variant Cover." o "Carnivore Comics Exclusive Cover by … - Limited to 1200 copies."] +
    "Written by X. Art by Y. Cover by Z." + sinopsis + "Cover price $3.99.". Los créditos y el
    precio hay que sacarlos con regex.
  - Se ignoran `#tab-ebaylistings-<IVID>` y las pestañas AJAX `/ajax/tab_wantlist?IVID=` y
    `/ajax/tab_census?IVID=`.
- IDs: `TID` es la serie, `IVID` el issue o variante, `ItemID` una copia en venta e `IVGroupID` un tag.
- Sufijos de variante: `A`/`B`/`C` son portadas normales; `RI.A` o `RIA` son retailer incentive
  (el ratio viene en la nota); `NYCC`, `REIQ`, `CARNIVORE.B`, `JOLZAR.A`, `XPOSURE`… son exclusivas.
  La nota de variante dice qué es cada una.
- Serie de ejemplo: `TID=58451662` (Teenage Mutant Ninja Turtles vs. Street Fighter, 2023 IDW,
  40 variantes de los números 1 a 5). Para volver a bajar el HTML:
  `curl -s -X POST -A "<UA de Chrome>" -o mcs.html "https://www.mycomicshop.com/search?TID=58451662"`.
- Sin verificar: si existe una URL por variante (p. ej. `POST search?IVID=<id>`) y cómo es la página
  de resultados de búsqueda por nombre.
- Texto libre (revisado con `TID=57389539` Batman Spawn 2023 DC, `TID=65211284` Vampirella X
  Witchblade Special 2026 Dynamite y `TID=58451662`):
  - Créditos en dos formatos. Frases: "Written by X. Art by Y (pencils), Z (inks) and W (inks
    assist). Cover by A and B." (también "Story by", "Cover by A, B"; nombres con iniciales como
    "J. Scott Campbell" o "Freddie E. Williams II"). Códigos de solicitación: "(W) X (A) Y (CA) Z
    (C) W" sin ningún separador entre el último nombre y la sinopsis ("(CA) Stjepan Sejic Two titans
    of comics…"); la sinopsis empieza igual en las variantes del mismo número.
  - La nota de variante va antes de los créditos: "Limited 1 for 25 Retailer Incentive Variant
    Cover[ - Signed by X]." (ratio), "… - Limited to 1200 copies." (tirada), "2nd Printing.",
    "Foil cover.", "Blank Sketch Cover.", "<tienda(s)> [joint] Exclusive [Variant|Virgin] Cover by X"
    (exclusiva y artista de portada), "New York Comic Con Exclusive…" (evento), "Reprints …".
  - "48 pages, full color, rated T+. Cover price $5.99." al final.
  - La página dice UTF-8 pero trae bytes cp1252 sueltos (aparece "�").

**Decisiones del usuario (2026-10-17):** el punto de entrada es el `TID` (como el id de Comic Vine),
con "MyComicShop" en el selector de fuente de Gestión → Edición; el botón abre un modal con todas las
variantes de la serie y se importa **una** a la vez, que precarga el formulario de edición como
Metron y Comic Vine (lo que MyComicShop no da se queda vacío). Si el publishing no existe se crea en
el momento, como hoy. Se guarda el `IVID` en `Edition` para marcar en el modal las variantes que ya
existen; el `TID` en `Publishing` no se guarda por ahora.

**Implementación sugerida:**
1. **Tercera fuente** `comics/catalog_sources/mycomicshop.py` que produzca `SourceIssue`
   (`types.py`). Así se reutilizan sin cambios el mapeador, `match_publishing`/`match_issue` y el
   formulario de edición. `CatalogSource.fetch` recibe un id de issue, pero la página es por serie:
   bajar por `TID`, cachear la serie y elegir la variante por `IVID`. Valorar un campo
   `mycomicshop_id` como los de Metron y Comic Vine. `is_configured` siempre devuelve verdadero,
   porque no hay clave.
2. **Descarga** en `comics/catalog_sources/http.py`: un helper de POST que devuelva HTML con las
   mismas reglas que el resto (`CATALOG_USER_AGENT`, timeout, límite de tamaño, sin seguir
   redirecciones, errores como `CatalogError`), `throttle` bajo y caché por `TID` (p. ej. 1 h).
   Añadir `media.mycomicshop.com` a `ALLOWED_COVER_HOSTS` y usar la portada de 600 px.
3. **Parser:** hoy no hay ninguna dependencia para HTML (`http.py` usa urllib). Decidir entre
   `html.parser` de la biblioteca estándar y añadir `beautifulsoup4` fijado en `requirements.txt`.
   Probarlo con un fixture HTML recortado en `comics/tests/` que tenga una variante sin stock, una con
   stock y una RI con nota.
4. **Vista en Gestión:** se pega la URL o el `TID` y se ve una vista previa propia: rejilla de
   portadas agrupada por número, y en cada variante su sufijo, la nota, la fecha, si ya existe en Comi
   y un botón para importarla. Ese botón abre el formulario de edición con la propuesta, como hace
   hoy el importador de Metron y Comic Vine.
5. **Alternativa si falla el POST:** subir el HTML guardado desde el navegador y pasarlo por el mismo
   parser. Si el POST deja de funcionar, valorar Playwright (Chromium sin interfaz en la imagen,
   ~400 MB más). **Límite acordado con el usuario:** nada de plugins "stealth", falseo de huella ni
   proxies. Si Imperva bloquea también al navegador, solo queda subir el HTML.

**Para más adelante:** búsqueda por nombre (`search?q=`, que en GET devuelve el reto; probarlo con
POST), importar varias variantes de golpe o como solicitudes de cambio (enlaza con la "Importación
masiva" de arriba).

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
- `edition_form` y `_clean_content` en `comics/views/manage_editions.py` y `evaluate` en
  `comics/achievements/engine.py` son demasiado complejas (`# noqa: C901`); dividirlas ahora que hay
  tests.
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
