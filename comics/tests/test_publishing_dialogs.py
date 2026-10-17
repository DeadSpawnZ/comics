"""Creating a publishing (and its title and editorial) from the Gestión edition form, and the series
ids it stores: the "Create" JSON endpoints, the publishing proposal of the catalogue import (first
issue's date, fallbacks, warnings, match by series id; HTTP mocked, never the network), the series
id fields and their identification-numbers panel on the publishing page."""

import copy
import dataclasses
import json
import re
from typing import Any
from unittest import mock
from urllib.request import Request

from django.contrib.admin.models import ADDITION, LogEntry
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from comics.catalog_sources import comicvine, metron
from comics.catalog_sources.mapper import SeriesLoaders, build_proposal
from comics.catalog_sources.service import load_proposal
from comics.catalog_sources.types import SourceSeries
from comics.forms import TitleDialogForm
from comics.models import Editorial, Publishing, Title
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_editorial, make_publishing, make_user
from .test_catalog_sources import COMICVINE, METRON_ISSUE, OPEN_URL, CatalogTestCase, http_error, json_response

SERIES = {
    "id": 1440,
    "name": "Marvel Two-in-One Annual",
    "volume": 1,
    "year_began": 1976,
    "cv_id": 2865,
    "gcd_id": 4321,
}
ISSUE_LIST = {
    "count": 3,
    "next": None,
    "results": [
        {"id": 15433, "number": "2", "cover_date": "1977-11-01", "store_date": "1977-08-09"},
        {"id": 15432, "number": "1", "cover_date": "1976-10-01", "store_date": "1976-07-13"},
        {"id": 15499, "number": "8", "cover_date": None, "store_date": None},
        "not an issue",
    ],
}
PUBLISHER = {"id": 1, "name": "Marvel", "country": "US"}


def metron_answers(
    issue: dict[str, Any] | None = None,
    series: Any = SERIES,
    issues: Any = ISSUE_LIST,
    publisher: Any = PUBLISHER,
) -> mock.Mock:
    """An open_url replacement answering each Metron resource (an exception is raised instead)."""

    def answer(opener: Any, request: Request) -> Any:
        url = request.full_url
        if "/issue/?series_id=" in url:
            result = issues
        elif "/series/" in url:
            result = series
        elif "/publisher/" in url:
            result = publisher
        else:
            result = issue or METRON_ISSUE
        if isinstance(result, Exception):
            raise result
        return json_response(result)

    return mock.Mock(side_effect=answer)


def requested(open_url: mock.Mock) -> list[str]:
    return [call.args[1].full_url for call in open_url.call_args_list]


# ---------- Publishing proposal (mapper and clients) ----------


