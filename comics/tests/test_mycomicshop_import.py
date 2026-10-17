"""Importing from MyComicShop: a variant of a series page as a SourceIssue, the mapper's edition-level
values (and Metron/Comic Vine without them), the lookup with its series id, the series endpoint of the
variants modal and Edition.mycomicshop_id. HTTP is mocked with the task 1 fixtures."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.catalog_sources import metron, mycomicshop
from comics.catalog_sources.errors import InvalidLookup, NotFound
from comics.catalog_sources.http import decode_html
from comics.catalog_sources.mapper import build_proposal
from comics.catalog_sources.mycomicshop import McsCredit, McsSeries, first_issue_date, parse_series, to_source_issue
from comics.catalog_sources.service import load_proposal, mycomicshop_series, source_choices
from comics.catalog_sources.types import SourceIssue
from comics.models import Edition, Issue, Publishing
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_edition, make_publishing, make_user
from .test_catalog_sources import (
    METRON_ISSUE,
    NO_CREDENTIALS,
    OPEN_URL,
    CatalogTestCase,
    FakeResponse,
    http_error,
    json_response,
)
from .test_mycomicshop import BATMAN_SPAWN, TMNT, VAMPIRELLA

BATMAN_TID, TMNT_TID, VAMPI_TID = 57389539, 58451662, 65211284
PAGES = {BATMAN_TID: BATMAN_SPAWN, TMNT_TID: TMNT, VAMPI_TID: VAMPIRELLA}


def series_of(tid: int) -> McsSeries:
    series = parse_series(decode_html(PAGES[tid]))
    series.tid = tid
    return series


def source_issue(tid: int, label: str) -> SourceIssue:
    series = series_of(tid)
    return to_source_issue(series, next(variant for variant in series.variants if variant.label == label))


def page_answer(opener: Any, request: Any) -> FakeResponse:
    """The fixture page of the TID the request asks for."""
    return FakeResponse(PAGES[int(request.full_url.rsplit("=", 1)[1])])


def neutral_issue(**changes: Any) -> SourceIssue:
    """A minimal SourceIssue (nothing in Comi matches it), with `changes`."""
    values: dict[str, Any] = {
        "source": "mycomicshop",
        "source_name": "MyComicShop",
        "source_id": 1,
        "source_url": "",
        "publisher": "",
        "series_name": "",
        "series_volume": None,
        "series_year": None,
        "series_type": "",
        "language": "en",
        "number": "1",
        "store_date": None,
        "cover_date": None,
        "price": None,
        "price_currency": "USD",
        "description": "",
        "image": "",
        "credits": [],
        "variants": [],
        "metron_id": None,
        "comicvine_id": None,
        "gcd_id": None,
    }
    values.update(changes)
    return SourceIssue(**values)


class ToSourceIssueTests(ComiTestCase):
    def test_retailer_incentive_with_ratio(self) -> None:
        issue = source_issue(BATMAN_TID, "1L")
        self.assertEqual(
            (issue.source, issue.source_name, issue.source_id, issue.mycomicshop_id, issue.source_series_id),
            ("mycomicshop", "MyComicShop", 57507069, 57507069, BATMAN_TID),
        )
        self.assertEqual(issue.source_url, "https://www.mycomicshop.com/search?TID=57389539#57507069")
        self.assertEqual(
            (issue.publisher, issue.series_name, issue.series_year, issue.series_volume, issue.language),
            ("DC", "Batman Spawn", 2023, None, "en"),
        )
        self.assertEqual((issue.number, issue.variant, issue.ratio, issue.printing), ("1", "L", "1:25", None))
        self.assertEqual((issue.store_date, issue.cover_date), (None, date(2023, 2, 1)))
        self.assertEqual((issue.price, issue.price_currency), (Decimal("6.99"), "USD"))
        self.assertTrue(issue.image.startswith("https://media.mycomicshop.com/n_iv/600/"))
        self.assertEqual(issue.variants, [])
        self.assertEqual((issue.metron_id, issue.comicvine_id, issue.gcd_id), (None, None, None))
        self.assertEqual((issue.limited_to, issue.retailer_exclusive, issue.event_exclusive), ("", "", ""))

    def test_store_exclusive_with_print_run(self) -> None:
        issue = source_issue(TMNT_TID, "1CARNIVORE.B")
        self.assertEqual(issue.publisher, "IDW Publishing")  # the full name, not the title's "IDW"
        self.assertEqual(
            (issue.variant, issue.retailer_exclusive, issue.limited_to), ("CARNIVORE.B", "Carnivore Comics", "1200")
        )
        self.assertIsNone(issue.cover_date)  # the page only gives the year
        self.assertEqual([c.name for c in issue.credits if c.is_cover], ["Tiago Da Silva"])

    def test_event_exclusive(self) -> None:
        issue = source_issue(TMNT_TID, "4NYCC")
        self.assertEqual(
            (issue.number, issue.event_exclusive, issue.retailer_exclusive), ("4", "New York Comic Con", "")
        )

    def test_second_printing(self) -> None:
        issue = source_issue(BATMAN_TID, "1U")
        self.assertEqual((issue.variant, issue.printing, issue.cover_date), ("U", 2, date(2023, 4, 1)))

    def test_reprint_without_price(self) -> None:
        issue = source_issue(BATMAN_TID, "1CUSTOM.B")
        self.assertIsNone(issue.price)
        self.assertEqual((issue.variant, issue.retailer_exclusive), ("CUSTOM.B", "McFarlane Toys Store"))

    def test_solicitation_codes_style(self) -> None:
        issue = source_issue(VAMPI_TID, "1A")
        self.assertEqual(issue.publisher, "Dynamite Entertainment")
        self.assertEqual(
            [(c.name, c.roles) for c in issue.credits],
            [("Erica Schultz", ["Writer"]), ("Valentina Pinti", ["Artist"]), ("Stjepan Sejic", ["Cover"])],
        )
        self.assertTrue(issue.description.startswith("Two titans of comics collide"))
        self.assertEqual(issue.price, Decimal("5.99"))

    def test_first_issue_date(self) -> None:
        self.assertEqual(first_issue_date(series_of(TMNT_TID)), date(2023, 6, 1))  # 1A; 1CARNIVORE.B has no month
        self.assertEqual(first_issue_date(series_of(BATMAN_TID)), date(2023, 2, 1))  # not the reprint's August
        tmnt = series_of(TMNT_TID)
        undated_first = replace(tmnt, variants=[v for v in tmnt.variants if v.label in ("1CARNIVORE.B", "3A")])
        self.assertIsNone(first_issue_date(undated_first))  # #3's date is not the first issue's


class MapperEditionValuesTests(CatalogTestCase):
    def test_edition_values_from_the_variant(self) -> None:
        proposal = build_proposal(source_issue(BATMAN_TID, "1L"))
        self.assertEqual(
            proposal.edition,
            {
                "variant": "L",
                "printing": "1st",
                "ratio": "1:25",
                "mycomicshop_id": "57507069",
                "number": "1",
                "release_date": "2023-02-01",
                "cover_price": "6.99",
            },
        )
        self.assertIn(
            "Only the cover date is known: the release date is usually a few months earlier.", proposal.warnings
        )
        self.assertEqual(proposal.identifiers, {"metron_id": None, "comicvine_id": None, "gcd_id": None})
        self.assertEqual(proposal.variants, [])
        self.assertTrue(proposal.cover_url.startswith("https://media.mycomicshop.com/n_iv/600/"))
        self.assertEqual(proposal.source_url, "https://www.mycomicshop.com/search?TID=57389539#57507069")

    def test_exclusives_print_run_and_printing(self) -> None:
        carnivore = build_proposal(source_issue(TMNT_TID, "1CARNIVORE.B")).edition
        self.assertEqual(
            (carnivore["variant"], carnivore["retailer_exclusive"], carnivore["limited_to"]),
            ("CARNIVORE.B", "Carnivore Comics", "1200"),
        )
        self.assertNotIn("release_date", carnivore)
        self.assertEqual(
            build_proposal(source_issue(TMNT_TID, "4NYCC")).edition["event_exclusive"], "New York Comic Con"
        )
        self.assertEqual(build_proposal(source_issue(BATMAN_TID, "1U")).edition["printing"], "2nd")

    def test_values_comi_does_not_accept_are_left_out_with_a_warning(self) -> None:
        issue = neutral_issue(
            variant="V" * 31,
            printing=7,
            ratio="1:1000",
            limited_to="12345678901",
            retailer_exclusive="S" * 61,
            event_exclusive="Comic Con",
        )
        proposal = build_proposal(issue)
        # Left out, not sent empty: the form keeps the value it has.
        self.assertEqual(proposal.edition, {"event_exclusive": "Comic Con", "number": "1"})
        rejected = [warning for warning in proposal.warnings if "Comi does not accept" in warning]
        self.assertEqual(len(rejected), 5)
        self.assertIn("Ratio: Comi does not accept “1:1000” from MyComicShop", rejected[2])
        self.assertTrue(rejected[1].startswith("Printing: Comi does not accept “7th”"))

    def test_an_ivid_already_in_comi_is_warned_about(self) -> None:
        edition = make_edition(mycomicshop_id=57507069)
        with CaptureQueriesContext(connection) as without_id:
            build_proposal(neutral_issue())
        with CaptureQueriesContext(connection) as with_id:
            warnings = build_proposal(neutral_issue(mycomicshop_id=57507069)).warnings
        self.assertEqual(len(with_id) - len(without_id), 1)
        self.assertIn(
            f"{edition.short_name} already has this MyComicShop ID: saving it on another edition will fail.", warnings
        )
        other = build_proposal(neutral_issue(mycomicshop_id=1)).warnings
        self.assertFalse(any("MyComicShop ID" in warning for warning in other))

    def test_uncertain_credits_are_named_in_a_warning(self) -> None:
        issue = neutral_issue(
            credits=[
                McsCredit(name="Jae Lee", roles=["Cover"], uncertain=True),
                McsCredit(name="Erica Schultz", roles=["Writer"]),
            ]
        )
        warnings = build_proposal(issue).warnings
        self.assertIn(
            "Check the artist name “Jae Lee”: MyComicShop's text does not make clear where it ends.", warnings
        )
        self.assertFalse(any("Erica Schultz" in warning for warning in warnings))

    def test_metron_keeps_the_main_cover_of_the_first_printing(self) -> None:
        edition = build_proposal(metron.to_source_issue(METRON_ISSUE)).edition
        self.assertEqual((edition["variant"], edition["printing"]), ("A", "1st"))
        for name in ("ratio", "limited_to", "retailer_exclusive", "event_exclusive", "mycomicshop_id"):
            with self.subTest(name=name):
                self.assertNotIn(name, edition)


class LoadProposalTests(CatalogTestCase):
    def test_new_publishing_takes_the_first_issue_from_the_same_page(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer) as open_url:
            proposal = load_proposal("mycomicshop", 57507069, BATMAN_TID)
        self.assertEqual(open_url.call_count, 1)  # the series details reuse the cached page
        new = proposal.new_publishing
        self.assertEqual(
            (new.publishing_title, new.year, new.language, new.date), ("Batman Spawn", 2023, "en", "2023-02-01")
        )
        self.assertEqual(new.new_editorial, {"name": "DC", "country": ""})
        self.assertIsNone(new.metron_series_id)
        self.assertIn("Only the first issue's cover date is known: check the start date.", new.warnings)

    def test_an_existing_publishing_matches_by_title_and_year(self) -> None:
        publishing = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        with mock.patch(OPEN_URL, side_effect=page_answer):
            proposal = load_proposal("mycomicshop", 58025365, BATMAN_TID)
        self.assertEqual(proposal.publishing["id"], publishing.pk)
        self.assertIsNone(proposal.new_publishing)

    def test_the_series_is_required_and_the_variant_must_be_in_it(self) -> None:
        with mock.patch(OPEN_URL) as open_url, self.assertRaises(InvalidLookup) as raised:
            load_proposal("mycomicshop", 57507069)
        open_url.assert_not_called()
        self.assertEqual(
            (raised.exception.status, raised.exception.message), (400, "MyComicShop also needs the series id.")
        )
        with mock.patch(OPEN_URL, side_effect=page_answer), self.assertRaises(NotFound) as raised:
            load_proposal("mycomicshop", 1, BATMAN_TID)
        self.assertEqual(raised.exception.message, "MyComicShop has no issue with the id 1 in the series 57389539.")


class LookupViewTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_catalog_lookup")
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def test_mycomicshop_with_its_series(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.client.get(self.url, {"source": "mycomicshop", "id": "61572909", "series": str(TMNT_TID)})
        self.assertEqual(response.status_code, 200)
        proposal = response.json()["proposal"]
        self.assertEqual((proposal["source"], proposal["source_id"]), ("mycomicshop", 61572909))
        self.assertEqual(proposal["edition"]["mycomicshop_id"], "61572909")
        self.assertEqual(proposal["edition"]["retailer_exclusive"], "Carnivore Comics")
        self.assertFalse(Edition.objects.exists() or Issue.objects.exists() or Publishing.objects.exists())

    def test_mycomicshop_without_a_valid_series_is_a_400(self) -> None:
        cases = [
            ({}, "MyComicShop also needs the series id."),
            ({"series": " "}, "MyComicShop also needs the series id."),
            ({"series": "abc"}, "Enter the series' number in the catalogue, e.g. 58451662."),
            ({"series": "0"}, "Enter the series' number in the catalogue, e.g. 58451662."),
        ]
        for params, message in cases:
            with self.subTest(params=params), mock.patch(OPEN_URL) as open_url:
                response = self.client.get(self.url, {"source": "mycomicshop", "id": "61572909", **params})
                self.assertEqual((response.status_code, response.json()), (400, {"error": message}))
                open_url.assert_not_called()

    def test_a_variant_of_another_series_is_a_404(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.client.get(self.url, {"source": "mycomicshop", "id": "61572909", "series": str(BATMAN_TID)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), {"error": "MyComicShop has no issue with the id 61572909 in the series 57389539."}
        )

    def test_metron_ignores_the_series(self) -> None:
        for series in ("", "123", "not-a-number", "9" * 300):
            with self.subTest(series=series), mock.patch(OPEN_URL, return_value=json_response(METRON_ISSUE)):
                response = self.client.get(self.url, {"source": "metron", "id": "15439", "series": series})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["proposal"]["edition"]["variant"], "A")


class SeriesEndpointTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_catalog_mycomicshop_series")

    def test_collectors_get_403_and_anonymous_users_the_login(self) -> None:
        with mock.patch(OPEN_URL) as open_url:
            self.assertEqual(self.client.get(self.url, {"id": TMNT_TID}).status_code, 302)
            self.client.force_login(make_user())
            self.assertEqual(self.client.get(self.url, {"id": TMNT_TID}).status_code, 403)
        open_url.assert_not_called()
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.assertEqual(self.client.post(self.url, {"id": TMNT_TID}).status_code, 405)

    def test_the_series_and_its_variants_grouped_by_number(self) -> None:
        publishing = make_publishing(publishing_title="TMNT vs. Street Fighter", year=2023)
        edition = make_edition(publishing=publishing, number="1", variant="A", mycomicshop_id=58639767)
        self.client.force_login(make_user(role=ADMINISTRATORS))
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.client.get(self.url, {"id": str(TMNT_TID)})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["series"],
            {
                "tid": TMNT_TID,
                "title": "Teenage Mutant Ninja Turtles vs. Street Fighter",
                "year": 2023,
                "publisher": "IDW Publishing",
                "url": "https://www.mycomicshop.com/search?TID=58451662",
            },
        )
        self.assertEqual(
            [(issue["number"], [v["label"] for v in issue["variants"]]) for issue in data["issues"]],
            [("1", ["1A", "1CARNIVORE.B"]), ("3", ["3A", "3RID"]), ("4", ["4NYCC"])],
        )
        first, carnivore = data["issues"][0]["variants"]
        self.assertEqual(
            first["existing"],
            {
                "id": edition.pk,
                "label": "TMNT vs. Street Fighter #1 A",
                "url": reverse("manage_edition_edit", args=[edition.pk]),
            },
        )
        self.assertEqual(
            (first["ivid"], first["variant"], first["date"], first["price"]), (58639767, "A", "2023-06", "3.99")
        )
        self.assertTrue(first["thumb_url"].startswith("https://media.mycomicshop.com/n_iv/120/"))
        self.assertTrue(first["cover_url"].startswith("https://media.mycomicshop.com/n_iv/600/"))
        self.assertEqual(
            {
                key: carnivore[key]
                for key in ("variant", "date", "limited_to", "retailer_exclusive", "event_exclusive", "existing")
            },
            {
                "variant": "CARNIVORE.B",
                "date": "2023",
                "limited_to": "1200",
                "retailer_exclusive": "Carnivore Comics",
                "event_exclusive": "",
                "existing": None,
            },
        )
        self.assertTrue(carnivore["note"].startswith("Carnivore Comics Exclusive Cover by Tiago Da Silva"))
        rid = data["issues"][1]["variants"][1]
        self.assertEqual((rid["ratio"], rid["printing"], rid["limited_to"]), ("1:250", "", ""))
        self.assertEqual(data["issues"][2]["variants"][0]["event_exclusive"], "New York Comic Con")

    def test_one_query_for_the_existing_editions(self) -> None:
        make_edition(mycomicshop_id=58639767)
        make_edition(mycomicshop_id=59040682)
        with mock.patch(OPEN_URL, side_effect=page_answer):
            mycomicshop.fetch_series(TMNT_TID)  # cached: only the editions are queried below
        with self.assertNumQueries(1):
            data = mycomicshop_series(TMNT_TID)
        existing = [v["ivid"] for issue in data["issues"] for v in issue["variants"] if v["existing"]]
        self.assertEqual(existing, [58639767, 59040682])

    def test_printing_is_an_ordinal(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            data = mycomicshop_series(BATMAN_TID)
        printings = {v["label"]: v["printing"] for issue in data["issues"] for v in issue["variants"]}
        self.assertEqual((printings["1U"], printings["1A"]), ("2nd", ""))

    def test_errors_are_json(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        for params in ({}, {"id": "abc"}, {"id": "0"}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        with mock.patch(OPEN_URL, side_effect=http_error(mycomicshop.series_url(5), 404)):
            response = self.client.get(self.url, {"id": "5"})
        self.assertEqual(
            (response.status_code, response.json()), (404, {"error": "MyComicShop has no series with the id 5."})
        )


class ImportBoxTests(CatalogTestCase):
    """The import box of the Gestión edition form: MyComicShop is loaded by series in a dialog."""

    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def test_mycomicshop_is_offered_and_loaded_by_series(self) -> None:
        page = self.client.get(reverse("manage_edition_new"))
        self.assertContains(page, '<option value="mycomicshop" data-requires-series>MyComicShop</option>', html=True)
        self.assertContains(page, 'data-series-label="MyComicShop series ID (TID)"')
        self.assertContains(page, "With MyComicShop, enter the series ID and pick the cover among its variants.")
        self.assertNotContains(page, "Fill in the form with an issue from Metron or Comic Vine.")
        self.assertContains(page, reverse("manage_catalog_mycomicshop_series"))

    def test_the_series_dialog_is_on_the_page_outside_the_form(self) -> None:
        page = self.client.get(reverse("manage_edition_new"))
        for marker in (
            'id="catalog-series-dialog"',
            'aria-labelledby="catalog-series-title"',
            'id="catalog-series-issues"',
            'id="catalog-series-status"',
            'id="catalog-series-import"',
        ):
            with self.subTest(marker=marker):
                self.assertContains(page, marker, count=1)
        html = page.content.decode()
        # A sibling of the edition form, so the variant radios are never submitted with it.
        self.assertGreater(html.index('id="catalog-series-dialog"'), html.index("</form>"))

    @override_settings(**NO_CREDENTIALS)
    def test_the_box_is_always_on(self) -> None:
        page = self.client.get(reverse("manage_edition_new"))
        self.assertContains(page, 'id="catalog-load"')
        self.assertContains(page, 'id="catalog-series-dialog"')
        self.assertNotContains(page, "Importing is off")

    def test_source_choices_say_which_sources_are_loaded_by_series(self) -> None:
        by_series = {choice["key"]: choice["requires_series"] for choice in source_choices()}
        self.assertEqual(by_series, {"metron": False, "comicvine": False, "mycomicshop": True})


class EditionMycomicshopIdTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.publishing = make_publishing(publishing_title="Batman Spawn", year=2023)

    def post(self, **changes: Any) -> Any:
        data = {
            "publishing": self.publishing.pk,
            "number": "1",
            "variant": "L",
            "printing": "1st",
            "format": "single_issue",
            "rarity": "common",
            "release_date": "2023-02-01",
            "cover_price": "6.99",
            "content": "single",
        }
        data.update(changes)
        return self.client.post(reverse("manage_edition_new"), data)

    def test_saved_from_the_gestion_form(self) -> None:
        page = self.client.get(reverse("manage_edition_new"))
        self.assertContains(page, 'name="mycomicshop_id"')
        self.assertEqual(self.post(mycomicshop_id="57507069").status_code, 302)
        self.assertEqual(Edition.objects.get().mycomicshop_id, 57507069)
        self.assertEqual(Edition.history.get().mycomicshop_id, 57507069)  # versioned like the other fields

    def test_unique_when_set(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="A", mycomicshop_id=57507069)
        response = self.post(mycomicshop_id="57507069")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Another edition already has this MyComicShop ID.")
        # Without an id, any number of editions.
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.post(variant="B").status_code, 302)
        self.assertEqual(Edition.objects.filter(mycomicshop_id__isnull=True).count(), 2)

    def test_the_database_refuses_a_duplicate(self) -> None:
        make_edition(mycomicshop_id=42)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_edition(mycomicshop_id=42)
