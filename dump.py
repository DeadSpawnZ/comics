#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from datetime import datetime

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'comic.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    now = datetime.now()
    timestamp = datetime.timestamp(now)
    output_filename = f"dump-{timestamp}.json"
    lista = ['manage.py', 'dumpdata', '--exclude=auth', '--exclude=contenttypes', '-o', output_filename, '--indent=4']
    execute_from_command_line(lista)

if __name__ == '__main__':
    main()