class SeriesProposalTests(CatalogTestCase):
    def load(self, open_url: mock.Mock, source: str = "metron", issue_id: int = 15439) -> dict[str, Any]:
        with mock.patch(OPEN_URL, open_url):
            return load_proposal(source, issue_id).as_dict()

    def test_metron_issue_names_its_series_and_publisher(self) -> None:
        source = metron.to_source_issue(METRON_ISSUE)
        self.assertEqual((source.metron_series_id, source.source_publisher_id), (1440, 1))

    def test_new_publishing_takes_the_first_issue_store_date(self) -> None:
        open_url = metron_answers()
        proposal = self.load(open_url)
        self.assertIsNone(proposal["publishing"])
        self.assertEqual(
            proposal["new_publishing"],
            {
                "publishing_title": "Marvel Two-in-One Annual",
                "year": 1976,
                "serie": "1st",
                "language": "en",
                "date": "1976-07-13",
                "metron_series_id": 1440,
                "comicvine_volume_id": 2865,
                "gcd_series_id": 4321,
                "editorial": None,
                "new_editorial": {"name": "Marvel", "country": "US"},
                "warnings": [],
            },
        )
        urls = requested(open_url)
        self.assertEqual(
            urls,
            [
                "https://metron.cloud/api/issue/15439/",
                "https://metron.cloud/api/series/1440/",
                "https://metron.cloud/api/issue/?series_id=1440",
                "https://metron.cloud/api/publisher/1/",
            ],
        )
        # Everything is cached: loading it again costs no request.
        self.load(open_url)
        self.assertEqual(open_url.call_count, 4)

    def test_the_cover_date_when_the_first_issue_has_no_store_date(self) -> None:
        issues = {"results": [{"id": 1, "number": "1", "cover_date": "1976-10-01", "store_date": None}]}
        proposal = self.load(metron_answers(issues=issues))["new_publishing"]
        self.assertEqual(proposal["date"], "1976-10-01")
        self.assertEqual(proposal["warnings"], ["Only the first issue's cover date is known: check the start date."])

    def test_january_first_of_the_start_year_when_the_series_cannot_be_read(self) -> None:
        error = http_error("https://metron.cloud/api/", 503)
        proposal = self.load(metron_answers(series=error, issues=error, publisher=error))["new_publishing"]
        self.assertEqual((proposal["date"], proposal["year"]), ("1976-01-01", 1976))
        self.assertEqual((proposal["comicvine_volume_id"], proposal["gcd_series_id"]), (None, None))
        self.assertEqual(proposal["metron_series_id"], 1440)  # from the issue itself
        self.assertEqual(proposal["new_editorial"], {"name": "Marvel", "country": ""})
        self.assertEqual(
            proposal["warnings"], ["The first issue's date is unknown: the start date is January 1, 1976. Check it."]
        )

    def test_answers_of_another_resource_are_ignored(self) -> None:
        # E.g. a series answer that is not series 1440: its ids are not taken.
        proposal = self.load(metron_answers(series={"id": 99, "cv_id": 1}, issues={"detail": "x"}))["new_publishing"]
        self.assertIsNone(proposal["comicvine_volume_id"])
        self.assertEqual(proposal["date"], "1976-01-01")

    def test_no_date_at_all(self) -> None:
        issue = copy.deepcopy(METRON_ISSUE)
        issue["series"]["year_began"] = None
        proposal = self.load(metron_answers(issue=issue, issues={"results": []}))["new_publishing"]
        self.assertEqual((proposal["date"], proposal["year"]), ("", None))
        self.assertIn("The start date of the series is unknown: enter it.", proposal["warnings"])

    def test_a_known_editorial_is_proposed_and_its_publisher_not_requested(self) -> None:
        marvel = make_editorial(name="MARVEL")
        open_url = metron_answers()
        proposal = self.load(open_url)["new_publishing"]
        self.assertEqual(
            (proposal["editorial"], proposal["new_editorial"]), ({"id": marvel.pk, "name": "MARVEL"}, None)
        )
        self.assertFalse(any("/publisher/" in url for url in requested(open_url)))

    def test_only_the_series_ids_are_requested_when_the_publishing_matches(self) -> None:
        make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976, language="en")
        open_url = metron_answers()
        proposal = self.load(open_url)
        self.assertIsNotNone(proposal["publishing"])
        self.assertIsNone(proposal["new_publishing"])
        # The series (for its Comic Vine id, matched before the title), not its issues nor publisher.
        self.assertEqual(
            requested(open_url), ["https://metron.cloud/api/issue/15439/", "https://metron.cloud/api/series/1440/"]
        )

    def test_a_publishing_created_from_comic_vine_is_matched_by_a_later_metron_import(self) -> None:
        def comicvine_answer(opener: Any, request: Request) -> Any:
            return json_response(COMICVINE["issue"] if "/issue/" in request.full_url else COMICVINE["volume"])

        proposed = self.load(mock.Mock(side_effect=comicvine_answer), "comicvine", 21801)["new_publishing"]
        self.assertEqual((proposed["comicvine_volume_id"], proposed["metron_series_id"]), (2865, None))
        manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(manager)
        values = {
            "title": Title.objects.create(name="Marvel Two-in-One").pk,
            "publishing_title": "Marvel 2-in-1 Annual",  # renamed by hand: the title does not match
            "serie": "1st",
            "language": "en",
            "date": proposed["date"],
            "editorials": [make_editorial(name="Marvel").pk],
            "comicvine_volume_id": proposed["comicvine_volume_id"],
        }
        response = self.client.post(
            reverse("manage_publishing_create_api"), {f"new_publishing-{key}": value for key, value in values.items()}
        )
        self.assertEqual(response.status_code, 200)

        proposal = self.load(metron_answers())
        self.assertEqual(proposal["publishing"]["id"], response.json()["id"])
        self.assertIsNone(proposal["new_publishing"])
        self.assertEqual(
            proposal["warnings"], ["Only the cover date is known: the release date is usually a few months earlier."]
        )

    def test_conflicting_series_ids_keep_metron_and_warn(self) -> None:
        by_metron = make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976, metron_series_id=1440)
        by_comicvine = make_publishing(publishing_title="Marvel 2-in-1 Annual", year=1976, comicvine_volume_id=2865)
        proposal = self.load(metron_answers())
        self.assertEqual(proposal["publishing"]["id"], by_metron.pk)
        self.assertIn(
            f"The Metron series is {by_metron.select_label}, but its Comic Vine volume is "
            f"{by_comicvine.select_label}: the first one is used. Check whether they are the same publishing.",
            proposal["warnings"],
        )

    def test_the_comic_vine_id_of_the_metron_series_matches_before_the_title(self) -> None:
        make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976, language="en")
        held = make_publishing(publishing_title="Renamed", year=1999, comicvine_volume_id=2865)
        self.assertEqual(self.load(metron_answers())["publishing"]["id"], held.pk)

    def test_a_language_or_country_comi_does_not_have_is_left_to_the_user(self) -> None:
        issue = copy.deepcopy(METRON_ISSUE)
        issue["series"]["language"] = "fr"
        proposal = self.load(metron_answers(issue=issue, publisher={**PUBLISHER, "country": "fr"}))["new_publishing"]
        self.assertEqual(proposal["language"], "")
        self.assertEqual(proposal["new_editorial"], {"name": "Marvel", "country": ""})
        self.assertEqual(
            proposal["warnings"],
            [
                "The language “fr” is not one of Comi's: choose it yourself.",
                "The publisher's country “FR” is not one of Comi's: choose it yourself.",
            ],
        )

    def test_comic_vine_falls_back_to_the_start_year_without_more_requests(self) -> None:
        def answer(opener: Any, request: Request) -> Any:
            return json_response(COMICVINE["issue"] if "/issue/" in request.full_url else COMICVINE["volume"])

        open_url = mock.Mock(side_effect=answer)
        proposal = self.load(open_url, "comicvine", 21801)["new_publishing"]
        self.assertEqual(open_url.call_count, 2)  # the issue and its volume, as before
        self.assertEqual((proposal["date"], proposal["year"], proposal["serie"]), ("1976-01-01", 1976, ""))
        self.assertEqual((proposal["metron_series_id"], proposal["comicvine_volume_id"]), (None, 2865))
        self.assertEqual(proposal["new_editorial"], {"name": "Marvel", "country": ""})
        self.assertEqual(
            proposal["warnings"],
            [
                "The first issue's date is unknown: the start date is January 1, 1976. Check it.",
                "Comic Vine does not say the language of the series: check it.",
            ],
        )

    def test_first_issue_is_the_earliest_dated_one(self) -> None:
        self.assertEqual(metron.first_issue(ISSUE_LIST["results"])["id"], 15432)
        self.assertEqual(metron.first_issue([{"id": 1}]), {})
        self.assertEqual(metron.first_issue(None), {})


