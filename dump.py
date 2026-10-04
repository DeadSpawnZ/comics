#!/usr/bin/env python
"""Create a database backup (dumpdata).

Usage:
    python dump.py [output_path.json]

If no path is given, "dump-<timestamp>.json" is created in the
current directory. Tables that Django regenerates by itself
(contenttypes, permissions, sessions and the admin log) are excluded
because they are the most common cause of integrity errors when the
dump is restored with `loaddata` in another environment.

References to users, groups and permissions are written with natural keys
(username, group name, app_label + codename) instead of primary keys, because
primary keys of permissions differ between databases. See DUMPDATA_OPTIONS.
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

# --natural-foreign: foreign keys / many-to-many to models with natural keys (User, Group,
#   Permission, ContentType) are written as those keys. Permissions are excluded from the dump and
#   get different ids in every database (migrations create them in a different order), so a group's
#   permissions as raw ids would point to the wrong permission, or to none, after a restore.
# --natural-primary: users and groups are written without their id, so loaddata matches them by
#   username / group name. A freshly migrated database already has the role groups (migration
#   0093), maybe with other ids; loading them by id would collide on the unique group name.
DUMPDATA_OPTIONS = ["--indent=4", "--natural-foreign", "--natural-primary"]


def dumpdata_args(output_filename: str) -> list[str]:
    """Arguments for the dumpdata command (after "dumpdata"), shared with the tests."""
    args = ["-o", output_filename, *DUMPDATA_OPTIONS]
    for model in EXCLUDED_MODELS:
        args += ["--exclude", model]
    return args


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

    execute_from_command_line(["manage.py", "dumpdata", *dumpdata_args(output_filename)])
    print(output_filename)


if __name__ == "__main__":
    main()
