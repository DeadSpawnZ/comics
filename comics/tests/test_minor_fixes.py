"""Login view, EditionAdmin flag, CollectedIssue inline and image/date helpers (point 6)."""

import datetime
import io
from unittest import mock

from django.contrib import admin
from django.test import RequestFactory, override_settings
from django.urls import reverse
from PIL import Image

from comics.admin import CollectedIssueInline
from comics.helper import generate_image_jpeg, parse_date_or_none, parse_id_or_none
from comics.models import CollectedIssue, Edition

from .base import ComiTestCase
from .factories import image_upload, make_edition, make_editorial, make_publishing, make_user


class LoginViewTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="reader", password="right-pass")
        self.url = reverse("login")

    def test_get_shows_the_sign_in_page_without_authenticating(self) -> None:
        with mock.patch("comics.views.login.authenticate") as authenticate:
            response = self.client.get(self.url)
        authenticate.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login.html")
        self.assertContains(response, 'id="loginDialog"')
        self.assertNotContains(response, "didn't match")

    def test_post_with_valid_credentials_signs_in(self) -> None:
        response = self.client.post(self.url, {"username": "reader", "password": "right-pass"})
        self.assertRedirects(response, "/collectables/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_post_with_wrong_credentials_shows_the_error(self) -> None:
        response = self.client.post(self.url, {"username": "reader", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your username and password didn't match.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_other_methods_are_not_allowed(self) -> None:
        self.assertEqual(self.client.put(self.url).status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_signed_in_user_is_sent_to_the_collection(self) -> None:
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), "/collectables/", fetch_redirect_response=False)

    def test_login_required_pages_send_their_url_as_next(self) -> None:
        response = self.client.get(reverse("comics"))
        self.assertRedirects(response, f"{self.url}?next=/comics/", fetch_redirect_response=False)

    def test_next_is_carried_in_the_login_dialog(self) -> None:
        response = self.client.get(self.url, {"next": "/comics/arcos/"})
        self.assertContains(response, '<input type="hidden" name="next" value="/comics/arcos/">', html=True)

    def test_no_next_field_without_a_valid_next(self) -> None:
        for params in [{}, {"next": "https://evil.example.com/"}]:
            with self.subTest(params=params):
                html = self.client.get(self.url, params).content.decode()
                # Only the login dialog's form (the language menu has its own `next` field).
                dialog = html[html.index('id="loginDialog"') :]
                dialog_form = dialog[: dialog.index("</form>")]
                self.assertNotIn('name="next"', dialog_form)

    def test_post_redirects_to_a_same_site_next(self) -> None:
        response = self.client.post(
            self.url, {"username": "reader", "password": "right-pass", "next": "/comics/arcos/"}
        )
        self.assertRedirects(response, "/comics/arcos/", fetch_redirect_response=False)

    def test_post_rejects_an_external_next(self) -> None:
        for target in ["https://evil.example.com/", "//evil.example.com/", "javascript:alert(1)"]:
            with self.subTest(next=target):
                self.client.logout()
                response = self.client.post(self.url, {"username": "reader", "password": "right-pass", "next": target})
                self.assertRedirects(response, "/collectables/", fetch_redirect_response=False)

    def test_failed_post_keeps_the_next(self) -> None:
        response = self.client.post(self.url, {"username": "reader", "password": "wrong", "next": "/comics/"})
        self.assertContains(response, '<input type="hidden" name="next" value="/comics/">', html=True)

    def test_signed_in_user_follows_a_same_site_next(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"next": "/comics/"})
        self.assertRedirects(response, "/comics/", fetch_redirect_response=False)


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
        request.user = make_user(staff=True, is_superuser=True)

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