class MatchBySeriesIdTests(CatalogTestCase):
    def test_the_metron_series_id_matches_before_the_title(self) -> None:
        held = make_publishing(publishing_title="Another name", year=1999, language="es", metron_series_id=1440)
        make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976, language="en")
        series = SeriesLoaders(ids=mock.Mock(return_value=SourceSeries()), details=mock.Mock())
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE), series)
        self.assertEqual(proposal.publishing["id"], held.pk)
        self.assertEqual((proposal.publishing_candidates, proposal.new_publishing), ([], None))
        series.details.assert_not_called()  # nothing to propose

    def test_metron_wins_over_comic_vine(self) -> None:
        by_cv = make_publishing(publishing_title="By Comic Vine", comicvine_volume_id=2865)
        by_metron = make_publishing(publishing_title="By Metron", metron_series_id=1440)
        source = dataclasses.replace(metron.to_source_issue(METRON_ISSUE), comicvine_volume_id=2865)
        self.assertEqual(build_proposal(source).publishing["id"], by_metron.pk)
        by_metron.delete()
        self.assertEqual(build_proposal(source).publishing["id"], by_cv.pk)

    def test_the_comic_vine_volume_id_matches(self) -> None:
        held = make_publishing(publishing_title="Renamed", comicvine_volume_id=2865)
        source = comicvine.to_source_issue(
            {"issue": COMICVINE["issue"]["results"], "volume": COMICVINE["volume"]["results"]}
        )
        self.assertEqual(source.comicvine_volume_id, 2865)
        self.assertEqual(build_proposal(source).publishing["id"], held.pk)


