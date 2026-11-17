"""EditionAdmin flag, CollectedIssue inline and image/date helpers (point 6).
The login view tests moved to test_accounts.py."""

import datetime
import io

from django.contrib import admin
from django.test import RequestFactory, override_settings
from PIL import Image

from comics.admin import CollectedIssueInline
from comics.helper import generate_image_jpeg, parse_date_or_none, parse_id_or_none
from comics.models import CollectedIssue, Edition

from .base import ComiTestCase
from .factories import image_upload, make_edition, make_editorial, make_publishing, make_superuser


class EditionAdminTests(ComiTestCase):
    @override_settings(STATIC_URL="/assets/")
    def test_country_flag_uses_the_static_url(self) -> None:
        edition = make_edition(publishing=make_publishing(editorials=[make_editorial(country="MX")]))
        model_admin = admin.site._registry[Edition]
        self.assertEqual(model_admin.country(edition), '<img src="/assets/images/MX.png" style="width:18px">')

    def test_country_without_editorial(self) -> None:
        self.assertEqual(admin.site._registry[Edition].country(make_edition()), "-")

    def test_collected_issue_inline_loads_issues_in_one_query(self) -> None:
        compilation = make_edition(number="100", format=Edition.FormatChoices.TRADE_PAPERBACK)
        for order, number in enumerate(["1", "2", "3"], start=1):
            issue = make_edition(publishing=make_publishing(), number=number).issue
            CollectedIssue.objects.create(edition=compilation, issue=issue, order=order)
        inline = CollectedIssueInline(Edition, admin.site)
        request = RequestFactory().get("/")
        request.user = make_superuser()

        # Every row shows its issue, whose label needs the issue's publishing: one query for all.
        with self.assertNumQueries(1):
            labels = [str(row.issue) for row in inline.get_queryset(request).filter(edition=compilation)]
        self.assertEqual(len(labels), 3)


class HelperTests(ComiTestCase):
    def test_generate_image_jpeg_without_image(self) -> None:
        self.assertIsNone(generate_image_jpeg("name", None))

    def test_generate_image_jpeg_flattens_transparent_png(self) -> None:
        result = generate_image_jpeg("cover_1", image_upload(mode="RGBA", fmt="PNG"))
        self.assertEqual(result.name, "cover_1.jpg")
        self.assertEqual(result.content_type, "image/jpeg")
        with Image.open(io.BytesIO(result.read())) as img:
            self.assertEqual((img.format, img.mode), ("JPEG", "RGB"))

    def test_parse_id_or_none(self) -> None:
        edition = make_edition()
        cases = [("12", 12), (7, 7), (edition, edition.pk), ("abc", None), ("", None), (None, None), ([1], None)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parse_id_or_none(value), expected)

    def test_parse_date_or_none(self) -> None:
        cases = {
            "2024-02-29": datetime.date(2024, 2, 29),
            "2024-02-30": None,
            "not-a-date": None,
            "": None,
            None: None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_date_or_none(value), expected)
