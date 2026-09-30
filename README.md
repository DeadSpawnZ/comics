Proyecto de registro de comics

## Requisitos previos

Antes de levantar el proyecto crea, en la raíz del repo, un archivo `mysql.env` (usado por el servicio `db` de docker-compose) con estas variables:

```
MYSQL_DATABASE=comic
MYSQL_ROOT_PASSWORD=<password root>
MYSQL_USER=<usuario de la app>
MYSQL_PASSWORD=<password del usuario>
```

Opcionalmente puedes crear un `.env` en la raíz con `COMPOSE_PROJECT_NAME=<nombre>` para fijar el nombre del proyecto de Docker Compose. Ninguno de los dos archivos se sube al repositorio (están en `.gitignore`).

## Instalación

Levanta el proyecto (esto construye la imagen del servicio `web` automáticamente, no hace falta un `docker build` manual aparte):

```bash
docker compose up --build
```

En el primer arranque, aplica las migraciones y crea un superusuario para poder entrar al admin:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

La app queda disponible en `http://localhost:8080/` y el admin en `http://localhost:8080/admin/`.

## Respaldar la base de datos

Para generar un respaldo (`dumpdata`) y copiarlo directamente a tu equipo, desde la raíz del repo en PowerShell:

```powershell
.\scripts\backup-db.ps1
```

El script valida que el contenedor esté corriendo, genera el dump dentro del contenedor `web`, lo copia a `.\backups\` con un nombre `dump-<fecha>.json`, limpia el archivo temporal del contenedor y conserva únicamente los últimos 10 respaldos locales (configurable con `-Keep`). También puedes elegir otra carpeta de destino con `-OutputDir`:

```powershell
.\scripts\backup-db.ps1 -OutputDir D:\backups\comi -Keep 20
```

Si prefieres generarlo manualmente dentro del contenedor:

```bash
docker compose exec web python dump.py
```

## Restaurar un dump

```bash
docker compose exec web python manage.py loaddata /code/dump-<fecha>.json
```

Si el archivo está solo en tu equipo (por ejemplo, uno generado con `backup-db.ps1`), primero cópialo dentro del contenedor:

```powershell
docker compose cp .\backups\dump-<fecha>.json web:/code/dump-<fecha>.json
```

Si aparece un error de integridad de datos al restaurar, limpia las tablas existentes antes de reintentar:

```bash
docker compose exec web python manage.py flush
```

## Respaldo completo en SQL (`mysqldump`)

El respaldo JSON de arriba solo contiene los datos de Django. Para una copia exacta de toda la base (tablas, índices, restricciones e historial del admin) usa `mysqldump`. Es el respaldo que se saca antes de aplicar una migración y se guarda en `backups/` con el nombre `comic-<fecha>.sql`.

Los comandos funcionan igual en PowerShell y en bash. El archivo se genera **dentro** del contenedor `db` y luego se copia con `docker compose cp`, porque redirigir con `>` en PowerShell 5.1 guarda el archivo en UTF-16 y lo corrompe.

### Sacar el respaldo

```powershell
docker compose exec db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers "$MYSQL_DATABASE" > /tmp/backup.sql'
docker compose cp db:/tmp/backup.sql .\backups\comic-<fecha>.sql
docker compose exec db rm /tmp/backup.sql
```

`--single-transaction` toma una copia consistente sin detener la app. Para confirmar que el respaldo no quedó incompleto, la última línea del archivo debe decir `-- Dump completed`.

### Restaurar el respaldo

> **Atención:** restaurar **reemplaza toda la base actual** con el contenido del respaldo (cada tabla se borra y se vuelve a crear). Lo que se haya registrado después de sacar ese respaldo se pierde. Saca un respaldo nuevo antes de restaurar por si necesitas volver atrás.

```powershell
docker compose stop web
docker compose cp .\backups\comic-<fecha>.sql db:/tmp/restore.sql
docker compose exec db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < /tmp/restore.sql && rm /tmp/restore.sql'
docker compose run --rm web python manage.py migrate
docker compose start web
```

- Detener `web` evita que alguien guarde cambios a mitad de la restauración.
- `migrate` aplica las migraciones del código que sean más nuevas que el respaldo. Si el respaldo ya las tenía, no hace nada.

### Probar un respaldo sin tocar la base real

La base `comic_refactor` vive en el mismo contenedor `db` y sirve para ensayar. Para cargar ahí un respaldo, cambia `"$MYSQL_DATABASE"` por `comic_refactor` en el comando de restauración (no hace falta detener `web`). La primera línea la crea si todavía no existe:

```powershell
docker compose exec db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE IF NOT EXISTS comic_refactor CHARACTER SET utf8mb4"'
docker compose cp .\backups\comic-<fecha>.sql db:/tmp/restore.sql
docker compose exec db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" comic_refactor < /tmp/restore.sql && rm /tmp/restore.sql'
```

## Correr localmente con configuración de producción

Por defecto (`docker compose up`) el proyecto corre en modo desarrollo: `DEBUG=True`, `runserver` con autoreload y `ALLOWED_HOSTS=*`. Para levantar el mismo stack pero con la configuración que usarías en producción (`DEBUG=False`, `ALLOWED_HOSTS` restringido, gunicorn en vez de `runserver`), usa el archivo de override `docker-compose.prod.yaml`:

```bash
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml up --build
```

Los estáticos se sirven igual en ambos modos (vía WhiteNoise, ya recolectados en la imagen con `collectstatic` durante el build), así que no hay diferencia de comportamiento ahí entre desarrollo y este modo "producción" local.

Antes de un despliegue real, además define `DJANGO_SECRET_KEY` con un valor propio (el `SECRET_KEY` por defecto en `settings.py` está expuesto en el historial del repo) y ajusta `DJANGO_ALLOWED_HOSTS` al dominio real.

## Traducciones (español / inglés)

El idioma se elige desde el botón de traducción en la barra superior y se guarda en una cookie. Sin elección previa se usa el idioma del navegador si es español o inglés; si no, español.

Los textos de la interfaz se escriben en **inglés** en el código (`{% translate %}` en plantillas, `gettext` en Python y en JS) y su traducción al español vive en `locale/es/LC_MESSAGES/`: `django.po` para Python y plantillas, `djangojs.po` para JavaScript. Al agregar o cambiar textos:

1. Extrae los textos nuevos a los `.po`:
   ```bash
   docker compose run --rm -v "$(pwd):/code" web sh -c 'python manage.py makemessages -l es --no-wrap -i staticfiles -i media -i backups -i "comics/static/vendor/*" && python manage.py makemessages -d djangojs -l es --no-wrap -i staticfiles -i media -i backups -i "comics/static/vendor/*"'
   ```
2. Escribe la traducción en cada `msgstr` vacío de los `.po`.
3. Reconstruye la imagen (`docker compose up --build`): el build compila los `.po` a `.mo` con `compilemessages`. Los `.mo` no se versionan.

## Notas

- Si ves un error del estilo `failed to solve: invalid file request mysql_volume/mysql.sock`, es porque quedó un volumen de MySQL corrupto. Bájalo y elimina el volumen con `docker compose down -v` y vuelve a levantar el proyecto (esto borra los datos de la base local).
- El contenedor `web` corre como usuario sin privilegios (`app`). Si ya tenías un `media_volume` creado por una versión anterior de la imagen (donde corría como `root`), la primera vez que actualices vas a necesitar arreglar los permisos una sola vez:
  ```bash
  docker compose exec --user root web chown -R app:app /code/media
  ```
