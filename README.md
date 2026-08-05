Proyecto de registro de comics

## Instalación
Para correr el proyecto es necesario primero hacer el build de la imagen de Django con el nombre comics-web
```bash
docker build -t comics-web .
```

Y posteriormente ejecuta el docker-compose con
```bash
docker compose up
```

Si aparece el error, solo se debe borrar la carpeta mysql_volume y comenzar desde el inicio.
```bash
ERROR: failed to build: failed to solve: invalid file request mysql_volume/mysql.sock
```

Para restaurar un dump de la base de datos es necesario entrar al contenedor con:

```
docker exec -it comi-web-1 bash
```
Y ejecutar el siguiente comando para cargar el dump
```
python manage.py loaddata /code/dump-1766186315.745431.json
```
Si existe algun error de integridad de datos solo basta con hacer una limpieza de los datos de las tablas existentes
```
python manage.py flush
```