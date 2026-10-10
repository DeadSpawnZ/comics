"""Shared test setup.

Every test case runs with plain static file storage (the production manifest storage needs
`collectstatic` to have run) and with a throwaway MEDIA_ROOT, so tests behave the same with the
real settings (MySQL in Docker) and with comic.settings_test (SQLite), and never touch real media.
The default language is English (the source language of every text) so assertions on messages do
not depend on compiled translations; tests about Spanish activate it explicitly."""

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from django.test import TestCase, override_settings

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="comi-tests-media-")

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class ComiTestCase(TestCase):
    """Base test case.

    A TestCase never commits, so transaction.on_commit callbacks (achievement evaluations, file
    deletions) stay pending for the whole test. Achievement evaluations are de-duplicated per
    transaction (comics.achievements.engine.schedule): an evaluation still pending from the test's
    fixtures (creating a user saves its profile, signing in is an event too) absorbs the events
    of the code under test, and a captureOnCommitCallbacks() opened afterwards does not see them.
    Create such fixtures inside `with self.committed():`, which runs what they scheduled as a
    real commit would."""

    @contextmanager
    def committed(self) -> Iterator[list[Callable[[], None]]]:
        """Run the on_commit callbacks scheduled inside the block when it ends, like a commit."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            yield callbacks

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
