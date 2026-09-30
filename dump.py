#!/usr/bin/env python
"""Create a database backup (dumpdata).

Usage:
    python dump.py [output_path.json]

If no path is given, "dump-<timestamp>.json" is created in the
current directory. Tables that Django regenerates by itself
(contenttypes, permissions, sessions and the admin log) are excluded
because they are the most common cause of integrity errors when the
dump is restored with `loaddata` in another environment.
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


def main() -> None:
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
