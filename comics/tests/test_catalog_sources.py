"""External catalogues (comics.catalog_sources): the Metron and Comic Vine clients (HTTP mocked: no
test touches the network), the mapper and its matching rules, the Gestión lookup endpoint, saving an
imported edition (cover download allowlist, identifiers on the issue, history) and the
"Identification numbers" panel."""

import base64
import copy
import http.client
import io
import json
import sys
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from django.views.debug import ExceptionReporter
from PIL import Image

from comics.catalog_sources import comicvine, metron
from comics.catalog_sources.errors import (
    AuthFailed,
    BadResponse,
    CatalogError,
    CoverRejected,
    NotConfigured,
    NotFound,
    RateLimited,
    Unavailable,
)
from comics.catalog_sources.http import _AllowlistedRedirects, _RefuseRedirects, download_cover, is_allowed_cover_url
from comics.catalog_sources.mapper import build_proposal, variant_letter
from comics.catalog_sources.service import load_proposal
from comics.change_requests import approve, submit
from comics.history import versions_of
from comics.models import Artist, ChangeRequest, Edition, Editorial, Issue, Publishing
from comics.roles import ADMINISTRATORS
from comics.versions import revert_to_version, version_entries

from .base import ComiTestCase
from .factories import image_upload, make_edition, make_editorial, make_publishing, make_user

FIXTURES = Path(__file__).parent / "fixtures"
METRON_ISSUE = json.loads((FIXTURES / "metron_issue_15439.json").read_text(encoding="utf-8"))
COMICVINE = json.loads((FIXTURES / "comicvine_issue_21801.json").read_text(encoding="utf-8"))

PASSWORD = "s3cret-metron-pass"
API_KEY = "cv-secret-api-key"
CREDENTIALS = {"METRON_USERNAME": "tester", "METRON_PASSWORD": PASSWORD, "COMICVINE_API_KEY": API_KEY}
NO_CREDENTIALS = {"METRON_USERNAME": "", "METRON_PASSWORD": "", "COMICVINE_API_KEY": ""}
COVER_URL = "https://static.metron.cloud/media/issue/2020/07/25/marvel-two-one-annual-7.jpg"
OPEN_URL = "comics.catalog_sources.http.open_url"


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def json_response(data: Any) -> FakeResponse:
    return FakeResponse(json.dumps(data).encode())


def http_error(url: str, status: int) -> HTTPError:
    return HTTPError(url, status, "error", Message(), io.BytesIO(b""))


