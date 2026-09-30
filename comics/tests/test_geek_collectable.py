"""GeekCollectable only converts an image when a new one is uploaded (point 5)."""

from unittest import mock

from PIL import Image

from comics import models
from comics.models import GeekCollectable

from .base import ComiTestCase
from .factories import image_upload, make_dealer


class GeekCollectableImageTests(ComiTestCase):
    def setUp(self) -> None:
        self.dealer = make_dealer()

    def create(self, **fields: object) -> GeekCollectable:
        return GeekCollectable.objects.create(name="Spawn figure", participant=self.dealer, **fields)

    def test_new_upload_is_converted_to_jpeg(self) -> None:
        item = self.create(image=image_upload("figure.png"))
        self.assertTrue(item.image.name.startswith("collectables/Spawn_figure_"))
        self.assertTrue(item.image.name.endswith(".jpg"))
        with Image.open(item.image.path) as img:
            self.assertEqual((img.format, img.mode), ("JPEG", "RGB"))

    def test_saving_again_keeps_the_image_untouched(self) -> None:
        item = self.create(image=image_upload())
        stored_name = item.image.name

        item.description = "Edited"
        with mock.patch.object(models, "generate_image_jpeg", wraps=models.generate_image_jpeg) as convert:
            item.save()
            GeekCollectable.objects.get(pk=item.pk).save()  # fresh instance, unchanged image

        convert.assert_not_called()
        self.assertEqual(GeekCollectable.objects.get(pk=item.pk).image.name, stored_name)

    def test_replacing_the_image_converts_the_new_one(self) -> None:
        item = self.create(image=image_upload())
        old_name = item.image.name

        item.image = image_upload("new.png", mode="RGB")
        with mock.patch.object(models, "generate_image_jpeg", wraps=models.generate_image_jpeg) as convert:
            item.save()

        convert.assert_called_once()
        self.assertNotEqual(item.image.name, old_name)
        self.assertTrue(item.image.name.endswith(".jpg"))

    def test_without_image_nothing_is_stored(self) -> None:
        item = self.create()
        item.save()
        self.assertFalse(GeekCollectable.objects.get(pk=item.pk).image)
