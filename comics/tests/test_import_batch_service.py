"""The import batch service (comics.catalog_sources.batches): building a batch from the variants picked
on a MyComicShop series page, a row's effective values, and the credit names resolved once per batch.
HTTP is mocked with the MyComicShop fixture pages."""

from dataclasses import replace
from typing import Any
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from comics.catalog_sources import mycomicshop
from comics.catalog_sources.batches import (
    MAX_BATCH_ROWS,
    PendingName,
    clear_name_resolution,
    create_batch,
    effective_cover_artists,
    effective_cover_url,
    effective_values,
    pending_names,
    resolve_name,
    resolved_credits,
)
from comics.catalog_sources.errors import Conflict, InvalidSelection, NotFound
from comics.models import Artist, ImportBatch, ImportBatchRow, Issue

from .factories import make_edition, make_editorial, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase
from .test_import_batches import make_batch, make_number, make_row
from .test_mycomicshop_import import BATMAN_TID, TMNT_TID, VAMPI_TID, page_answer, series_of
from .test_mycomicshop_link import IVID_1A, IVID_1L, IVID_1P, IVID_1U

FETCH_SERIES = "comics.catalog_sources.mycomicshop.fetch_series"
# Teenage Mutant Ninja Turtles vs. Street Fighter (2023, IDW): 1A, 1CARNIVORE.B, 3A, 3RID, 4NYCC.
TMNT_IVIDS = [58639767, 61572909, 59040676, 59040682, 65354182]
VAMPI_1I = 65338030  # a variant without a cover picture


def credit(name: str, artist_id: int | None = None, uncertain: bool = False) -> dict[str, Any]:
    return {"name": name, "artist_id": artist_id, "uncertain": uncertain}


class CreateBatchTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user()
        self.publishing = make_publishing(
            publishing_title="Batman Spawn", year=2023, language="en", editorials=[make_editorial(name="DC")]
        )
        self.todd = Artist.objects.create(name="Todd McFarlane")
        self.greg = Artist.objects.create(name="Greg Capullo")

    def create(self, tid: int, ivids: list[int]) -> ImportBatch:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            return create_batch(tid, ivids, self.user)

    def test_batch_numbers_and_rows_in_page_order(self) -> None:
        batch = self.create(BATMAN_TID, [IVID_1U, IVID_1A, IVID_1L, IVID_1A])
        self.assertEqual(batch.source, mycomicshop.KEY)
        self.assertEqual(
            (batch.series_id, batch.series_title, batch.series_year, batch.series_publisher),
            (BATMAN_TID, "Batman Spawn", 2023, "DC"),
        )
        self.assertEqual((batch.publishing, batch.created_by), (self.publishing, self.user))
        self.assertEqual((batch.defaults, batch.name_resolutions, batch.status), ({}, {}, ImportBatch.Status.OPEN))
        [number] = batch.numbers.all()
        self.assertEqual((number.number, number.position, number.issue), ("1", 0, None))
        # A new issue proposes the story credits of its variants, once each.
        self.assertEqual(
            number.creators,
            [
                credit("Todd McFarlane", self.todd.pk),
                credit("Greg Capullo", self.greg.pk),
                credit("Jonathan Glapion"),
            ],
        )
        rows = list(batch.rows.all())
        self.assertEqual([(row.label, row.position) for row in rows], [("1A", 0), ("1L", 1), ("1U", 2)])
        self.assertTrue(all(row.group_id == number.pk for row in rows))
        self.assertTrue(all((row.match, row.selected) == (ImportBatchRow.Match.NONE, True) for row in rows))

    def test_row_proposal_holds_the_edition_values_and_the_cover(self) -> None:
        batch = self.create(BATMAN_TID, [IVID_1L, IVID_1U])
        virgin, second = batch.rows.all()
        variant = next(item for item in series_of(BATMAN_TID).variants if item.ivid == IVID_1U)
        self.assertEqual(second.ivid, IVID_1U)
        self.assertEqual(second.source_text, variant.details.text)
        proposal = second.proposal
        self.assertEqual(
            {key: proposal[key] for key in ("number", "variant", "printing", "mycomicshop_id")},
            {"number": "1", "variant": "U", "printing": "2nd", "mycomicshop_id": str(IVID_1U)},
        )
        # The cover as the probable-match rule reads it: an unstated printing is "" (any printing),
        # although the form value proposes "1st".
        self.assertEqual(proposal["cover_key"], {"letter": "U", "printing": "2nd", "exclusive": False})
        self.assertEqual(
            (virgin.proposal["printing"], virgin.proposal["cover_key"]),
            ("1st", {"letter": "L", "printing": "", "exclusive": False}),
        )
        self.assertNotIn("cover_key", effective_values(batch.rows.select_related("batch").get(pk=second.pk)))
        self.assertEqual(proposal["cover_url"], variant.cover_url)
        self.assertEqual(
            proposal["cover_artists"], [credit("Todd McFarlane", self.todd.pk), credit("Greg Capullo", self.greg.pk)]
        )
        self.assertIn(
            "Only the cover date is known: the release date is usually a few months earlier.", proposal["warnings"]
        )
        self.assertEqual(virgin.proposal["cover_artists"], [credit("Jason Fabok")])

    def test_an_existing_issue_keeps_its_credits(self) -> None:
        tmnt = make_publishing(
            publishing_title="Teenage Mutant Ninja Turtles vs. Street Fighter", year=2023, language="en"
        )
        existing = make_edition(publishing=tmnt, number="1", variant="Z").issue
        batch = self.create(TMNT_TID, TMNT_IVIDS)
        numbers = list(batch.numbers.all())
        self.assertEqual([(item.number, item.position) for item in numbers], [("1", 0), ("3", 1), ("4", 2)])
        self.assertEqual(numbers[0].issue, existing)
        # Its proposed story credits are kept (another publishing could make the issue new), unused.
        self.assertTrue(numbers[0].creators)
        self.assertIsNone(numbers[1].issue)
        self.assertEqual([item["name"] for item in numbers[1].creators], ["Paul Allor", "Ariel Medel", "Sarah Myer"])
        self.assertEqual([row.group_id for row in batch.rows.all()], [numbers[i].pk for i in (0, 0, 1, 1, 2)])

    def test_probable_rows_start_unselected(self) -> None:
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1P])
        probable, other = batch.rows.all()
        self.assertEqual((probable.match, probable.selected), (ImportBatchRow.Match.PROBABLE, False))
        self.assertEqual(list(probable.probable_editions.all()), [main])
        self.assertEqual((other.match, other.selected), (ImportBatchRow.Match.NONE, True))
        self.assertFalse(other.probable_editions.exists())
        # The number's issue is the main edition's: its story credits are not used.
        self.assertEqual(batch.numbers.get().issue, main.issue)
        self.assertTrue(batch.numbers.get().creators)
        self.assertFalse([name for name in pending_names(batch) if name.numbers])

    def test_a_cover_comi_does_not_download_from_is_left_out(self) -> None:
        series = series_of(VAMPI_TID)
        series.variants = [
            replace(series.variants[0], cover_url="https://example.com/cover.jpg"),
            *series.variants[1:],
        ]
        with mock.patch(FETCH_SERIES, return_value=series):
            batch = create_batch(VAMPI_TID, [series.variants[0].ivid, VAMPI_1I], self.user)
        foreign, without = batch.rows.all()
        self.assertNotIn("cover_url", foreign.proposal)
        self.assertIn(
            "The cover is on a server Comi does not download from: upload it yourself.", foreign.proposal["warnings"]
        )
        self.assertNotIn("cover_url", without.proposal)
        self.assertNotIn(
            "The cover is on a server Comi does not download from: upload it yourself.", without.proposal["warnings"]
        )

    def test_a_probable_row_can_have_several_probable_editions(self) -> None:
        # 1A states no printing, so every printing of the A cover is probably it.
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        reprint = make_edition(publishing=self.publishing, number="1", variant="A", printing="2nd")
        batch = self.create(BATMAN_TID, [IVID_1A])
        row = batch.rows.get()
        self.assertEqual((row.match, row.selected), (ImportBatchRow.Match.PROBABLE, False))
        self.assertEqual(set(row.probable_editions.all()), {main, reprint})

    def test_an_existing_issue_is_found_regardless_of_case(self) -> None:
        series = series_of(BATMAN_TID)
        series.variants = [replace(series.variants[0], number="NN")]
        existing = Issue.objects.create(publishing=self.publishing, number="nn")
        with mock.patch(FETCH_SERIES, return_value=series):
            batch = create_batch(BATMAN_TID, [series.variants[0].ivid], self.user)
        number = batch.numbers.get()
        self.assertEqual((number.number, number.issue), ("NN", existing))

    def test_a_stored_number_with_trailing_spaces_is_another_issue(self) -> None:
        # Comi's collation (utf8mb4_0900_ai_ci) is NO PAD, as SQLite compares: "1 " is not "1", the
        # same as Edition.assign_default_issue looks the issue up, so the number gets a new issue.
        Issue.objects.create(publishing=self.publishing, number="1 ")
        batch = self.create(BATMAN_TID, [IVID_1A])
        number = batch.numbers.get()
        self.assertIsNone(number.issue)
        self.assertEqual(len(number.creators), 3)

    def test_the_fields_whose_value_comi_refused_are_recorded(self) -> None:
        series = series_of(BATMAN_TID)
        variant = series.variants[0]
        series.variants = [replace(variant, details=replace(variant.details, printing=7))]
        with mock.patch(FETCH_SERIES, return_value=series):
            batch = create_batch(BATMAN_TID, [variant.ivid], self.user)
        proposal = batch.rows.get().proposal
        self.assertEqual(proposal["rejected"], ["printing"])
        self.assertNotIn("printing", proposal)
        self.assertNotIn("rejected", effective_values(batch.rows.select_related("batch").get()))
        # A source that says nothing has nothing rejected (MyComicShop never gives a series type).
        self.assertEqual(self.create(BATMAN_TID, [IVID_1A]).rows.get().proposal["rejected"], [])

    def test_a_publishing_matched_by_another_title_is_a_batch_warning(self) -> None:
        self.publishing.publishing_title = "Batman / Spawn"
        self.publishing.save()
        batch = self.create(BATMAN_TID, [IVID_1A])
        self.assertEqual(batch.publishing, self.publishing)
        self.assertEqual(
            batch.warnings, ["Matched “Batman Spawn” as “Batman / Spawn”: check that it is the same series."]
        )
        batch.refresh_from_db()
        self.assertEqual(len(batch.warnings), 1)

    def test_a_publishing_matched_by_its_title_has_no_batch_warning(self) -> None:
        self.assertEqual(self.create(BATMAN_TID, [IVID_1A]).warnings, [])

    def test_long_series_texts_are_cut(self) -> None:
        series = series_of(BATMAN_TID)
        series.title, series.publisher = "T" * 250, "P" * 250
        with mock.patch(FETCH_SERIES, return_value=series):
            batch = create_batch(BATMAN_TID, [IVID_1A], self.user)
        batch.refresh_from_db()
        self.assertEqual((batch.series_title, batch.series_publisher), ("T" * 200, "P" * 200))

    def test_no_publishing_means_new_issues(self) -> None:
        self.publishing.delete()
        batch = self.create(BATMAN_TID, [IVID_1A])
        self.assertIsNone(batch.publishing)
        self.assertIsNone(batch.numbers.get().issue)
        self.assertEqual(len(batch.numbers.get().creators), 3)


class CreateBatchRejectionTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user()

    def test_an_empty_selection_is_rejected_without_asking_mycomicshop(self) -> None:
        with mock.patch(OPEN_URL) as open_url, self.assertRaises(InvalidSelection) as raised:
            create_batch(BATMAN_TID, [], self.user)
        self.assertEqual(raised.exception.message, "Select at least one variant.")
        open_url.assert_not_called()

    def test_more_than_the_limit_is_rejected(self) -> None:
        with mock.patch(OPEN_URL) as open_url, self.assertRaises(InvalidSelection) as raised:
            create_batch(BATMAN_TID, list(range(1, MAX_BATCH_ROWS + 2)), self.user)
        self.assertEqual(raised.exception.message, f"A batch can have at most {MAX_BATCH_ROWS} variants.")
        open_url.assert_not_called()
        self.assertFalse(ImportBatch.objects.exists())

    def test_an_ivid_of_another_series_is_rejected(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer), self.assertRaises(NotFound) as raised:
            create_batch(BATMAN_TID, [IVID_1A, 999], self.user)
        self.assertIn("999", raised.exception.message)
        self.assertFalse(ImportBatch.objects.exists())

    def test_a_variant_an_edition_already_has_is_rejected(self) -> None:
        edition = make_edition(number="1", mycomicshop_id=IVID_1L)
        with mock.patch(OPEN_URL, side_effect=page_answer), self.assertRaises(Conflict) as raised:
            create_batch(BATMAN_TID, [IVID_1A, IVID_1L], self.user)
        self.assertEqual(raised.exception.message, f"{edition.short_name} already has this MyComicShop ID.")
        self.assertFalse(ImportBatch.objects.exists())

    def test_a_number_longer_than_comi_allows_is_rejected(self) -> None:
        series = series_of(BATMAN_TID)
        series.variants = [replace(series.variants[0], number="123456")]
        with mock.patch(FETCH_SERIES, return_value=series), self.assertRaises(InvalidSelection):
            create_batch(BATMAN_TID, [series.variants[0].ivid], self.user)
        self.assertFalse(ImportBatch.objects.exists())