# ---------- "Create" endpoints ----------


class CreateEndpointTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.dc = make_editorial(name="DC")
        self.urls = [
            reverse("manage_title_create_api"),
            reverse("manage_editorial_create_api"),
            reverse("manage_publishing_create_api"),
        ]

    def post(self, name: str, data: dict[str, Any]) -> Any:
        return self.client.post(reverse(name), data)

    def publishing_data(self, **values: Any) -> dict[str, Any]:
        title = Title.objects.get_or_create(name="Batman")[0]
        data = {
            "title": title.pk,
            "publishing_title": "Batman",
            "serie": "3rd",
            "language": "en",
            "date": "2016-06-15",
            "year": "",
            "editorials": [self.dc.pk],
            "metron_series_id": "",
            "comicvine_volume_id": "",
            "gcd_series_id": "",
        }
        data.update(values)
        return {f"new_publishing-{key}": value for key, value in data.items()}

    def test_collectors_get_403_and_anonymous_users_the_login(self) -> None:
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 302)
        self.client.force_login(make_user())
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertEqual((Title.objects.count(), Editorial.objects.count(), Publishing.objects.count()), (0, 1, 0))

    def test_post_only(self) -> None:
        self.client.force_login(self.manager)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_the_csrf_token_is_required(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        url = reverse("manage_title_create_api")
        self.assertEqual(client.post(url, {"new_title-name": "Batman"}).status_code, 403)
        self.assertFalse(Title.objects.exists())
        client.get(reverse("manage_edition_new"))  # the page that holds the dialogs sets the token
        token = client.cookies["csrftoken"].value
        response = client.post(url, {"new_title-name": "Batman", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 200)

    def test_creates_a_title(self) -> None:
        self.client.force_login(self.manager)
        response = self.post("manage_title_create_api", {"new_title-name": "  Batman "})
        self.assertEqual(response.status_code, 200)
        title = Title.objects.get()
        self.assertEqual(response.json(), {"id": title.pk, "label": "Batman"})
        entry = LogEntry.objects.get(content_type__model="title", object_id=str(title.pk))
        self.assertEqual((entry.action_flag, entry.user_id), (ADDITION, self.manager.pk))

    def test_title_errors_are_returned_by_field(self) -> None:
        Title.objects.create(name="Batman")
        self.client.force_login(self.manager)
        for name, message in (("batman", "A title with that name already exists."), ("", "This field is required.")):
            with self.subTest(name=name):
                response = self.post("manage_title_create_api", {"new_title-name": name})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"errors": {"name": [message]}, "non_field_errors": []})
        self.assertEqual(Title.objects.count(), 1)

    def test_creates_an_editorial(self) -> None:
        self.client.force_login(self.manager)
        response = self.post(
            "manage_editorial_create_api", {"new_editorial-name": "Image", "new_editorial-country": "US"}
        )
        editorial = Editorial.objects.get(name="Image")
        self.assertEqual(response.json(), {"id": editorial.pk, "label": "Image"})
        self.assertEqual(editorial.country, "US")
        self.assertTrue(LogEntry.objects.filter(content_type__model="editorial", object_id=str(editorial.pk)).exists())

    def test_editorial_errors(self) -> None:
        make_editorial(name="Marvel")
        self.client.force_login(self.manager)
        response = self.post(
            "manage_editorial_create_api", {"new_editorial-name": "MARVEL", "new_editorial-country": "FR"}
        )
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertEqual(errors["name"], ["An editorial with that name already exists."])
        self.assertIn("country", errors)
        self.assertEqual(Editorial.objects.count(), 2)

    def test_creates_a_publishing(self) -> None:
        self.client.force_login(self.manager)
        data = self.publishing_data(metron_series_id="1440", comicvine_volume_id="2865", gcd_series_id="4321")
        response = self.post("manage_publishing_create_api", data)
        self.assertEqual(response.status_code, 200)
        publishing = Publishing.objects.get()
        self.assertEqual(
            response.json(),
            {
                "id": publishing.pk,
                "label": "Batman (2016) 3rd · EN · DC",
                "publishing_title": "Batman",
                "date": "2016-06-15",
            },
        )
        self.assertEqual(publishing.title.name, "Batman")
        self.assertEqual((publishing.year, str(publishing.date)), (2016, "2016-06-15"))  # year from the date
        self.assertEqual(list(publishing.editorials.all()), [self.dc])
        self.assertEqual(
            (publishing.metron_series_id, publishing.comicvine_volume_id, publishing.gcd_series_id), (1440, 2865, 4321)
        )
        self.assertTrue(
            LogEntry.objects.filter(content_type__model="publishing", object_id=str(publishing.pk)).exists()
        )

    def test_the_language_and_start_date_must_be_chosen(self) -> None:
        self.client.force_login(self.manager)
        response = self.post("manage_publishing_create_api", self.publishing_data(language="", date=""))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["errors"],
            {"language": ["This field is required."], "date": ["This field is required."]},
        )
        data = self.publishing_data()
        del data["new_publishing-language"], data["new_publishing-date"]  # e.g. the disabled placeholder
        response = self.post("manage_publishing_create_api", data)
        self.assertEqual(set(response.json()["errors"]), {"language", "date"})
        self.assertFalse(Publishing.objects.exists())

    def test_the_title_is_chosen_never_derived(self) -> None:
        self.client.force_login(self.manager)
        response = self.post("manage_publishing_create_api", self.publishing_data(title=""))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"], {"title": ["This field is required."]})
        self.assertFalse(Publishing.objects.exists())

    def test_a_duplicate_publishing_is_refused(self) -> None:
        make_publishing(publishing_title="Batman", year=2016, serie="3rd", language="en")
        self.client.force_login(self.manager)
        response = self.post("manage_publishing_create_api", self.publishing_data())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["non_field_errors"],
            ["A publishing with that title, year, series and language already exists."],
        )
        self.assertEqual(Publishing.objects.count(), 1)

    def test_series_ids_of_another_publishing_are_refused(self) -> None:
        make_publishing(metron_series_id=1440, comicvine_volume_id=2865)
        self.client.force_login(self.manager)
        response = self.post(
            "manage_publishing_create_api", self.publishing_data(metron_series_id="1440", comicvine_volume_id="2865")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["errors"],
            {
                "metron_series_id": ["Another publishing already has this Metron series ID."],
                "comicvine_volume_id": ["Another publishing already has this Comic Vine volume ID."],
            },
        )

    def test_a_model_refusal_rolls_back_and_is_returned(self) -> None:
        self.client.force_login(self.manager)
        with mock.patch.object(Publishing, "validate_duplicates", side_effect=ValidationError("Duplicated publishing")):
            response = self.post("manage_publishing_create_api", self.publishing_data())
        self.assertEqual((response.status_code, response.json()["non_field_errors"]), (400, ["Duplicated publishing"]))
        self.assertFalse(Publishing.objects.exists())
        self.assertFalse(LogEntry.objects.filter(content_type__model="publishing").exists())

    def test_a_row_created_meanwhile_is_a_form_error(self) -> None:
        self.client.force_login(self.manager)
        with mock.patch.object(TitleDialogForm, "save", side_effect=IntegrityError("duplicate")):
            response = self.post("manage_title_create_api", {"new_title-name": "Batman"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["non_field_errors"], ["A title with that name already exists."])


# ---------- Pages ----------


class EditionFormDialogsTests(ComiTestCase):
    def test_the_dialogs_live_outside_the_edition_form(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        edition_form = re.search(r'<form [^>]*id="edition-form".*?</form>', html, flags=re.DOTALL).group(0)
        self.assertIn('id="create-publishing" data-open-dialog="publishing-dialog"', edition_form)
        self.assertIn('id="form-draft"', edition_form)
        self.assertNotIn("publishing-dialog-form", edition_form)
        for dialog, url in (
            ("publishing-dialog-form", "manage_publishing_create_api"),
            ("title-dialog-form", "manage_title_create_api"),
            ("editorial-dialog-form", "manage_editorial_create_api"),
        ):
            with self.subTest(dialog=dialog):
                form = re.search(rf'<form id="{dialog}" action="([^"]+)".*?</form>', html, flags=re.DOTALL)
                self.assertEqual(form.group(1), reverse(url))
                self.assertIn('name="csrfmiddlewaretoken"', form.group(0))
        self.assertIn('name="new_publishing-title"', html)
        self.assertIn('data-target-select="id_new_publishing-title"', html)
        self.assertIn('data-target-select="id_new_publishing-editorials"', html)
        for script in ("publishing_dialogs.js", "form_draft.js"):
            self.assertIn(f"js/custom/{script}", html)

    def test_the_dialog_starts_without_language_or_date(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        language = re.search(r'<select name="new_publishing-language".*?</select>', html, flags=re.DOTALL).group(0)
        self.assertInHTML('<option value="" selected disabled>Choose a language</option>', language)
        self.assertNotIn('value="en" selected', language)
        date = re.search(r'<input type="date" name="new_publishing-date"[^>]*>', html).group(0)
        self.assertNotIn("value=", date)
        self.assertIn("required", date)


class PublishingSeriesIdsTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def test_the_panel_lists_the_series_numbers_with_links(self) -> None:
        publishing = make_publishing(metron_series_id=1440, comicvine_volume_id=2865, gcd_series_id=4321)
        response = self.client.get(reverse("manage_publishing_edit", args=[publishing.pk]))
        self.assertContains(response, "Identification numbers")
        self.assertContains(response, 'href="https://comicvine.gamespot.com/volume/4050-2865/"')
        self.assertContains(response, 'href="https://www.comics.org/series/4321/"')
        self.assertContains(response, 'data-copy-value="1440"')
        self.assertContains(response, "js/custom/copy_value.js")
        self.assertNotContains(response, "metron.cloud")

    def test_no_panel_without_numbers(self) -> None:
        publishing = make_publishing()
        response = self.client.get(reverse("manage_publishing_edit", args=[publishing.pk]))
        self.assertContains(response, 'name="metron_series_id"')
        self.assertNotContains(response, "Identification numbers")

    def test_the_publishing_page_saves_them(self) -> None:
        publishing = make_publishing(publishing_title="Saga", year=2012)
        taken = make_publishing(gcd_series_id=7, comicvine_volume_id=99)
        url = reverse("manage_publishing_edit", args=[publishing.pk])
        data = {
            "publishing_title": "Saga",
            "serie": "1st",
            "language": "en",
            "date": "2012-03-14",
            "year": "2012",
            "editorials": [make_editorial().pk],
        }
        response = self.client.post(
            url, {**data, "metron_series_id": "5", "comicvine_volume_id": "99", "gcd_series_id": "7"}
        )
        self.assertContains(response, "Another publishing already has this Comic Vine volume ID.")
        self.client.post(url, {**data, "metron_series_id": "5", "comicvine_volume_id": "", "gcd_series_id": "7"})
        publishing.refresh_from_db()
        # GCD's id may repeat (only indexed); Metron's and Comic Vine's are unique.
        self.assertEqual(
            (publishing.metron_series_id, publishing.comicvine_volume_id, publishing.gcd_series_id), (5, None, 7)
        )
        self.assertEqual(taken.gcd_series_id, 7)

    def test_identification_numbers_order(self) -> None:
        publishing = Publishing(gcd_series_id=3, metron_series_id=1)
        self.assertEqual(
            [(item.source, item.value) for item in publishing.identification_numbers()], [("Metron", 1), ("GCD", 3)]
        )


class SeriesIdFieldsTests(ComiTestCase):
    def test_metron_and_comic_vine_ids_are_unique_gcd_only_indexed(self) -> None:
        for field in ("metron_series_id", "comicvine_volume_id"):
            with self.subTest(field=field):
                make_publishing(**{field: 10})
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Publishing.objects.create(publishing_title=f"Other {field}", year=2001, **{field: 10})
        make_publishing(gcd_series_id=10)
        make_publishing(gcd_series_id=10)
        self.assertTrue(Publishing._meta.get_field("gcd_series_id").db_index)
        # Unset ids never collide.
        self.assertGreaterEqual(Publishing.objects.filter(metron_series_id__isnull=True).count(), 3)

    def test_the_proposal_is_json_serializable(self) -> None:
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertIsNotNone(proposal.new_publishing)
        json.dumps(proposal.as_dict())
