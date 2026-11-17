"""Gestión edition form: a new edition proposes the publishing's start date as its release date
(edition_form.js does it on the page with the dates the view hands it)."""

import datetime

from django.urls import reverse

from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_edition, make_publishing, make_user


class EditionReleaseDateTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.publishing = make_publishing(date=datetime.date(1986, 2, 1))

    def test_the_page_knows_each_publishing_start_date(self) -> None:
        response = self.client.get(reverse("manage_edition_new"))
        data = response.context["form_data"]
        self.assertEqual(data["publishingDates"][self.publishing.pk], "1986-02-01")
        self.assertTrue(data["fillReleaseDate"])

    def test_new_edition_from_a_publishing_starts_with_its_date(self) -> None:
        response = self.client.get(f"{reverse('manage_edition_new')}?publishing={self.publishing.pk}")
        self.assertEqual(response.context["form"]["release_date"].value(), datetime.date(1986, 2, 1))

    def test_an_unknown_publishing_keeps_the_default_date(self) -> None:
        response = self.client.get(f"{reverse('manage_edition_new')}?publishing=999999")
        self.assertNotEqual(response.context["form"]["release_date"].value(), datetime.date(1986, 2, 1))

    def test_an_existing_edition_keeps_its_date(self) -> None:
        edition = make_edition(publishing=self.publishing)
        response = self.client.get(reverse("manage_edition_edit", args=[edition.pk]))
        self.assertFalse(response.context["form_data"]["fillReleaseDate"])

    def test_a_form_sent_back_with_errors_keeps_its_date(self) -> None:
        response = self.client.post(reverse("manage_edition_new"), {"publishing": self.publishing.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form_data"]["fillReleaseDate"])
