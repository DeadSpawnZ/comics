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
#   As a consequence user and group ids are not preserved by a restore (they may change). That is
#   safe: every reference to them inside the dump is written by natural key, and the only tables
#   that store user ids outside the dump (admin log, sessions) are excluded above.
DUMPDATA_OPTIONS = ["--indent=4", "--natural-foreign", "--natural-primary"]


def write_dump(output_filename: str) -> None:
    """Write the dump to `output_filename` in UTF-8 (Django must be set up). Shared with the tests.

    The file is opened here rather than with dumpdata's -o, which uses the platform's default
    encoding: on Windows (cp1252) loaddata, which always reads UTF-8, could not load it back."""
    from django.core.management import call_command

    with open(output_filename, "w", encoding="utf-8") as stream:
        call_command("dumpdata", *DUMPDATA_OPTIONS, exclude=EXCLUDED_MODELS, stdout=stream)


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "comic.settings")
    try:
        import django
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

    django.setup()
    write_dump(output_filename)
    print(output_filename)


if __name__ == "__main__":
    main()
