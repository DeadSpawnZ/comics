"""Gestión edition and collection forms: the save buttons say "Update" once the object exists; the
edition form's related selects get admin-style "+" (create) and pencil (edit) actions."""

from django.urls import reverse

from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_superuser, make_user


class EditionFormButtonsTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)

    def test_a_new_edition_offers_to_save(self) -> None:
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        self.assertIn("Save and continue editing", html)
        self.assertNotIn("Update and continue editing", html)

    def test_an_existing_edition_offers_to_update(self) -> None:
        edition = make_edition()
        html = self.client.get(reverse("manage_edition_edit", args=[edition.pk])).content.decode()
        self.assertIn("Update and continue editing", html)
        self.assertNotIn("Save and continue editing", html)

    def test_publishing_has_create_and_edit_actions(self) -> None:
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        self.assertIn('id="create-publishing"', html)
        self.assertIn('data-for="id_publishing"', html)
        self.assertIn(f'data-url-template="{reverse("manage_publishing_edit", args=[0])}"', html)

    def test_title_edit_link_only_for_superusers(self) -> None:
        title_change = reverse("admin:comics_title_change", args=[0])
        self.assertNotIn(title_change, self.client.get(reverse("manage_edition_new")).content.decode())
        self.client.force_login(make_superuser())
        self.assertIn(title_change, self.client.get(reverse("manage_edition_new")).content.decode())


class CollectionFormButtonsTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)

    def test_a_new_piece_offers_to_save(self) -> None:
        html = self.client.get(reverse("manage_collection_new")).content.decode()
        self.assertIn("Save and continue editing", html)
        self.assertNotIn("Update and continue editing", html)

    def test_an_existing_piece_offers_to_update(self) -> None:
        piece = make_collection(collector=self.manager, edition=make_edition())
        html = self.client.get(reverse("manage_collection_edit", args=[piece.pk])).content.decode()
        self.assertIn("Update and continue editing", html)
        self.assertNotIn("Save and continue editing", html)
