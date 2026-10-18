"""MyComicShop variants modal against the editions Comi already has: the probable matches of the series
endpoint (same publishing, number, variant letter and stated printing, no IVID yet) and the "Link"
endpoint that stores a variant's IVID on one of them. HTTP is mocked with the task 1 fixtures."""

from typing import Any
from unittest import mock

from django.contrib.admin.models import CHANGE, LogEntry
from django.test import Client
from django.urls import reverse

from comics.catalog_sources import mycomicshop
from comics.catalog_sources.service import mycomicshop_series
from comics.history import versions_of
from comics.models import Edition
from comics.roles import ADMINISTRATORS

from .factories import make_edition, make_editorial, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase
from .test_mycomicshop_import import BATMAN_TID, page_answer

# Batman Spawn (2023, DC): 1A, 1B&N, 1CUSTOM.B, 1L, 1P and 1U (a 2nd printing).
IVID_1A, IVID_1L, IVID_1P, IVID_1U = 57507058, 57507069, 57507073, 58025365


def variants_by_label(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {variant["label"]: variant for issue in data["issues"] for variant in issue["variants"]}


class ProbableMatchTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.dc = make_editorial(name="DC")
        self.publishing = make_publishing(
            publishing_title="Batman Spawn", year=2023, language="en", editorials=[self.dc]
        )

    def series(self) -> dict[str, Any]:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            return mycomicshop_series(BATMAN_TID)

    def test_same_number_and_letter_is_probable(self) -> None:
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        virgin = make_edition(publishing=self.publishing, number="1", variant="L")
        data = self.series()
        self.assertEqual(
            data["series"]["publishing"], {"id": self.publishing.pk, "label": self.publishing.select_label}
        )
        variants = variants_by_label(data)
        self.assertIsNone(variants["1A"]["existing"])
        self.assertEqual(
            variants["1A"]["probable"],
            [
                {
                    "id": main.pk,
                    "label": "Batman Spawn #1 A",
                    "url": reverse("manage_edition_edit", args=[main.pk]),
                    "cover": "#1 A",
                    "thumb_url": "",
                }
            ],
        )
        self.assertEqual([item["id"] for item in variants["1L"]["probable"]], [virgin.pk])
        self.assertEqual(variants["1P"]["probable"], [])

    def test_a_different_letter_or_another_publishing_is_not_probable(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="B")
        make_edition(publishing=self.publishing, number="2", variant="A")
        make_edition(publishing=make_publishing(publishing_title="Spawn", year=2023), number="1", variant="A")
        variants = variants_by_label(self.series())
        self.assertTrue(all(not variant["probable"] for variant in variants.values()))

    def test_an_edition_with_another_ivid_is_not_a_candidate(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="A", mycomicshop_id=999)
        variants = variants_by_label(self.series())
        self.assertEqual((variants["1A"]["existing"], variants["1A"]["probable"]), (None, []))

    def test_the_firm_match_by_ivid_wins(self) -> None:
        firm = make_edition(publishing=self.publishing, number="1", variant="A", mycomicshop_id=IVID_1A)
        make_edition(publishing=self.publishing, number="1", variant="A", printing="2nd")
        first = variants_by_label(self.series())["1A"]
        self.assertEqual(first["existing"]["id"], firm.pk)
        self.assertEqual(first["probable"], [])

    def test_no_publishing_match_means_no_probables(self) -> None:
        other_year = make_publishing(publishing_title="Batman Spawn", year=2022, language="en")
        make_edition(publishing=other_year, number="1", variant="A")
        self.publishing.delete()
        data = self.series()
        self.assertIsNone(data["series"]["publishing"])
        self.assertTrue(all(not variant["probable"] for variant in variants_by_label(data).values()))

    def test_ambiguous_publishings_mean_no_probables(self) -> None:
        twin = make_publishing(
            publishing_title="Batman Spawn", year=2023, language="en", serie="2nd", editorials=[self.dc]
        )
        make_edition(publishing=self.publishing, number="1", variant="A")
        make_edition(publishing=twin, number="1", variant="A")
        data = self.series()
        self.assertIsNone(data["series"]["publishing"])
        self.assertEqual(variants_by_label(data)["1A"]["probable"], [])

    def test_exclusives_only_match_exclusives(self) -> None:
        regular = make_edition(publishing=self.publishing, number="1", variant="A")
        make_edition(publishing=self.publishing, number="1", variant="A", retailer_exclusive="Walmart")
        make_edition(publishing=self.publishing, number="1", variant="L", event_exclusive="SDCC 2023")
        make_edition(publishing=self.publishing, number="1", variant="B&N")  # regular, unlike 1B&N
        variants = variants_by_label(self.series())
        self.assertEqual([item["id"] for item in variants["1A"]["probable"]], [regular.pk])
        self.assertEqual((variants["1L"]["probable"], variants["1B&N"]["probable"]), ([], []))
        store = make_edition(publishing=self.publishing, number="1", variant="B&N", retailer_exclusive="Barnes & Noble")
        probable = variants_by_label(self.series())["1B&N"]["probable"]
        self.assertEqual([(item["id"], item["cover"]) for item in probable], [(store.pk, "#1 Barnes & Noble B&N")])

    def test_a_stated_printing_must_match(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="U", printing="1st")
        self.assertEqual(variants_by_label(self.series())["1U"]["probable"], [])
        second = make_edition(publishing=self.publishing, number="1", variant="U", printing="2nd")
        probable = variants_by_label(self.series())["1U"]["probable"]
        self.assertEqual([(item["id"], item["cover"]) for item in probable], [(second.pk, "#1 U · 2nd")])

    def test_without_a_stated_printing_every_printing_is_offered_first_one_first(self) -> None:
        second = make_edition(publishing=self.publishing, number="1", variant="A", printing="2nd")
        first = make_edition(publishing=self.publishing, number="1", variant="A", printing="1st")
        probable = variants_by_label(self.series())["1A"]["probable"]
        self.assertEqual([item["id"] for item in probable], [first.pk, second.pk])

    def test_query_count_does_not_grow_with_the_candidates(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            mycomicshop.fetch_series(BATMAN_TID)  # cached: only Comi is queried below
        make_edition(publishing=self.publishing, number="1", variant="A")
        # IVIDs, editorials, publishings by title (+ their editorials), candidate editions.
        with self.assertNumQueries(5):
            mycomicshop_series(BATMAN_TID)
        for letter in ("L", "P"):
            make_edition(publishing=self.publishing, number="1", variant=letter)
        make_edition(publishing=self.publishing, number="1", variant="U", printing="2nd")
        make_edition(publishing=self.publishing, number="1", variant="B&N", mycomicshop_id=58320841)
        with self.assertNumQueries(5):
            data = mycomicshop_series(BATMAN_TID)
        variants = variants_by_label(data)
        self.assertEqual(
            sorted(label for label, variant in variants.items() if variant["probable"]), ["1A", "1L", "1P", "1U"]
        )
        self.assertIsNotNone(variants["1B&N"]["existing"])


class LinkEndpointTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_catalog_mycomicshop_link")
        self.publishing = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        self.edition = make_edition(publishing=self.publishing, number="1", variant="L")
        self.admin = make_user(role=ADMINISTRATORS)

    def post(self, **data: Any) -> Any:
        data.setdefault("edition", self.edition.pk)
        data.setdefault("ivid", IVID_1L)
        return self.client.post(self.url, data)

    def test_only_gestion_can_link_and_only_by_post(self) -> None:
        self.assertEqual(self.post().status_code, 302)  # anonymous: to the login
        self.client.force_login(make_user())
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url, {"edition": self.edition.pk, "ivid": IVID_1L}).status_code, 405)
        self.edition.refresh_from_db()
        self.assertIsNone(self.edition.mycomicshop_id)

    def test_link_stores_the_ivid_with_a_version_and_a_log_entry(self) -> None:
        self.client.force_login(self.admin)
        kept = ("variant", "printing", "issue_id", "notes", "image", "thumbnail", "cover_price", "release_date")
        before = Edition.objects.values(*kept).get(pk=self.edition.pk)
        versions = versions_of(Edition, self.edition.pk).count()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Edition.objects.values(*kept).get(pk=self.edition.pk), before)  # only the IVID changes
        self.assertEqual(
            response.json(),
            {
                "existing": {
                    "id": self.edition.pk,
                    "label": "Batman Spawn #1 L",
                    "url": reverse("manage_edition_edit", args=[self.edition.pk]),
                }
            },
        )
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.mycomicshop_id, IVID_1L)
        self.assertEqual(versions_of(Edition, self.edition.pk).count(), versions + 1)
        latest = versions_of(Edition, self.edition.pk).first()
        self.assertEqual((latest.mycomicshop_id, latest.history_user), (IVID_1L, self.admin))
        self.assertIn(str(IVID_1L), latest.history_change_reason)
        entry = LogEntry.objects.get(object_id=str(self.edition.pk), content_type__model="edition")
        self.assertEqual((entry.action_flag, entry.user), (CHANGE, self.admin))
        self.assertIn(str(IVID_1L), entry.change_message)

    def test_linking_the_same_ivid_again_is_idempotent(self) -> None:
        self.client.force_login(self.admin)
        self.assertEqual(self.post().status_code, 200)
        versions = versions_of(Edition, self.edition.pk).count()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["existing"]["id"], self.edition.pk)
        self.assertEqual(versions_of(Edition, self.edition.pk).count(), versions)
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.edition.pk)).count(), 1)

    def test_an_edition_with_another_ivid_is_a_409(self) -> None:
        self.edition.mycomicshop_id = 111
        self.edition.save()
        self.client.force_login(self.admin)
        response = self.post()
        self.assertEqual(
            (response.status_code, response.json()),
            (409, {"error": "Batman Spawn #1 L is already linked to another MyComicShop cover (111)."}),
        )
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.mycomicshop_id, 111)

    def test_an_ivid_held_by_another_edition_is_a_409(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="M", mycomicshop_id=IVID_1L)
        self.client.force_login(self.admin)
        response = self.post()
        self.assertEqual(
            (response.status_code, response.json()),
            (409, {"error": "Batman Spawn #1 M already has this MyComicShop ID."}),
        )
        self.edition.refresh_from_db()
        self.assertIsNone(self.edition.mycomicshop_id)
        self.assertFalse(LogEntry.objects.filter(object_id=str(self.edition.pk)).exists())

    def test_an_unknown_edition_is_a_404(self) -> None:
        self.client.force_login(self.admin)
        missing = Edition.objects.order_by("-pk").values_list("pk", flat=True).first() + 100
        response = self.post(edition=missing)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "The edition does not exist: it may have been deleted."})

    def test_invalid_values_are_a_400(self) -> None:
        self.client.force_login(self.admin)
        for data in ({"edition": ""}, {"ivid": ""}, {"ivid": "abc"}, {"ivid": "0"}, {"edition": "-1"}):
            with self.subTest(data=data):
                response = self.post(**data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        self.edition.refresh_from_db()
        self.assertIsNone(self.edition.mycomicshop_id)

    def test_csrf_is_required(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(self.url, {"edition": self.edition.pk, "ivid": IVID_1L})
        self.assertEqual(response.status_code, 403)
        self.edition.refresh_from_db()
        self.assertIsNone(self.edition.mycomicshop_id)

    def test_the_edition_form_knows_the_link_url_and_the_open_edition(self) -> None:
        self.client.force_login(self.admin)
        page = self.client.get(reverse("manage_edition_new"))
        self.assertContains(page, self.url)
        self.assertIsNone(page.context["form_data"]["editionId"])
        page = self.client.get(reverse("manage_edition_edit", args=[self.edition.pk]))
        # The JS fills the open edition's MyComicShop ID field when it is the one linked.
        self.assertEqual(page.context["form_data"]["editionId"], self.edition.pk)
        self.assertContains(page, f'"editionId": {self.edition.pk}')
