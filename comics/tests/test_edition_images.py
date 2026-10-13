"""Edition.process_image: an uploaded cover becomes a JPEG plus its thumbnail; an edition without a
cover is saved without processing (or logging) anything, and removing the cover removes its
thumbnail too."""

from unittest import mock

from comics.models import catalog
from comics.models import Edition

from .base import ComiTestCase
from .factories import image_upload, make_edition, make_publishing


class ProcessImageTests(ComiTestCase):
    def test_saving_without_a_cover_processes_and_logs_nothing(self) -> None:
        edition = Edition(publishing=make_publishing(), number="1")
        with mock.patch.object(catalog.Image, "open") as image_open, self.assertNoLogs("comics.models"):
            edition.save()
            edition.notes = "edited"
            edition.save()

        image_open.assert_not_called()
        self.assertFalse(edition.image)
        self.assertFalse(edition.thumbnail)

    def test_an_uploaded_cover_produces_the_image_and_its_thumbnail(self) -> None:
        edition = Edition(publishing=make_publishing(), number="2", image=image_upload("cover.png"))
        edition.save()

        edition.refresh_from_db()
        self.assertTrue(edition.image.name.endswith(".jpg"))
        self.assertTrue(edition.thumbnail.name.endswith("_thumb.jpg"))

    def test_an_unchanged_cover_is_not_processed_again(self) -> None:
        edition = make_edition(image=image_upload("cover.png"))
        edition.refresh_from_db()
        paths = (edition.image.name, edition.thumbnail.name)

        with mock.patch.object(catalog.Image, "open") as image_open:
            edition.notes = "edited"
            edition.save()

        image_open.assert_not_called()
        edition.refresh_from_db()
        self.assertEqual((edition.image.name, edition.thumbnail.name), paths)

    def test_removing_the_cover_clears_the_thumbnail(self) -> None:
        edition = make_edition(image=image_upload("cover.png"))
        edition.refresh_from_db()
        self.assertTrue(edition.thumbnail)

        edition.image = None
        with self.assertNoLogs("comics.models"):
            edition.save()

        edition.refresh_from_db()
        self.assertFalse(edition.image)
        self.assertFalse(edition.thumbnail)