def png_bytes(size: tuple[int, int] = (6, 9)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (0, 128, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def metron_with_variants() -> dict[str, Any]:
    data = copy.deepcopy(METRON_ISSUE)
    data["variants"] = [
        {
            "name": "2Nd Printing 1:25 Ron Wilson Virgin Variant",
            "price": "5.99",
            "sku": "",
            "upc": "",
            "image": "https://static.metron.cloud/media/variants/b.jpg",
        },
        {
            "name": "Unknown Comics Exclusive",
            "price": None,
            "sku": "",
            "upc": "",
            "image": "https://evil.example/c.jpg",
        },
        {"name": "7Th Printing", "price": "4.99", "sku": "", "upc": "", "image": ""},
    ]
    return data


@override_settings(**CREDENTIALS)
class CatalogTestCase(ComiTestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()  # cached answers and rate-limit counters must not leak between tests
        self.addCleanup(cache.clear)


# ---------- Mapper ----------


class MapperTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.marvel = make_editorial(name="MARVEL")
        self.publishing = make_publishing(
            publishing_title="Marvel Two-In-One Annual", year=1976, serie="1st", language="en", editorials=[self.marvel]
        )
        # Same title in another language: never the match of an English series.
        make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976, serie="1st", language="es")
        self.ron = Artist.objects.create(name="ron wilson")
        self.tom = Artist.objects.create(name="Tom DeFalco")

    def test_metron_issue_becomes_a_neutral_source_issue(self) -> None:
        source = metron.to_source_issue(METRON_ISSUE)
        self.assertEqual((source.publisher, source.series_name), ("Marvel", "Marvel Two-in-One Annual"))
        self.assertEqual((source.series_volume, source.series_year, source.language), (1, 1976, "en"))
        self.assertEqual((source.number, str(source.price), source.price_currency), ("7", "1.00", "USD"))
        self.assertEqual((source.metron_id, source.comicvine_id, source.gcd_id), (15439, 21801, 35962))
        self.assertIsNone(source.store_date)
        self.assertEqual(source.image, COVER_URL)

    def test_proposal_from_metron_matches_case_insensitively(self) -> None:
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertEqual(proposal.editorial, {"id": self.marvel.pk, "name": "MARVEL"})
        self.assertEqual(proposal.publishing["id"], self.publishing.pk)
        self.assertEqual(proposal.publishing_candidates, [])
        self.assertIsNone(proposal.issue)  # not in Comi yet
        self.assertEqual(
            proposal.edition,
            {
                "variant": "A",
                "printing": "1st",
                "number": "7",
                "release_date": "1982-10-01",  # only the cover date is known
                "cover_price": "1.00",
                "format": "single_issue",  # series type "Annual"
            },
        )
        self.assertEqual(proposal.synopsis, METRON_ISSUE["desc"])
        self.assertEqual(proposal.identifiers, {"metron_id": 15439, "comicvine_id": 21801, "gcd_id": 35962})
        self.assertEqual(proposal.cover_url, COVER_URL)
        self.assertEqual(
            [(a.id, a.name) for a in proposal.creators], [(self.ron.pk, "ron wilson"), (self.tom.pk, "Tom DeFalco")]
        )
        # The editor is not a creator; cover-only roles go to the cover artists.
        self.assertNotIn("Jim Salicrup", proposal.unmatched["creators"])
        self.assertIn("Armando Gil", proposal.unmatched["creators"])
        self.assertEqual([a.id for a in proposal.cover_artists], [self.ron.pk])
        self.assertEqual(proposal.unmatched["cover_artists"], ["Frank Giacoia"])
        self.assertEqual((proposal.unmatched["editorial"], proposal.unmatched["publishing"]), ("", ""))
        self.assertIn(
            "Only the cover date is known: the release date is usually a few months earlier.", proposal.warnings
        )
        self.assertEqual(proposal.variants, [])

    def test_unmatched_editorial_and_publishing_are_listed(self) -> None:
        Publishing.objects.all().delete()
        Editorial.objects.all().delete()
        older = make_publishing(publishing_title="Marvel Two-in-One Annual", year=1980, language="en")
        newer = make_publishing(publishing_title="marvel two-in-one annual", year=1990, language="en")
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertIsNone(proposal.publishing)
        self.assertIsNone(proposal.editorial)
        self.assertEqual(proposal.unmatched["editorial"], "Marvel")
        self.assertEqual(proposal.unmatched["publishing"], "Marvel Two-in-One Annual (1976) 1st · EN")
        # Another year is another publishing: offered as candidates, never selected.
        self.assertEqual([item["id"] for item in proposal.publishing_candidates], [older.pk, newer.pk])

    def test_the_issue_is_matched_by_its_ids_first(self) -> None:
        elsewhere = make_edition(publishing=make_publishing(publishing_title="Other"), number="1").issue
        Issue.objects.filter(pk=elsewhere.pk).update(metron_id=15439)
        holder = make_edition(publishing=make_publishing(publishing_title="Third"), number="2").issue
        Issue.objects.filter(pk=holder.pk).update(comicvine_id=21801)
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertEqual(proposal.issue["id"], elsewhere.pk)
        self.assertIn(
            f"{holder} already has one of these ids: saving them on another issue will fail.", proposal.warnings
        )

    def test_the_issue_is_matched_by_publishing_and_number(self) -> None:
        issue = make_edition(publishing=self.publishing, number="7").issue
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertEqual(
            proposal.issue, {"id": issue.pk, "label": str(issue), "publishing_id": self.publishing.pk, "number": "7"}
        )

    def test_variants_are_offered_with_what_their_names_say(self) -> None:
        proposal = build_proposal(metron.to_source_issue(metron_with_variants()))
        second, exclusive, seventh = proposal.variants
        self.assertEqual([variant.letter for variant in proposal.variants], ["B", "C", "D"])
        self.assertEqual((second.ratio, second.printing, second.cover_price), ("1:25", "2nd", "5.99"))
        self.assertEqual(second.cover_url, "https://static.metron.cloud/media/variants/b.jpg")
        self.assertEqual([artist.id for artist in second.cover_artists], [self.ron.pk])
        self.assertEqual((exclusive.ratio, exclusive.printing, exclusive.cover_price), ("", "1st", None))
        self.assertEqual(exclusive.cover_url, "")  # not an allowlisted host
        self.assertEqual(seventh.printing, "")  # Comi has no 7th printing: left to the user
        # With variants, the main cover gets the cover artists who also drew the interior.
        self.assertEqual([artist.id for artist in proposal.cover_artists], [self.ron.pk])
        self.assertEqual(proposal.unmatched["cover_artists"], ["Frank Giacoia"])
        self.assertTrue(any("lists the artists of all the covers together" in warning for warning in proposal.warnings))

    def test_variant_letters(self) -> None:
        self.assertEqual([variant_letter(index) for index in (0, 1, 24, 25, 26)], ["B", "C", "Z", "AA", "AB"])

    def test_comicvine_issue_becomes_a_proposal(self) -> None:
        source = comicvine.to_source_issue(
            {"issue": COMICVINE["issue"]["results"], "volume": COMICVINE["volume"]["results"]}
        )
        self.assertEqual(
            (source.publisher, source.series_year, source.series_volume, source.language), ("Marvel", 1976, None, "")
        )
        self.assertEqual(
            source.description,
            "The Champion challenges Earth's mightiest heroes & more.\nSecond paragraph.\nSame paragraph, new line.",
        )
        proposal = build_proposal(source)
        self.assertEqual(proposal.identifiers, {"metron_id": None, "comicvine_id": 21801, "gcd_id": None})
        self.assertEqual(proposal.cover_url, "https://comicvine.gamespot.com/a/uploads/scale_large/0/4/21801.jpg")
        # Comic Vine has no language: the editorial tells the English publishing from the Spanish one.
        self.assertEqual(proposal.publishing["id"], self.publishing.pk)
        self.assertEqual([artist.id for artist in proposal.cover_artists], [self.ron.pk])
        self.assertEqual(proposal.unmatched["creators"], ["Frank Giacoia", "George Roussos"])
        self.assertNotIn("format", proposal.edition)


# ---------- Clients ----------


class MetronClientTests(CatalogTestCase):
    def test_fetches_with_basic_auth_and_user_agent_then_caches(self) -> None:
        with mock.patch(OPEN_URL, return_value=json_response(METRON_ISSUE)) as open_url:
            data = metron.fetch_issue(15439)
            metron.fetch_issue(15439)
        self.assertEqual(data["id"], 15439)
        self.assertEqual(open_url.call_count, 1)  # the second call is served from the cache
        request: Request = open_url.call_args.args[1]
        self.assertEqual(request.full_url, "https://metron.cloud/api/issue/15439/")
        self.assertTrue(request.get_header("Authorization").startswith("Basic "))
        self.assertTrue(request.get_header("User-agent").startswith("Comi/"))

    def test_errors_become_messages_without_credentials(self) -> None:
        url = "https://metron.cloud/api/issue/1/"
        cases = [
            (http_error(url, 401), AuthFailed, "Metron rejected the credentials. Check them in the .env file."),
            (http_error(url, 404), NotFound, "Metron has no issue with the id 1."),
            (
                http_error(url, 429),
                RateLimited,
                "Metron is receiving too many requests from Comi. Wait a minute and try again.",
            ),
            (http_error(url, 503), Unavailable, "Metron is not working right now (error 503). Try again later."),
            (http_error(url, 301), BadResponse, "Metron sent an answer Comi cannot read."),
            (URLError(TimeoutError("timed out")), Unavailable, "Metron did not answer in time. Try again in a moment."),
            (TimeoutError("timed out"), Unavailable, "Metron did not answer in time. Try again in a moment."),
            (URLError(OSError("no route")), Unavailable, "Metron could not be reached. Try again later."),
        ]
        for error, error_class, message in cases:
            with self.subTest(error=error_class.__name__, message=message):
                cache.clear()
                with mock.patch(OPEN_URL, side_effect=error), self.assertRaises(error_class) as raised:
                    metron.fetch_issue(1)
                self.assertEqual(raised.exception.message, message)
                self.assertNotIn(PASSWORD, raised.exception.message)
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)

    def test_unreadable_answers(self) -> None:
        for body in (b"<html>maintenance</html>", json.dumps([1, 2]).encode()):
            with (
                self.subTest(body=body[:10]),
                mock.patch(OPEN_URL, return_value=FakeResponse(body)),
                self.assertRaises(BadResponse),
            ):
                metron.fetch_issue(2)

    def test_own_rate_limit_stops_before_requesting(self) -> None:
        with (
            mock.patch.object(metron, "REQUESTS_PER_MINUTE", 1),
            mock.patch("comics.catalog_sources.http.time", mock.Mock(time=mock.Mock(return_value=6_000_000.0))),
            mock.patch(OPEN_URL, return_value=json_response(METRON_ISSUE)) as open_url,
        ):
            metron.fetch_issue(15439)
            with self.assertRaises(RateLimited):
                metron.fetch_issue(15440)
        self.assertEqual(open_url.call_count, 1)

    @override_settings(**NO_CREDENTIALS)
    def test_not_configured(self) -> None:
        with mock.patch(OPEN_URL) as open_url, self.assertRaises(NotConfigured):
            load_proposal("metron", 15439)
        open_url.assert_not_called()


class ComicVineClientTests(CatalogTestCase):
    def test_fetches_the_issue_and_its_volume(self) -> None:
        def answer(opener: Any, request: Request) -> FakeResponse:
            return json_response(COMICVINE["issue"] if "/issue/" in request.full_url else COMICVINE["volume"])

        with mock.patch(OPEN_URL, side_effect=answer) as open_url:
            data = comicvine.fetch_issue(21801)
        self.assertEqual((data["issue"]["id"], data["volume"]["start_year"]), (21801, "1976"))
        urls = [call.args[1].full_url for call in open_url.call_args_list]
        self.assertTrue(urls[0].startswith("https://comicvine.gamespot.com/api/issue/4000-21801/?"))
        self.assertTrue(urls[1].startswith("https://comicvine.gamespot.com/api/volume/4050-2865/?"))
        self.assertIn("format=json", urls[0])

    def test_envelope_and_http_errors(self) -> None:
        url = "https://comicvine.gamespot.com/api/issue/4000-5/"
        cases = [
            (json_response({"status_code": 100, "error": "Invalid API Key", "results": []}), AuthFailed),
            (json_response({"status_code": 101, "error": "Object Not Found", "results": []}), NotFound),
            (json_response({"status_code": 107, "error": "Rate limit", "results": []}), RateLimited),
            (json_response({"status_code": 1, "error": "OK", "results": []}), NotFound),
            (http_error(url, 420), RateLimited),
            (http_error(url, 401), AuthFailed),
        ]
        for answer, error_class in cases:
            with self.subTest(error=error_class.__name__):
                cache.clear()
                kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with mock.patch(OPEN_URL, **kwargs), self.assertRaises(error_class) as raised:
                    comicvine.fetch_issue(5)
                self.assertNotIn(API_KEY, raised.exception.message)


class CoverUrlTests(ComiTestCase):
    def test_allowlist(self) -> None:
        allowed = [COVER_URL, "https://comicvine.gamespot.com/a/uploads/scale_large/0/4/1.jpg"]
        rejected = [
            "http://static.metron.cloud/media/a.jpg",  # not HTTPS
            "https://static.metron.cloud:8443/media/a.jpg",
            "https://user:pass@static.metron.cloud/media/a.jpg",
            "https://static.metron.cloud.evil.example/a.jpg",
            "https://evil.example/static.metron.cloud/a.jpg",
            "https://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "",
        ]
        for url in allowed:
            with self.subTest(url=url):
                self.assertTrue(is_allowed_cover_url(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(is_allowed_cover_url(url))

    def test_redirects(self) -> None:
        request = Request(COVER_URL)
        args = (request, io.BytesIO(b""), 302, "Found", Message())
        self.assertIsNone(_AllowlistedRedirects().redirect_request(*args, "https://evil.example/x.jpg"))
        followed = _AllowlistedRedirects().redirect_request(*args, "https://static.metron.cloud/other.jpg")
        self.assertEqual(followed.full_url, "https://static.metron.cloud/other.jpg")
        # API requests carry credentials: they never follow a redirect.
        self.assertIsNone(_RefuseRedirects().redirect_request(*args, "https://metron.cloud/api/issue/1/"))


# ---------- Gestión endpoint ----------


class CatalogLookupViewTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_catalog_lookup")
        self.manager = make_user(role=ADMINISTRATORS)

    def counts(self) -> list[int]:
        return [model.objects.count() for model in (Editorial, Publishing, Artist, Issue, Edition)]

    def test_collectors_get_403_and_anonymous_users_the_login(self) -> None:
        self.assertEqual(self.client.get(self.url, {"source": "metron", "id": "1"}).status_code, 302)
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self.url, {"source": "metron", "id": "1"}).status_code, 403)

    def test_returns_the_proposal_and_saves_nothing(self) -> None:
        self.client.force_login(self.manager)
        before = self.counts()
        with mock.patch(OPEN_URL, return_value=json_response(METRON_ISSUE)):
            response = self.client.get(self.url, {"source": "metron", "id": "15439"})
        self.assertEqual(response.status_code, 200)
        proposal = response.json()["proposal"]
        self.assertEqual((proposal["source"], proposal["edition"]["number"]), ("metron", "7"))
        self.assertEqual(proposal["unmatched"]["editorial"], "Marvel")
        self.assertNotIn(PASSWORD, response.content.decode())
        self.assertEqual(self.counts(), before)
        self.assertEqual(Issue.history.count(), 0)

    def test_comicvine_ids_may_keep_their_prefix(self) -> None:
        self.client.force_login(self.manager)

        def answer(opener: Any, request: Request) -> FakeResponse:
            return json_response(COMICVINE["issue"] if "/issue/" in request.full_url else COMICVINE["volume"])

        with mock.patch(OPEN_URL, side_effect=answer):
            response = self.client.get(self.url, {"source": "comicvine", "id": "4000-21801"})
        self.assertEqual(response.json()["proposal"]["identifiers"]["comicvine_id"], 21801)
        self.assertNotIn(API_KEY, response.content.decode())

    def test_invalid_input_and_source_errors_are_json(self) -> None:
        self.client.force_login(self.manager)
        for params in (
            {"source": "metron", "id": "abc"},
            {"source": "gcd", "id": "1"},
            {"source": "metron", "id": "0"},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        with mock.patch(OPEN_URL, side_effect=http_error("https://metron.cloud/api/issue/9/", 404)):
            response = self.client.get(self.url, {"source": "metron", "id": "9"})
        self.assertEqual(
            (response.status_code, response.json()), (404, {"error": "Metron has no issue with the id 9."})
        )

    @override_settings(**NO_CREDENTIALS)
    def test_disabled_without_credentials(self) -> None:
        self.client.force_login(self.manager)
        with mock.patch(OPEN_URL) as open_url:
            response = self.client.get(self.url, {"source": "metron", "id": "15439"})
        self.assertEqual((response.status_code, response.json()), (503, {"error": "Metron is not configured."}))
        open_url.assert_not_called()
        # MyComicShop needs no key, so the import box stays on with only that source enabled.
        page = self.client.get(reverse("manage_edition_new"))
        self.assertNotContains(page, "Importing is off")
        self.assertContains(page, '<option value="metron" disabled>Metron · not configured</option>', html=True)
        self.assertContains(page, '<option value="mycomicshop" data-requires-series>MyComicShop</option>', html=True)

    @override_settings(METRON_USERNAME="", METRON_PASSWORD="", COMICVINE_API_KEY=API_KEY)
    def test_only_configured_sources_can_be_chosen(self) -> None:
        self.client.force_login(self.manager)
        page = self.client.get(reverse("manage_edition_new"))
        self.assertContains(page, 'id="catalog-load"')
        self.assertContains(page, '<option value="metron" disabled>Metron · not configured</option>', html=True)
        self.assertContains(page, '<option value="comicvine" >Comic Vine</option>', html=True)
        self.assertNotContains(page, API_KEY)


# ---------- Saving an imported edition ----------


class ImportedEditionSaveTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)
        self.publishing = make_publishing(publishing_title="Marvel Two-in-One Annual", year=1976)

    def new_edition_data(self, **changes: Any) -> dict[str, Any]:
        data = {
            "publishing": self.publishing.pk,
            "number": "7",
            "variant": "A",
            "printing": "1st",
            "format": "single_issue",
            "rarity": "common",
            "release_date": "1982-10-01",
            "cover_price": "1.00",
            "content": "single",
        }
        data.update(changes)
        return data

    def test_the_imported_cover_is_downloaded_on_save(self) -> None:
        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())) as open_url:
            response = self.client.post(
                reverse("manage_edition_new"), self.new_edition_data(catalog_cover_url=COVER_URL)
            )
        self.assertEqual(response.status_code, 302)
        edition = Edition.objects.get(publishing=self.publishing, number="7")
        self.assertTrue(edition.image.name.endswith(".jpg"))  # processed like an uploaded cover
        self.assertTrue(edition.thumbnail)
        self.assertEqual(open_url.call_args.args[1].full_url, COVER_URL)

    def test_covers_from_other_hosts_are_never_fetched(self) -> None:
        for url in ("https://evil.example/cover.jpg", "http://static.metron.cloud/a.jpg", "http://127.0.0.1:8000/x"):
            with self.subTest(url=url), mock.patch(OPEN_URL) as open_url:
                response = self.client.post(reverse("manage_edition_new"), self.new_edition_data(catalog_cover_url=url))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Covers can only be downloaded from the catalogues Comi imports from.")
                open_url.assert_not_called()
        self.assertFalse(Edition.objects.exists())

    def test_a_bad_download_is_a_form_error(self) -> None:
        cases = [
            (FakeResponse(b"x", {"Content-Length": str(50 * 1024 * 1024)}), "The cover is too large (maximum 10 MB)."),
            (FakeResponse(b"<html>not a picture</html>"), "Upload a valid image."),
            (http_error(COVER_URL, 404), "The cover could not be downloaded. Try again, or upload it yourself."),
        ]
        for answer, message in cases:
            kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
            with self.subTest(message=message), mock.patch(OPEN_URL, **kwargs):
                response = self.client.post(
                    reverse("manage_edition_new"), self.new_edition_data(catalog_cover_url=COVER_URL)
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message[:30])
        self.assertFalse(Edition.objects.exists())

    def test_an_uploaded_file_wins_over_the_imported_cover(self) -> None:
        data = self.new_edition_data(catalog_cover_url=COVER_URL, image=image_upload())
        with mock.patch(OPEN_URL) as open_url:
            response = self.client.post(reverse("manage_edition_new"), data)
        self.assertEqual(response.status_code, 302)
        open_url.assert_not_called()
        self.assertTrue(Edition.objects.get().image)

    def test_identifiers_synopsis_and_creators_are_saved_on_the_issue(self) -> None:
        artist = Artist.objects.create(name="Tom DeFalco")
        data = self.new_edition_data(
            **{
                "issue_data-metron_id": "15439",
                "issue_data-comicvine_id": "21801",
                "issue_data-gcd_id": "35962",
                "issue_data-synopsis": "The Champion challenges Earth's heroes.",
                "issue_data-creators": [artist.pk],
            }
        )
        response = self.client.post(reverse("manage_edition_new"), data)
        self.assertEqual(response.status_code, 302)
        issue = Edition.objects.get().issue
        self.assertEqual((issue.metron_id, issue.comicvine_id, issue.gcd_id), (15439, 21801, 35962))
        self.assertEqual(issue.synopsis, "The Champion challenges Earth's heroes.")
        self.assertEqual(list(issue.creators.all()), [artist])
        # One version of the issue, by the manager, with the identifiers (labels read well).
        latest = version_entries(Issue, issue.pk)[0]
        self.assertEqual(latest.version.history_user, self.manager)
        self.assertEqual(latest.version.metron_id, 15439)
        changes = {change.label: change.new for change in latest.changes}
        self.assertEqual(
            {label: changes.get(label) for label in ("Metron ID", "Comic Vine ID", "GCD ID")},
            {"Metron ID": "15439", "Comic Vine ID": "21801", "GCD ID": "35962"},
        )

    def test_history_records_a_changed_identifier(self) -> None:
        edition = make_edition(publishing=self.publishing, number="7")
        Issue.objects.filter(pk=edition.issue_id).update(metron_id=1)
        data = self.new_edition_data(**{"issue_data-shown": edition.issue_id, "issue_data-metron_id": "15439"})
        self.client.post(reverse("manage_edition_edit", args=[edition.pk]), data)
        latest = version_entries(Issue, edition.issue_id)[0]
        self.assertIn(("Metron ID", "15439"), [(change.label, change.new) for change in latest.changes])

    def test_an_id_of_another_issue_is_refused(self) -> None:
        other = make_edition(publishing=make_publishing(publishing_title="Other"), number="1").issue
        Issue.objects.filter(pk=other.pk).update(metron_id=15439)
        response = self.client.post(
            reverse("manage_edition_new"), self.new_edition_data(**{"issue_data-metron_id": "15439"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Another issue already has this Metron ID.")
        self.assertFalse(Edition.objects.filter(publishing=self.publishing).exists())

    def test_a_new_cover_of_an_issue_that_has_the_ids(self) -> None:
        # The import matched an issue already in Comi (by its Metron ID) and linked the new edition
        # to it: the same ids are not a conflict with itself.
        issue = make_edition(publishing=make_publishing(publishing_title="Original"), number="7").issue
        Issue.objects.filter(pk=issue.pk).update(metron_id=15439, comicvine_id=21801)
        data = self.new_edition_data(
            issue=issue.pk, **{"issue_data-metron_id": "15439", "issue_data-comicvine_id": "21801"}
        )
        response = self.client.post(reverse("manage_edition_new"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.get(publishing=self.publishing).issue_id, issue.pk)

    def test_only_changed_values_reach_the_chosen_issue(self) -> None:
        artist = Artist.objects.create(name="Jim Lee")
        edition = make_edition(publishing=self.publishing, number="7")
        shown = edition.issue
        shown.synopsis = "Shown synopsis"
        shown.metron_id = 15439
        shown.save()
        shown.creators.set([artist])
        target = make_edition(publishing=make_publishing(publishing_title="Foreign"), number="7").issue
        # The page showed the edition's own issue; the user links it to another one without
        # touching the issue fields: nothing of the shown issue is copied onto the chosen one.
        data = self.new_edition_data(
            issue=target.pk,
            **{
                "issue_data-shown": shown.pk,
                "issue_data-synopsis": "Shown synopsis",
                "issue_data-metron_id": "15439",
                "issue_data-creators": [artist.pk],
                "issue_data-gcd_id": "777",  # the one value changed
            },
        )
        response = self.client.post(reverse("manage_edition_edit", args=[edition.pk]), data)
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertEqual((target.synopsis, target.metron_id, target.gcd_id), ("", None, 777))
        self.assertFalse(target.creators.exists())

    def test_a_form_without_the_issue_section_changes_nothing_on_the_issue(self) -> None:
        artist = Artist.objects.create(name="Jim Lee")
        edition = make_edition(publishing=self.publishing, number="7")
        Issue.objects.filter(pk=edition.issue_id).update(synopsis="Kept", metron_id=15439)
        edition.issue.creators.set([artist])
        response = self.client.post(reverse("manage_edition_edit", args=[edition.pk]), self.new_edition_data(notes="x"))
        self.assertEqual(response.status_code, 302)
        issue = Issue.objects.get(pk=edition.issue_id)
        self.assertEqual((issue.synopsis, issue.metron_id), ("Kept", 15439))
        self.assertEqual(list(issue.creators.all()), [artist])


# ---------- Change requests ----------


class IdentifierProposalTests(CatalogTestCase):
    def test_an_issue_correction_can_carry_identifiers(self) -> None:
        author = make_user()
        edition = make_edition()
        self.client.force_login(author)
        url = reverse("comics_issue_suggest", args=[edition.issue_id])
        self.client.post(url, {"metron_id": "15439", "reason": "Found it on Metron"})
        change_request = ChangeRequest.objects.get()
        self.assertEqual(change_request.changes, {"metron_id": 15439})
        approve(change_request, user=make_user(role=ADMINISTRATORS))
        self.assertEqual(Issue.objects.get(pk=edition.issue_id).metron_id, 15439)

    def test_approving_a_duplicate_identifier_is_refused(self) -> None:
        taken = make_edition().issue
        Issue.objects.filter(pk=taken.pk).update(comicvine_id=21801)
        target = make_edition().issue
        change_request = submit(
            author=make_user(),
            model=Issue,
            changes={"comicvine_id": 21801},
            label=str(target),
            reason="CV",
            target=target,
        )
        with self.assertRaisesMessage(ValidationError, "Comic Vine ID"):
            approve(change_request, user=make_user(role=ADMINISTRATORS))
        self.assertIsNone(Issue.objects.get(pk=target.pk).comicvine_id)
        self.assertEqual(ChangeRequest.objects.get().status, ChangeRequest.Status.PENDING)


# ---------- Identification numbers panel ----------


class IdentificationPanelTests(ComiTestCase):
    def setUp(self) -> None:
        self.edition = make_edition()
        self.client.force_login(make_user())

    def test_lists_only_the_numbers_that_are_set_with_links(self) -> None:
        Issue.objects.filter(pk=self.edition.issue_id).update(metron_id=15439, comicvine_id=21801, gcd_id=35962)
        response = self.client.get(reverse("comics_edition_detail", args=[self.edition.pk]))
        self.assertContains(response, "Identification numbers")
        self.assertContains(
            response,
            'href="https://comicvine.gamespot.com/issue/4000-21801/" target="_blank" rel="noopener noreferrer"',
        )
        self.assertContains(response, "(opens in a new tab)", count=2)
        self.assertContains(response, 'href="https://www.comics.org/issue/35962/"')
        self.assertContains(response, 'data-copy-value="15439"')
        self.assertContains(response, 'data-copy-value="21801"')
        self.assertContains(response, "js/custom/copy_value.js")
        self.assertNotContains(response, "metron.cloud/issue")  # Metron pages have no id-based URL

    def test_only_the_set_numbers_are_listed(self) -> None:
        Issue.objects.filter(pk=self.edition.issue_id).update(metron_id=15439)
        response = self.client.get(reverse("comics_edition_detail", args=[self.edition.pk]))
        self.assertContains(response, 'data-copy-value="15439"')
        self.assertNotContains(response, "comicvine.gamespot.com/issue")
        self.assertNotContains(response, "comics.org")

    def test_no_panel_without_numbers(self) -> None:
        response = self.client.get(reverse("comics_edition_detail", args=[self.edition.pk]))
        self.assertNotContains(response, "Identification numbers")

    def test_gestion_edition_page_shows_it_too(self) -> None:
        Issue.objects.filter(pk=self.edition.issue_id).update(gcd_id=35962)
        self.client.force_login(make_user(role=ADMINISTRATORS))
        response = self.client.get(reverse("manage_edition_edit", args=[self.edition.pk]))
        self.assertContains(response, "Identification numbers")
        self.assertContains(response, 'href="https://www.comics.org/issue/35962/"')
        self.assertContains(response, f'name="issue_data-shown" value="{self.edition.issue_id}"')


# ---------- Robustness: request errors, secrets in tracebacks, reverts, creators ----------


class RequestErrorTests(CatalogTestCase):
    """Whatever fails while sending or reading becomes a user-facing error, never a 500."""

    def test_api_request_errors(self) -> None:
        unreachable = "Metron could not be reached. Try again later."
        unreadable = "Metron sent an answer Comi cannot read."
        cases = [
            (OSError("network is down"), unreachable),
            (ConnectionResetError("reset"), unreachable),
            (http.client.IncompleteRead(b"partial"), unreachable),
            (http.client.RemoteDisconnected("closed"), unreachable),
            (http.client.InvalidURL("bad url"), unreachable),  # an HTTPException (and a ValueError)
            (ValueError("unknown url type"), unreadable),
            (RecursionError("too deep"), unreadable),
        ]
        for error, message in cases:
            with self.subTest(error=type(error).__name__):
                cache.clear()
                with mock.patch(OPEN_URL, side_effect=error), self.assertRaises(CatalogError) as raised:
                    metron.fetch_issue(3)
                self.assertEqual(raised.exception.message, message)
                self.assertTrue(raised.exception.__suppress_context__)

    def test_errors_while_reading_and_decoding(self) -> None:
        class BrokenResponse(FakeResponse):
            def read(self, size: int = -1) -> bytes:
                raise http.client.IncompleteRead(b"half")

        with mock.patch(OPEN_URL, return_value=BrokenResponse(b"")), self.assertRaises(Unavailable):
            metron.fetch_issue(4)
        deep = b"[" * 100_000 + b"]" * 100_000  # json.loads raises RecursionError
        with mock.patch(OPEN_URL, return_value=FakeResponse(deep)), self.assertRaises(BadResponse):
            metron.fetch_issue(5)

    def test_cover_download_errors(self) -> None:
        failed = "The cover could not be downloaded. Try again, or upload it yourself."
        errors = (OSError("down"), http.client.IncompleteRead(b""), http.client.InvalidURL("x"), RecursionError())
        for error in errors:
            with self.subTest(error=type(error).__name__), mock.patch(OPEN_URL, side_effect=error):
                with self.assertRaises(CoverRejected) as raised:
                    download_cover(COVER_URL)
                self.assertEqual(raised.exception.message, failed)

    def test_urls_with_whitespace_or_control_characters_are_refused_up_front(self) -> None:
        urls = (
            f"{COVER_URL}\n",
            "https://static.metron.cloud/a\tb.jpg",
            "https://static.metron.cloud/a\rb.jpg",
            "https://static.metron.cloud/a b.jpg",
            "https://static.metron.cloud/a\x00.jpg",
        )
        for url in urls:
            with self.subTest(url=repr(url)), mock.patch(OPEN_URL) as open_url:
                self.assertFalse(is_allowed_cover_url(url))
                with self.assertRaises(CoverRejected):
                    download_cover(url)
                open_url.assert_not_called()

    def test_tracebacks_hide_the_credentials(self) -> None:
        token = base64.b64encode(f"tester:{PASSWORD}".encode()).decode()

        def traceback_text(call: Any) -> str:
            try:
                call()
            except CatalogError:
                return ExceptionReporter(None, *sys.exc_info()).get_traceback_text()
            self.fail("no error raised")

        with mock.patch(OPEN_URL, side_effect=http_error("https://metron.cloud/api/issue/6/", 401)):
            text = traceback_text(lambda: metron.fetch_issue(6))
        self.assertIn("get_json", text)  # the frames are there, their secrets are not
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(token, text)
        cv_url = "https://comicvine.gamespot.com/api/issue/4000-6/"
        with mock.patch(OPEN_URL, side_effect=http_error(cv_url, 401)):
            text = traceback_text(lambda: comicvine.fetch_issue(6))
        self.assertIn("get_json", text)
        self.assertNotIn(API_KEY, text)


class RevertUniqueIdTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)

    def test_reverting_to_an_id_taken_since_is_refused_not_a_crash(self) -> None:
        issue = make_edition().issue
        issue.metron_id = 5
        issue.save()
        old = versions_of(Issue, issue.pk).filter(metron_id=5).first()
        issue.metron_id = None
        issue.save()
        other = make_edition().issue
        other.metron_id = 5
        other.save()
        with self.assertRaisesMessage(ValidationError, "This version cannot be restored"):
            revert_to_version(old, user=self.manager)
        self.client.force_login(self.manager)
        response = self.client.post(reverse("manage_issue_revert", args=[issue.pk, old.history_id]), {"reason": ""})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Issue.objects.get(pk=issue.pk).metron_id)
        shown = " ".join(str(message) for message in get_messages(response.wsgi_request))
        self.assertIn("This version cannot be restored", shown)

    def test_restoring_a_deleted_issue_whose_id_was_taken_is_refused(self) -> None:
        edition = make_edition(number="1")
        issue = edition.issue
        issue.metron_id = 7
        issue.save()
        first = versions_of(Edition, edition.pk).first()
        edition.number = "99"
        edition.save()  # follows its own series: the old issue is left orphaned and deleted
        self.assertFalse(Issue.objects.filter(pk=issue.pk).exists())
        taker = make_edition().issue
        taker.metron_id = 7
        taker.save()
        with self.assertRaisesMessage(ValidationError, "This version cannot be restored"):
            revert_to_version(first, user=self.manager)
        self.assertEqual(Edition.objects.get(pk=edition.pk).number, "99")


class CreatorsDeltaTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.a, self.b, self.c = (Artist.objects.create(name=name) for name in ("Artist A", "Artist B", "Artist C"))

    def post(self, edition: Edition | None, **fields: Any) -> Any:
        publishing = edition.publishing if edition else make_publishing()
        data = {
            "publishing": publishing.pk,
            "number": edition.number if edition else "1",
            "variant": "A",
            "printing": "1st",
            "format": "single_issue",
            "rarity": "common",
            "release_date": "2000-01-01",
            "cover_price": "0",
            "content": "single",
            **fields,
        }
        url = reverse("manage_edition_edit", args=[edition.pk]) if edition else reverse("manage_edition_new")
        return self.client.post(url, data)

    def test_linking_to_another_issue_and_adding_a_creator(self) -> None:
        edition = make_edition()
        edition.issue.creators.set([self.a])
        target = make_edition().issue
        target.creators.set([self.b])
        response = self.post(
            edition,
            issue=target.pk,
            **{"issue_data-shown": edition.issue_id, "issue_data-creators": [self.a.pk, self.c.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(target.creators.all()), {self.b, self.c})  # not A, the shown issue's

    def test_a_new_edition_on_an_existing_issue_keeps_its_creators(self) -> None:
        target = make_edition().issue
        target.creators.set([self.b])
        self.assertEqual(self.post(None, issue=target.pk).status_code, 302)
        self.assertEqual(set(target.creators.all()), {self.b})
        response = self.post(None, issue=target.pk, number="2", **{"issue_data-creators": [self.c.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(target.creators.all()), {self.b, self.c})

    def test_a_compilation_ignores_the_issue_section(self) -> None:
        collected = make_edition().issue
        response = self.post(
            None,
            content="compilation",
            collected=json.dumps([collected.pk]),
            format="trade_paperback",
            **{"issue_data-metron_id": "not a number"},
        )
        self.assertEqual(response.status_code, 302)
