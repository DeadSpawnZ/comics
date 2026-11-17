"""MyComicShop's series id (TID) on Publishing: the field (unique, in the Gestión forms and the
identifiers panel), matching the series by it before the title (importer and variants modal), the
new publishing proposal carrying it, and the "Link" action of the variants modal filling it on the
edition's publishing. HTTP is mocked with the task 1 fixtures."""

from typing import Any
from unittest import mock

from django.contrib.admin.models import CHANGE, LogEntry
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.urls import reverse

from comics.catalog_sources import mycomicshop
from comics.catalog_sources.mapper import match_publishing
from comics.catalog_sources.service import load_proposal, mycomicshop_series
from comics.models import Edition, Publishing, Title
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_edition, make_editorial, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase
from .test_mycomicshop_import import BATMAN_TID, neutral_issue, page_answer
from .test_mycomicshop_link import IVID_1L, variants_by_label


class FieldTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def test_unique_when_set(self) -> None:
        make_publishing(mycomicshop_series_id=BATMAN_TID)
        make_publishing(mycomicshop_series_id=None)
        make_publishing(mycomicshop_series_id=None)  # several publishings without one
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_publishing(mycomicshop_series_id=BATMAN_TID)

    def test_the_panel_links_the_series_page(self) -> None:
        publishing = make_publishing(mycomicshop_series_id=BATMAN_TID)
        response = self.client.get(reverse("manage_publishing_edit", args=[publishing.pk]))
        self.assertContains(response, 'name="mycomicshop_series_id"')
        self.assertContains(response, f'href="{mycomicshop.series_url(BATMAN_TID)}"')
        self.assertEqual(
            [
                (item.source, item.value)
                for item in Publishing(gcd_series_id=3, mycomicshop_series_id=9).identification_numbers()
            ],
            [("GCD", 3), ("MyComicShop", 9)],
        )

    def test_the_publishing_page_saves_it_and_refuses_another_publishings(self) -> None:
        make_publishing(mycomicshop_series_id=BATMAN_TID)
        publishing = make_publishing(publishing_title="Saga", year=2012)
        url = reverse("manage_publishing_edit", args=[publishing.pk])
        data = {
            "publishing_title": "Saga",
            "serie": "1st",
            "language": "en",
            "date": "2012-03-14",
            "year": "2012",
            "editorials": [make_editorial().pk],
        }
        response = self.client.post(url, {**data, "mycomicshop_series_id": str(BATMAN_TID)})
        self.assertContains(response, "Another publishing already has this MyComicShop series ID.")
        publishing.refresh_from_db()
        self.assertIsNone(publishing.mycomicshop_series_id)
        self.client.post(url, {**data, "mycomicshop_series_id": "123"})
        publishing.refresh_from_db()
        self.assertEqual(publishing.mycomicshop_series_id, 123)

    def test_the_create_dialog_saves_it_and_refuses_another_publishings(self) -> None:
        title = Title.objects.create(name="Batman Spawn")
        values = {
            "title": title.pk,
            "publishing_title": "Batman Spawn",
            "serie": "1st",
            "language": "en",
            "date": "2023-02-01",
            "year": "",
            "editorials": [make_editorial().pk],
            "mycomicshop_series_id": str(BATMAN_TID),
        }
        data = {f"new_publishing-{key}": value for key, value in values.items()}
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        self.assertIn('name="new_publishing-mycomicshop_series_id"', html)
        response = self.client.post(reverse("manage_publishing_create_api"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Publishing.objects.get(pk=response.json()["id"]).mycomicshop_series_id, BATMAN_TID)
        response = self.client.post(reverse("manage_publishing_create_api"), {**data, "new_publishing-serie": "2nd"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["errors"],
            {"mycomicshop_series_id": ["Another publishing already has this MyComicShop series ID."]},
        )


class MatchByTidTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Matches MyComicShop's series by title, year and language.
        self.by_title = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")

    def test_the_publishing_holding_the_tid_beats_the_title(self) -> None:
        held = make_publishing(publishing_title="Batman / Spawn (renamed)", year=2022, mycomicshop_series_id=BATMAN_TID)
        with mock.patch(OPEN_URL, side_effect=page_answer):
            proposal = load_proposal("mycomicshop", IVID_1L, BATMAN_TID)
        self.assertEqual(proposal.publishing["id"], held.pk)
        self.assertEqual(proposal.publishing_candidates, [])
        self.assertIsNone(proposal.new_publishing)
        self.assertFalse(any(warning.startswith("Matched “") for warning in proposal.warnings))

    def test_another_tid_falls_back_to_the_title(self) -> None:
        make_publishing(publishing_title="Other", mycomicshop_series_id=1)
        with mock.patch(OPEN_URL, side_effect=page_answer):
            proposal = load_proposal("mycomicshop", IVID_1L, BATMAN_TID)
        self.assertEqual(proposal.publishing["id"], self.by_title.pk)

    def test_the_series_modal_matches_by_tid_too(self) -> None:
        held = make_publishing(publishing_title="Renamed", year=2022, mycomicshop_series_id=BATMAN_TID)
        candidate = make_edition(publishing=held, number="1", variant="L")
        make_edition(publishing=self.by_title, number="1", variant="L")
        with mock.patch(OPEN_URL, side_effect=page_answer):
            data = mycomicshop_series(BATMAN_TID)
        self.assertEqual(data["series"]["publishing"]["id"], held.pk)
        self.assertEqual([item["id"] for item in variants_by_label(data)["1L"]["probable"]], [candidate.pk])

    def test_other_sources_ignore_the_tid(self) -> None:
        # Metron and Comic Vine issues have no MyComicShop series id: a TID never matches them.
        make_publishing(publishing_title="Renamed", mycomicshop_series_id=BATMAN_TID)
        source = neutral_issue(
            source="metron", series_name="Batman Spawn", series_year=2023, source_series_id=BATMAN_TID
        )
        self.assertEqual(match_publishing(source, None), (self.by_title, []))

    def test_the_new_publishing_proposal_carries_the_tid(self) -> None:
        self.by_title.delete()
        with mock.patch(OPEN_URL, side_effect=page_answer):
            proposal = load_proposal("mycomicshop", IVID_1L, BATMAN_TID)
        self.assertIsNone(proposal.publishing)
        self.assertEqual(proposal.new_publishing.mycomicshop_series_id, BATMAN_TID)
        self.assertEqual(proposal.as_dict()["new_publishing"]["mycomicshop_series_id"], BATMAN_TID)


class LinkFillsTidTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_catalog_mycomicshop_link")
        self.publishing = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        self.edition = make_edition(publishing=self.publishing, number="1", variant="L")
        self.admin = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.admin)

    def post(self, **data: Any) -> Any:
        data.setdefault("edition", self.edition.pk)
        data.setdefault("ivid", IVID_1L)
        data.setdefault("tid", BATMAN_TID)
        return self.client.post(self.url, data)

    def publishing_log(self) -> Any:
        return LogEntry.objects.filter(content_type__model="publishing", object_id=str(self.publishing.pk))

    def assert_linked(self, response: Any) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).mycomicshop_id, IVID_1L)

    def test_the_link_fills_an_empty_tid(self) -> None:
        self.assert_linked(self.post())
        self.publishing.refresh_from_db()
        self.assertEqual(self.publishing.mycomicshop_series_id, BATMAN_TID)
        entry = self.publishing_log().get()
        self.assertEqual((entry.action_flag, entry.user), (CHANGE, self.admin))
        self.assertIn(str(BATMAN_TID), entry.change_message)

    def test_relinking_the_same_ivid_fills_it_too(self) -> None:
        Edition.objects.filter(pk=self.edition.pk).update(mycomicshop_id=IVID_1L)
        self.assert_linked(self.post())
        self.publishing.refresh_from_db()
        self.assertEqual(self.publishing.mycomicshop_series_id, BATMAN_TID)

    def test_another_tid_already_set_is_not_overwritten(self) -> None:
        Publishing.objects.filter(pk=self.publishing.pk).update(mycomicshop_series_id=123)
        self.assert_linked(self.post())
        self.publishing.refresh_from_db()
        self.assertEqual(self.publishing.mycomicshop_series_id, 123)
        self.assertFalse(self.publishing_log().exists())

    def test_a_tid_held_by_another_publishing_is_skipped_and_the_link_succeeds(self) -> None:
        other = make_publishing(publishing_title="Other", mycomicshop_series_id=BATMAN_TID)
        self.assert_linked(self.post())
        self.publishing.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.publishing.mycomicshop_series_id, other.mycomicshop_series_id), (None, BATMAN_TID))
        self.assertFalse(self.publishing_log().exists())

    def test_a_tid_taken_meanwhile_does_not_undo_the_link(self) -> None:
        # Another publishing took the TID after the check (which is made to miss it): the unique
        # index refuses the write, and only that write is undone.
        other = make_publishing(publishing_title="Other", mycomicshop_series_id=BATMAN_TID)
        real_exists = QuerySet.exists

        def exists(queryset: QuerySet) -> bool:
            return False if queryset.model is Publishing else real_exists(queryset)

        with mock.patch.object(QuerySet, "exists", exists):
            self.assert_linked(self.post())
        self.publishing.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.publishing.mycomicshop_series_id, other.mycomicshop_series_id), (None, BATMAN_TID))
        self.assertFalse(self.publishing_log().exists())

    def test_without_a_tid_the_publishing_is_left_alone(self) -> None:
        self.assert_linked(self.client.post(self.url, {"edition": self.edition.pk, "ivid": IVID_1L}))
        self.publishing.refresh_from_db()
        self.assertIsNone(self.publishing.mycomicshop_series_id)

    def test_an_invalid_tid_is_a_400(self) -> None:
        response = self.post(tid="abc")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(Edition.objects.get(pk=self.edition.pk).mycomicshop_id)
