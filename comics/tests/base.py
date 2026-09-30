"""Shared test setup.

Every test case runs with plain static file storage (the production manifest storage needs
`collectstatic` to have run) and with a throwaway MEDIA_ROOT, so tests behave the same with the
real settings (MySQL in Docker) and with comic.settings_test (SQLite), and never touch real media.
The default language is English (the source language of every text) so assertions on messages do
not depend on compiled translations; tests about Spanish activate it explicitly."""

import shutil
import tempfile

from django.test import TestCase, override_settings

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="comi-tests-media-")

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class ComiTestCase(TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