class CreateBatchQueryTests(CatalogTestCase):
    def test_queries_do_not_grow_with_the_variants(self) -> None:
        user = make_user()
        tmnt = make_publishing(
            publishing_title="Teenage Mutant Ninja Turtles vs. Street Fighter", year=2023, language="en"
        )
        make_edition(publishing=tmnt, number="1", variant="A")  # an existing issue and a probable row
        Artist.objects.create(name="Paul Allor")
        with mock.patch(OPEN_URL, side_effect=page_answer):
            mycomicshop.fetch_series(TMNT_TID)  # cached: only Comi is queried below
            with CaptureQueriesContext(connection) as one:
                single = create_batch(TMNT_TID, TMNT_IVIDS[:1], user)
            with self.assertNumQueries(len(one.captured_queries)):
                every = create_batch(TMNT_TID, TMNT_IVIDS, user)
        self.assertEqual((single.rows.count(), every.rows.count()), (1, 5))
        self.assertEqual(every.numbers.count(), 3)


class EffectiveValuesTests(CatalogTestCase):
    def test_overrides_win_over_defaults_and_defaults_over_the_proposal(self) -> None:
        batch = make_batch(defaults={"printing": "2nd", "release_date": "2023-02-15", "format": "single_issue"})
        row = make_row(
            make_number(batch),
            proposal={
                "variant": "A",
                "printing": "1st",
                "release_date": "2023-02-01",
                "cover_price": "4.99",
                "cover_url": "https://media.mycomicshop.com/n_iv/600/1.jpg",
                "cover_artists": [credit("Todd McFarlane")],
                "warnings": ["Check it."],
            },
            overrides={"printing": "3rd"},
        )
        self.assertEqual(
            effective_values(row),
            {
                "variant": "A",
                "printing": "3rd",
                "release_date": "2023-02-15",  # a batch release date applies to every row
                "cover_price": "4.99",
                "format": "single_issue",
            },
        )
        self.assertEqual(effective_cover_url(row), "https://media.mycomicshop.com/n_iv/600/1.jpg")
        self.assertEqual(effective_cover_artists(row), [credit("Todd McFarlane")])
        row.overrides = {"release_date": "2023-03-01", "cover_url": "", "cover_artists": []}
        self.assertEqual(effective_values(row)["release_date"], "2023-03-01")
        self.assertEqual((effective_cover_url(row), effective_cover_artists(row)), ("", []))


class NameResolutionTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.todd = Artist.objects.create(name="Todd McFarlane")
        self.greg = Artist.objects.create(name="Greg Capullo")
        self.batch = make_batch()
        first = make_number(
            self.batch,
            "1",
            creators=[
                credit("Todd McFarlane", self.todd.pk),
                credit("Jonathan Glapion"),
                credit("Greg Capullo", self.greg.pk, uncertain=True),
            ],
        )
        second = make_number(self.batch, "2", position=1)
        make_row(first, 1, proposal={"cover_artists": [credit("Jonathan Glapion"), credit("Jason Fabok")]})
        make_row(
            first,
            2,
            position=1,
            proposal={"cover_artists": [credit("Somebody Else")]},
            overrides={"cover_artists": [credit("jason  FABOK"), credit("Todd McFarlane", self.todd.pk)]},
        )
        make_row(second, 3, position=2, proposal={"cover_artists": [credit("Brett Booth")]})

    def test_pending_names_in_first_appearance_order(self) -> None:
        with self.assertNumQueries(2):
            pending = pending_names(self.batch)
        self.assertEqual(
            pending,
            [
                PendingName("Jonathan Glapion", None, numbers=1, rows=1),
                PendingName("Greg Capullo", self.greg.pk, numbers=1, rows=0),
                PendingName("Jason Fabok", None, numbers=0, rows=2),  # the override replaces the proposal
                PendingName("Brett Booth", None, numbers=0, rows=1),
            ],
        )

    def test_the_story_credits_of_an_existing_issue_are_not_names_of_the_batch(self) -> None:
        number = self.batch.numbers.get(number="1")
        number.issue = make_edition(number="1").issue
        number.save()
        pending = pending_names(self.batch)
        # "Greg Capullo" was only a (guessed) story credit; the others are cover artists too.
        self.assertEqual(
            pending,
            [
                PendingName("Jonathan Glapion", None, numbers=0, rows=1),
                PendingName("Jason Fabok", None, numbers=0, rows=2),
                PendingName("Brett Booth", None, numbers=0, rows=1),
            ],
        )
        with self.assertRaises(ValidationError):
            resolve_name(self.batch, "Greg Capullo", "discard")

    def test_a_resolved_name_is_no_longer_pending(self) -> None:
        resolve_name(self.batch, "  JASON fabok ", "new")
        self.assertEqual(
            self.batch.name_resolutions,
            {"jason fabok": {"name": "Jason Fabok", "action": "new", "artist_id": None, "new_name": "Jason Fabok"}},
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions["jason fabok"]["action"], "new")
        self.assertNotIn("Jason Fabok", [item.name for item in pending_names(self.batch)])
        clear_name_resolution(self.batch, "Jason Fabok")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions, {})

    def test_clearing_a_name_without_resolution_changes_nothing(self) -> None:
        resolve_name(self.batch, "Brett Booth", "discard")
        self.batch.refresh_from_db()
        before = (self.batch.name_resolutions, self.batch.updated_at)
        clear_name_resolution(self.batch, "Jason Fabok")
        clear_name_resolution(self.batch, "Nobody")  # not even a name of the batch
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.name_resolutions, self.batch.updated_at), before)

    def test_a_new_artist_can_be_given_a_corrected_name(self) -> None:
        resolve_name(self.batch, "Jason Fabok", "new", new_name="  Jason   Fabok Jr. ")
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.name_resolutions["jason fabok"],
            {"name": "Jason Fabok", "action": "new", "artist_id": None, "new_name": "Jason Fabok Jr."},
        )
        self.assertEqual(resolved_credits(self.batch, [credit("jason fabok")]).new_names, ["Jason Fabok Jr."])

    def test_a_new_name_an_artist_already_has_resolves_to_that_artist(self) -> None:
        # "Brett" was cut by the catalogue's text; corrected, it is an artist Comi already has.
        booth = Artist.objects.create(name="Brett Booth")
        resolve_name(self.batch, "Brett Booth", "new", new_name="BRETT  booth")
        self.assertEqual(
            self.batch.name_resolutions["brett booth"],
            {"name": "Brett Booth", "action": "artist", "artist_id": booth.pk},
        )
        self.assertEqual(Artist.objects.filter(name__iexact="brett booth").count(), 1)

    def test_invalid_new_names_are_rejected(self) -> None:
        cases = [
            ("Jason Fabok", "new", "   ", "Enter a name."),
            ("Jason Fabok", "new", "J" * 101, f"“{'J' * 101}” is longer than 100 characters."),
            ("Jason Fabok", "discard", "Jason Fabok", "A new name is only given to create an artist."),
        ]
        for name, action, new_name, message in cases:
            with self.subTest(action=action, new_name=new_name), self.assertRaises(ValidationError) as raised:
                resolve_name(self.batch, name, action, new_name=new_name)
            self.assertEqual(raised.exception.messages, [message])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions, {})

    def test_names_corrected_into_the_same_one_are_one_new_artist(self) -> None:
        resolve_name(self.batch, "Jason Fabok", "new", new_name="Jason Fabok")
        resolve_name(self.batch, "Brett Booth", "new", new_name="jason fabok")
        result = resolved_credits(self.batch, [credit("Jason Fabok"), credit("Brett Booth")])
        self.assertEqual(result.new_names, ["Jason Fabok"])

    def test_every_action_is_stored(self) -> None:
        resolve_name(self.batch, "Greg Capullo", "artist", self.greg.pk)
        resolve_name(self.batch, "Jonathan Glapion", "discard")
        resolve_name(self.batch, "Todd McFarlane", "discard")  # a matched name can be dropped too
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.name_resolutions,
            {
                "greg capullo": {"name": "Greg Capullo", "action": "artist", "artist_id": self.greg.pk},
                "jonathan glapion": {"name": "Jonathan Glapion", "action": "discard", "artist_id": None},
                "todd mcfarlane": {"name": "Todd McFarlane", "action": "discard", "artist_id": None},
            },
        )

    def test_invalid_resolutions_are_rejected(self) -> None:
        cases = [
            ("Jason Fabok", "rename", None, "Choose what to do with the name."),
            ("Jason Fabok", "artist", None, "Choose an existing artist."),
            ("Jason Fabok", "artist", self.greg.pk + 1000, "Choose an existing artist."),
            ("Jason Fabok", "new", self.greg.pk, "An artist is only given to associate the name with it."),
            ("Nobody", "new", None, "“Nobody” is not a name of this batch."),
        ]
        for name, action, artist_id, message in cases:
            with self.subTest(action=action, artist_id=artist_id), self.assertRaises(ValidationError) as raised:
                resolve_name(self.batch, name, action, artist_id)
            self.assertEqual(raised.exception.messages, [message])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions, {})

    def test_a_closed_batch_cannot_be_resolved(self) -> None:
        ImportBatch.objects.filter(pk=self.batch.pk).update(status=ImportBatch.Status.CLOSED)
        calls = [
            lambda: resolve_name(self.batch, "Jason Fabok", "new"),
            lambda: clear_name_resolution(self.batch, "Jason Fabok"),
        ]
        for call in calls:
            with self.assertRaises(ValidationError) as raised:
                call()
            self.assertEqual(raised.exception.messages, ["The batch is closed."])

    def test_resolved_credits(self) -> None:
        other = Artist.objects.create(name="Jason Fabok (the real one)")
        resolve_name(self.batch, "Jonathan Glapion", "new")
        resolve_name(self.batch, "Jason Fabok", "artist", other.pk)
        resolve_name(self.batch, "Brett Booth", "discard")
        credits = [
            credit("Todd McFarlane", self.todd.pk),
            credit("Jonathan Glapion"),
            credit("Greg Capullo", self.greg.pk, uncertain=True),  # guessed and not resolved
            credit("jason fabok"),
            credit("Brett Booth"),
            credit("Unknown Person"),
            credit("todd mcfarlane", self.todd.pk),
            credit("JONATHAN GLAPION"),
        ]
        with self.assertNumQueries(0):
            result = resolved_credits(self.batch, credits)
        self.assertEqual(result.artist_ids, [self.todd.pk, other.pk])
        self.assertEqual(result.new_names, ["Jonathan Glapion"])
        self.assertEqual(result.unresolved, ["Greg Capullo", "Unknown Person"])
