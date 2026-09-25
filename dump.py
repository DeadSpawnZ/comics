#!/usr/bin/env python
"""Genera un respaldo (dumpdata) de la base de datos.

Uso:
    python dump.py [ruta_de_salida.json]

Si no se indica una ruta, se genera "dump-<timestamp>.json" en el
directorio actual. Se excluyen las tablas que Django regenera solo
(contenttypes, permisos, sesiones y el log del admin) porque son la
causa mas comun de errores de integridad al restaurar el dump con
`loaddata` en otro entorno.
"""
import os
import sys
from datetime import datetime

EXCLUDED_MODELS = [
    "contenttypes",
    "auth.permission",
    "admin.logentry",
    "sessions.session",
]


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "comic.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    if len(sys.argv) > 1:
        output_filename = sys.argv[1]
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"dump-{timestamp}.json"

    argv = ["manage.py", "dumpdata", "-o", output_filename, "--indent=4"]
    for model in EXCLUDED_MODELS:
        argv += ["--exclude", model]

    execute_from_command_line(argv)
    print(output_filename)


if __name__ == "__main__":
    main()
