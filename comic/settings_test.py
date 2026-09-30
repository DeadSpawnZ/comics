"""Settings for running the test suite locally without Docker/MySQL.

Usage: python manage.py test comics --settings=comic.settings_test

SQLite differs from MySQL in some behaviors (case sensitivity, constraints,
transactional DDL), so the Docker/MySQL run remains the authoritative one.
"""

import tempfile

from .settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Fast hashing: tests don't need secure passwords.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Keep uploaded files out of the real media folder.
MEDIA_ROOT = tempfile.mkdtemp(prefix="comi-test-media-")

# Avoid needing collectstatic / the manifest while running tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
