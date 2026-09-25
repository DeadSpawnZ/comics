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

## Correr localmente con configuración de producción

Por defecto (`docker compose up`) el proyecto corre en modo desarrollo: `DEBUG=True`, `runserver` con autoreload y `ALLOWED_HOSTS=*`. Para levantar el mismo stack pero con la configuración que usarías en producción (`DEBUG=False`, `ALLOWED_HOSTS` restringido, gunicorn en vez de `runserver`), usa el archivo de override `docker-compose.prod.yaml`:

```bash
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml up --build
```

Los estáticos se sirven igual en ambos modos (vía WhiteNoise, ya recolectados en la imagen con `collectstatic` durante el build), así que no hay diferencia de comportamiento ahí entre desarrollo y este modo "producción" local.

Antes de un despliegue real, además define `DJANGO_SECRET_KEY` con un valor propio (el `SECRET_KEY` por defecto en `settings.py` está expuesto en el historial del repo) y ajusta `DJANGO_ALLOWED_HOSTS` al dominio real.

## Notas

- Si ves un error del estilo `failed to solve: invalid file request mysql_volume/mysql.sock`, es porque quedó un volumen de MySQL corrupto. Bájalo y elimina el volumen con `docker compose down -v` y vuelve a levantar el proyecto (esto borra los datos de la base local).
- El contenedor `web` corre como usuario sin privilegios (`app`). Si ya tenías un `media_volume` creado por una versión anterior de la imagen (donde corría como `root`), la primera vez que actualices vas a necesitar arreglar los permisos una sola vez:
  ```bash
  docker compose exec --user root web chown -R app:app /code/media
  ```
