# Comi: producto

Qué hace Comi y con qué conceptos trabaja: funcionalidades, conceptos del catálogo, roles y áreas, y
cómo funcionan los logros. La descripción técnica (arquitectura, esquema de datos, instalación,
respaldos y desarrollo) está en el [`README.md`](README.md); el trabajo pendiente, las decisiones
abiertas y las limitaciones conocidas, en [`BACKLOG.md`](BACKLOG.md).

**Contenido:** [Funcionalidades](#funcionalidades) · [Conceptos del catálogo](#conceptos-del-catálogo) ·
[Roles y áreas](#roles-y-áreas) · [Logros](#logros)

## Funcionalidades

- **Catálogo** de editoriales, títulos, publishings, issues, ediciones (variantes, reimpresiones,
  compilaciones) y artistas. Se mantiene desde **Gestión**, un área propia separada del admin de
  Django.
- **Importación desde catálogos externos:** el formulario de ediciones de Gestión se puede precargar
  desde [Metron](https://metron.cloud) (fuente principal), [Comic Vine](https://comicvine.gamespot.com)
  (respaldo) o [MyComicShop](https://www.mycomicshop.com), para las variantes que no están en los otros
  dos (incentivos, exclusivas de tienda o convención…): con el `TID` de la serie se abre un modal con
  todas sus portadas, se elige una y se rellenan variante, ratio, impresión, tirada y exclusivas.
  El modal marca como "Posiblemente en Comi" las ediciones ya catalogadas que parecen ser esa portada
  (mismo publishing, número y letra, misma condición de exclusiva y la impresión si MyComicShop la
  indica) y que aún no tienen ID de MyComicShop; "Vincular" guarda ese ID en la edición (como una
  versión más del historial). El `TID` queda guardado en el publishing (al crearlo desde la
  importación o al vincular una portada, si no tenía otro) y empareja la serie antes que el título.
  Desde los mismos diálogos se crean el publishing, el título, la editorial y los
  artistas que falten. Las editoriales tienen alias para emparejar los nombres de cada fuente.
- **Lotes de importación desde MyComicShop** (Gestión → Importaciones): en el modal de una serie se
  marcan varias variantes y "Importar seleccionadas" crea un lote, guardado en la base, que se revisa
  antes de crear nada: valores comunes para todas las variantes, cambios por variante, cada nombre
  de artista sin coincidencia se resuelve una vez para todo el lote (asociar, crear o descartar) y
  las posibles coincidencias se pueden vincular. Las casillas de las variantes se guardan solas en
  cada cambio. "Crear seleccionadas" crea cada edición con su propia transacción y su versión en el
  historial; las que fallan se quedan con su error.
- **Colección personal** ("Mis comics"): compras y ventas de cada edición (con vendedor, precio y
  valoración), firmas de artistas, lo que se tiene y lo que falta de cada publishing. También
  **arcos de lectura** (issues en orden, de varias series), **connectings** (portadas que forman una
  imagen mayor) y **collectables** (figuras, prints, pines...: un catálogo de artículos
  compartido y las piezas que compra o vende cada coleccionista).
  En la lista de ediciones de Mis comics, lo que se tiene es un trofeo amarillo (con el número de
  copias) y lo que se busca un corazón rojo, cada uno en su columna; las 5 últimas ediciones
  agregadas salen primero en la primera página sin filtros (10 en Gestión).
- **Cacería:** cada coleccionista marca con el corazón ("Lo busco") las ediciones que quiere
  conseguir, desde la lista o el detalle de ediciones y desde las piezas que le faltan de un
  connecting, y las ve juntas en Mis comics → Cacería: una cuadrícula de portadas, atenuadas mientras
  no las tiene, cada una con un diálogo para su nota. Se puede buscar una edición que ya se tiene
  (otra copia o una en mejor estado; sale entera, con su trofeo). Al registrar la compra de una
  edición buscada, sale de la cacería y el comprador recibe una notificación desde la que puede
  seguir buscándola (con su nota) sin deshacer la compra.
- **Historial de versiones** de issues y ediciones: cada guardado es una versión (quién, cuándo,
  motivo), sea cual sea su origen. Cualquier versión se puede restaurar desde Gestión.
- **Solicitudes de cambio:** los coleccionistas proponen correcciones o altas de issues y ediciones;
  los revisores las validan y los administradores las aprueban. Al aprobarse se aplican como una
  versión más del historial. El autor recibe un aviso en cada paso.
- **Logros** con niveles, categorías y ventanas de tiempo. La regla de cada logro está en el código;
  textos, íconos, niveles y categorías se editan en Gestión.
- **Perfiles** públicos o privados con avatar y logros, **notificaciones** (campana de la barra
  superior) y **anuncios** de Gestión a todos o a ciertos roles.

## Conceptos del catálogo

| Concepto | Qué es |
| --- | --- |
| **Editorial** | La casa que publica (Marvel, DC, Panini…). Puede tener alias con los nombres que le dan Metron o Comic Vine. |
| **Title** | Un nombre que agrupa varios publishings, por ejemplo los volúmenes de una misma cabecera. |
| **Publishing** | Una serie concreta: nombre, volumen (`serie`), idioma, año de inicio y editoriales. Es lo que Metron llama *series* y Comic Vine *volume*. |
| **Issue** | El contenido de un número (la historia): número, sinopsis y creadores. Es único por publishing y número. |
| **Edition** | La pieza física, lo que se suele llamar "un cómic": portada o variante, impresión, formato, precio de portada, fecha de salida e imagen. Apunta al issue que contiene; si es una compilación, a varios issues en orden. Puede pertenecer a un publishing distinto al del issue (reediciones, ediciones extranjeras). |
| **Artist** | Creadores del interior (`IssueCreator`) y artistas de portada (`EditionCoverArtist`). No se puede borrar mientras tenga créditos. |
| **Collection** | Una compra o una venta de una edición por un coleccionista. Una venta apunta a la compra que cierra (`previous_trade`). |
| **Article** | Un artículo del catálogo de collectables (figura, print, pin…), compartido por todos. Se mantiene desde Gestión. |
| **Collectable** | Una compra o una venta de un artículo por un coleccionista. Como en `Collection`, una venta apunta a la compra que cierra (`previous_trade`). |

## Roles y áreas

Los roles son grupos de Django; las vistas comprueban permisos, nunca nombres de grupo
(`comics/roles.py`). Se recrean con `manage.py ensure_roles`.

| Rol | Puede |
| --- | --- |
| **Collectors** | Todo usuario registrado: su colección, perfil, notificaciones y proponer cambios. |
| **Reviewers** | Además, validar o rechazar solicitudes en el área de revisión. |
| **Administrators** | Además, entrar en Gestión y aprobar solicitudes. |
| Superusuarios | Todo lo anterior y el admin de Django, que es solo para ellos. |

| Ruta | Área |
| --- | --- |
| `/comics/` | Mis comics: colección, ediciones, cacería, arcos, connectings y solicitudes propias |
| `/collectables/` | Collectables propios (las piezas que se conservan) y catálogo de artículos |
| `/perfil/`, `/u/<usuario>/` | Perfil propio y perfiles de otros usuarios |
| `/notificaciones/` | Notificaciones |
| `/revisiones/` | Cola de revisión de solicitudes (Reviewers y Administrators) |
| `/gestion/` | Gestión: catálogo, colecciones, artículos y collectables, arcos, connectings, historial, logros y anuncios (Administrators) |
| `/admin/` | Admin de Django (superusuarios) |

## Logros

Cómo se gana cada logro está en el código (`comics/achievements/rules.py`); nombre, textos, ícono, niveles con sus umbrales, si es secreto y si está activo se editan en Gestión → Logros. Cada logro nuevo llega con una migración que crea su registro.

Los logros por fecha (como «Veterano», por años de antigüedad) solo se revisan al iniciar sesión o al correr `award_achievements` (ver [README](README.md#logros)): quien no vuelve a iniciar sesión no los recibe hasta entonces. No se usan programadores del sistema (cron, Programador de tareas de Windows); las tareas periódicas tienen que vivir dentro de la app o del stack de Docker (pendiente en `BACKLOG.md`).
