"""Creating the editions of an import batch (comics.catalog_sources.batch_creation): every selected row
through the Gestión edition form, the batch's new artists once, failures kept per row, and linking a
probable row to an edition Comi already has. Cover downloads are mocked."""

import json
from dataclasses import replace
from typing import Any
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from comics.catalog_sources import batch_creation, mycomicshop
from comics.catalog_sources.batch_creation import create_rows, link_row
from comics.catalog_sources.batches import (
    clear_name_resolution,
    close_batch,
    create_batch,
    pending_names,
    resolve_name,
    set_publishing,
)
from comics.catalog_sources.errors import Conflict, NotFound
from comics.catalog_sources.service import link_mycomicshop_edition
from comics.catalog_sources.types import SourceIssue
from comics.editions import create_edition
from comics.history import versions_of
from comics.models import Artist, Edition, ImportBatch, ImportBatchNumber, ImportBatchRow, Issue

from .factories import make_edition, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase, FakeResponse, png_bytes
from .test_import_batch_service import FETCH_SERIES
from .test_import_batches import make_batch, make_number, make_row
from .test_mycomicshop_import import BATMAN_TID, series_of

TID = 57389539
MCS_COVER = "https://media.mycomicshop.com/n_iv/600/57507058.jpg"
CREATE_EDITION = "comics.catalog_sources.batch_creation.create_edition"


def credit(name: str, artist_id: int | None = None, uncertain: bool = False) -> dict[str, Any]:
    return {"name": name, "artist_id": artist_id, "uncertain": uncertain}


def proposal(variant: str, **values: Any) -> dict[str, Any]:
    """A row proposal as the batch service builds it (edition form values as strings)."""
    data = {
        "variant": variant,
        "printing": "1st",
        "release_date": "2023-02-01",
        "cover_price": "4.99",
        "format": "single_issue",
        "cover_artists": [],
        "warnings": [],
    }
    data.update(values)
    return data


class BatchCreationTestCase(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user()
        self.publishing = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        self.todd = Artist.objects.create(name="Todd McFarlane")
        self.greg = Artist.objects.create(name="Greg Capullo")
        self.batch = make_batch(publishing=self.publishing, series_id=TID, created_by=self.user)

    def create(self, **kwargs: Any) -> Any:
        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())) as open_url:
            result = create_rows(self.batch, user=self.user, **kwargs)
        self.open_url = open_url
        return result

    def row(self, group: ImportBatchNumber, ivid: int, position: int, variant: str, **fields: Any) -> ImportBatchRow:
        fields.setdefault("proposal", proposal(variant))
        return make_row(group, ivid, position=position, label=f"{group.number}{variant}", **fields)


class CreateRowsTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        # #1 already exists in Comi with its story credits; #2 is new.
        self.existing = make_edition(publishing=self.publishing, number="1", variant="A")
        self.existing.issue.creators.set([self.todd])
        one = make_number(self.batch, "1", issue=self.existing.issue)
        two = make_number(
            self.batch, "2", position=1, creators=[credit("Todd McFarlane", self.todd.pk), credit("Jonathan Glapion")]
        )
        self.one_b = self.row(
            one,
            101,
            0,
            "B",
            proposal=proposal(
                "B", cover_url=MCS_COVER, cover_artists=[credit("Greg Capullo", self.greg.pk)], ratio="1:25"
            ),
        )
        self.two_a = self.row(two, 201, 1, "A", proposal=proposal("A", cover_artists=[credit("Jonathan Glapion")]))
        self.two_b = self.row(
            two, 202, 2, "B", proposal=proposal("B", cover_artists=[credit("Greg Capullo", self.greg.pk, True)])
        )
        resolve_name(self.batch, "Jonathan Glapion", "new")
        resolve_name(self.batch, "Greg Capullo", "artist", self.greg.pk)

    def test_the_selected_rows_become_editions(self) -> None:
        result = self.create()
        self.assertEqual([row.pk for row in result.created], [self.one_b.pk, self.two_a.pk, self.two_b.pk])
        self.assertEqual(result.failed, [])
        glapion = Artist.objects.get(name="Jonathan Glapion")
        self.assertEqual(result.artists, [glapion])
        for row in (self.one_b, self.two_a, self.two_b):
            row.refresh_from_db()
            self.assertEqual((row.status, row.errors), (ImportBatchRow.Status.CREATED, {}))
            self.assertEqual(row.edition.mycomicshop_id, row.ivid)
            self.assertEqual((row.edition.publishing, row.edition.number), (self.publishing, row.group.number))
        one_b, two_a, two_b = (row.edition for row in (self.one_b, self.two_a, self.two_b))
        self.assertEqual((one_b.variant, one_b.ratio, one_b.rarity), ("B", "1:25", "common"))
        self.assertEqual(list(one_b.cover_artists.all()), [self.greg])
        self.assertEqual(list(two_a.cover_artists.all()), [glapion])
        self.assertEqual(list(two_b.cover_artists.all()), [self.greg])
        # The cover is downloaded for the row that has one.
        self.assertEqual(self.open_url.call_count, 1)
        self.assertEqual(self.open_url.call_args.args[1].full_url, MCS_COVER)
        self.assertTrue(one_b.image)
        self.assertFalse(two_a.image)
        # The existing issue keeps its credits; the new one gets the resolved story credits.
        self.assertEqual(one_b.issue, self.existing.issue)
        self.assertEqual(list(self.existing.issue.creators.all()), [self.todd])
        self.assertEqual(two_a.issue, two_b.issue)
        self.assertEqual(set(two_a.issue.creators.all()), {self.todd, glapion})
        self.assertEqual(Issue.objects.count(), 2)
        # The publishing gets the batch's TID; the batch stays open.
        self.assertTrue(result.series_id_filled)
        self.publishing.refresh_from_db()
        self.assertEqual(self.publishing.mycomicshop_series_id, TID)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.OPEN)

    def test_the_versions_are_by_the_given_user(self) -> None:
        existing_versions = versions_of(Issue, self.existing.issue_id).count()
        result = self.create()
        reason = f"Imported from MyComicShop (batch #{self.batch.pk})."
        new_issue = result.created[1].edition.issue
        for model, pk in [(Edition, row.edition.pk) for row in result.created] + [(Issue, new_issue.pk)]:
            with self.subTest(model=model.__name__, pk=pk):
                versions = list(versions_of(model, pk))
                self.assertEqual(len(versions), 1)
                self.assertEqual((versions[0].history_user, versions[0].history_change_reason), (self.user, reason))
        self.assertEqual(versions_of(Issue, self.existing.issue_id).count(), existing_versions)  # untouched
        custom = make_row(make_number(self.batch, "3", position=2), 301, position=3, proposal=proposal("A"))
        self.create(reason="Custom reason")
        custom.refresh_from_db()
        self.assertEqual(versions_of(Edition, custom.edition.pk)[0].history_change_reason, "Custom reason")

    def test_a_failing_row_does_not_stop_the_others(self) -> None:
        self.one_b.overrides = {"cover_price": "lots"}
        self.one_b.save()
        result = self.create()
        self.assertEqual([row.pk for row in result.failed], [self.one_b.pk])
        self.assertEqual([row.pk for row in result.created], [self.two_a.pk, self.two_b.pk])
        self.one_b.refresh_from_db()
        self.assertEqual(self.one_b.status, ImportBatchRow.Status.FAILED)
        self.assertIsNone(self.one_b.edition)
        self.assertEqual(list(self.one_b.errors), ["cover_price"])
        json.dumps(self.one_b.errors)
        self.assertFalse(Edition.objects.filter(mycomicshop_id=101).exists())

    def test_rerunning_only_retries_failed_and_pending_rows(self) -> None:
        self.one_b.overrides = {"cover_price": "lots"}
        self.one_b.save()
        first = self.create()
        created = {row.pk: row.edition_id for row in first.created}
        unselected = self.row(self.one_b.group, 102, 3, "C", selected=False)
        linked = self.row(self.one_b.group, 103, 4, "D", status=ImportBatchRow.Status.LINKED)
        self.one_b.overrides = {}
        self.one_b.save()
        with mock.patch(CREATE_EDITION, wraps=create_edition) as spy:
            second = self.create()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([row.pk for row in second.created], [self.one_b.pk])
        self.assertEqual((second.failed, second.artists), ([], []))
        self.assertFalse(second.series_id_filled)  # the publishing already has it
        for row in (self.two_a, self.two_b):
            row.refresh_from_db()
            self.assertEqual(row.edition_id, created[row.pk])
        for row in (unselected, linked):
            row.refresh_from_db()
            self.assertIsNone(row.edition)
        self.assertEqual(Edition.objects.count(), 4)  # the existing one and the three rows
        self.assertEqual(Artist.objects.filter(name="Jonathan Glapion").count(), 1)

    def test_unresolved_names_fail_the_row_without_creating_anything(self) -> None:
        self.batch.name_resolutions = {}
        self.batch.save()
        self.two_a.overrides = {"cover_artists": [credit("Unknown Person"), credit("Jonathan Glapion")]}
        self.two_a.save()
        with mock.patch(CREATE_EDITION, wraps=create_edition) as spy:
            result = self.create()
        self.assertEqual(spy.call_count, 1)  # only the row without unresolved names is tried
        self.assertEqual([row.pk for row in result.failed], [self.two_a.pk, self.two_b.pk])
        self.assertEqual(result.artists, [])
        self.two_a.refresh_from_db()
        self.assertEqual(self.two_a.status, ImportBatchRow.Status.FAILED)
        self.assertEqual(
            self.two_a.errors,
            {
                "cover_artists": ["Resolve these names first: Unknown Person, Jonathan Glapion."],
                "creators": ["Resolve these names first: Jonathan Glapion."],
            },
        )
        self.two_b.refresh_from_db()
        self.assertEqual(
            self.two_b.errors,
            {
                "cover_artists": ["Resolve these names first: Greg Capullo."],  # guessed, not confirmed
                "creators": ["Resolve these names first: Jonathan Glapion."],
            },
        )
        # A number whose issue exists never saves story credits, so they need no resolution.
        self.assertEqual([row.pk for row in result.created], [self.one_b.pk])
        self.assertFalse(Artist.objects.filter(name="Jonathan Glapion").exists())
        self.assertFalse(Issue.objects.filter(number="2").exists())
        self.assertFalse(Edition.objects.filter(mycomicshop_id__in=[201, 202]).exists())

    def test_the_issue_a_row_creates_becomes_the_numbers_issue(self) -> None:
        self.two_b.overrides = {"cover_price": "lots"}
        self.two_b.save()
        self.create()
        self.two_a.refresh_from_db()
        group = ImportBatchNumber.objects.get(pk=self.two_a.group_id)
        self.assertEqual(group.issue, self.two_a.edition.issue)
        # Its story credits are saved, so the retried sibling needs no story resolutions: a story name
        # added since is no name of the batch, and even a "new" resolution stored for it is not created.
        clear_name_resolution(self.batch, "Jonathan Glapion")
        ImportBatchNumber.objects.filter(pk=group.pk).update(creators=[*group.creators, credit("Ghost Writer")])
        self.assertNotIn("Ghost Writer", [name.name for name in pending_names(self.batch)])
        self.batch.name_resolutions["ghost writer"] = {
            "name": "Ghost Writer",
            "action": "new",
            "artist_id": None,
            "new_name": "Ghost Writer",
        }
        self.batch.save(update_fields=["name_resolutions"])
        self.two_b.overrides = {}
        self.two_b.save()
        result = self.create()
        self.assertEqual([row.pk for row in result.created], [self.two_b.pk])
        self.assertEqual(result.artists, [])
        self.assertFalse(Artist.objects.filter(name="Ghost Writer").exists())
        self.two_b.refresh_from_db()
        self.assertEqual(self.two_b.edition.issue, group.issue)

    def test_a_publishing_chosen_during_the_run_stops_it(self) -> None:
        """Another request gives the batch another publishing after the run read it: the run creates
        nothing in the old publishing and leaves the numbers as set_publishing found them."""
        other = make_publishing(publishing_title="Batman/Spawn")
        create_new_artists = batch_creation._create_new_artists

        def then_change_the_publishing(*args: Any, **kwargs: Any) -> Any:
            created = create_new_artists(*args, **kwargs)
            set_publishing(ImportBatch.objects.get(pk=self.batch.pk), other)
            return created

        with mock.patch.object(batch_creation, "_create_new_artists", side_effect=then_change_the_publishing):
            result = self.create()
        self.assertTrue(result.interrupted)
        self.assertEqual((result.created, result.failed, result.series_id_filled), ([], [], False))
        self.assertFalse(Edition.objects.filter(mycomicshop_id__in=[101, 201, 202]).exists())
        self.assertEqual(dict(self.batch.numbers.values_list("number", "issue_id")), {"1": None, "2": None})
        self.publishing.refresh_from_db()
        self.assertIsNone(self.publishing.mycomicshop_series_id)

    def test_a_batch_closed_during_the_run_stops_it(self) -> None:
        def close(row: ImportBatchRow) -> None:
            close_batch(ImportBatch.objects.get(pk=self.batch.pk))

        result = self.create(on_row_created=close)
        self.assertTrue(result.interrupted)
        self.assertEqual([row.pk for row in result.created], [self.one_b.pk])
        for row in (self.two_a, self.two_b):
            row.refresh_from_db()
            self.assertEqual(row.status, ImportBatchRow.Status.PENDING)

    def test_the_publishing_cannot_change_once_a_row_is_created(self) -> None:
        """set_publishing asked for while a row is being created sees it CREATED and refuses."""
        refused: list[str] = []

        def change_the_publishing(row: ImportBatchRow) -> None:
            try:
                set_publishing(ImportBatch.objects.get(pk=self.batch.pk), make_publishing())
            except ValidationError as exc:
                refused.extend(exc.messages)

        result = self.create(on_row_created=change_the_publishing)
        self.assertFalse(result.interrupted)
        self.assertEqual(len(result.created), 3)
        self.assertEqual(
            refused,
            ["The publishing can no longer change: some variants of the batch already have their edition."] * 3,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.publishing, self.publishing)

    def test_a_row_taken_by_another_run_is_skipped(self) -> None:
        def other_run_takes_two_b(*args: Any, **kwargs: Any) -> Any:
            ImportBatchRow.objects.filter(pk=self.two_b.pk).update(status=ImportBatchRow.Status.CREATED)
            return create_edition(*args, **kwargs)

        with mock.patch(CREATE_EDITION, side_effect=other_run_takes_two_b) as spy:
            result = self.create()
        self.assertEqual(spy.call_count, 2)
        self.assertEqual([row.pk for row in result.created], [self.one_b.pk, self.two_a.pk])
        self.assertEqual(result.failed, [])
        self.two_b.refresh_from_db()
        self.assertIsNone(self.two_b.edition)


class PreselectedValuesTests(BatchCreationTestCase):
    """A row gets the values the Gestión page preselects only for what its source says nothing about."""

    def test_a_silent_source_gets_the_preselected_values(self) -> None:
        silent = {"variant": "A", "release_date": "2023-02-01", "cover_price": "4.99", "rejected": []}
        row = self.row(make_number(self.batch), 100, 0, "A", proposal=silent)
        result = self.create()
        self.assertEqual([item.pk for item in result.created], [row.pk])
        edition = result.created[0].edition
        self.assertEqual((edition.printing, edition.format, edition.rarity), ("1st", "single_issue", "common"))

    def built_row(self, series: Any) -> ImportBatchRow:
        with mock.patch(FETCH_SERIES, return_value=series):
            batch = create_batch(TID, [series.variants[0].ivid], self.user)
        ImportBatch.objects.filter(pk=batch.pk).update(publishing=self.publishing)
        for pending in pending_names(batch):  # only the edition values are under test
            resolve_name(batch, pending.name, "discard")
        self.batch = batch
        return batch.rows.get()

    def assert_fails_on(self, row: ImportBatchRow, field: str, value: str) -> None:
        with mock.patch(CREATE_EDITION, wraps=create_edition) as spy:
            result = self.create()
        spy.assert_not_called()  # refused before the form, whatever the field's default
        self.assertEqual([item.pk for item in result.failed], [row.pk])
        row.refresh_from_db()
        self.assertEqual(row.status, ImportBatchRow.Status.FAILED)
        self.assertEqual(row.errors, {field: ["Comi did not accept the catalogue's value: fill it in yourself."]})
        self.assertFalse(Edition.objects.exists())
        # The batch defaults (or the row's overrides) let the user fill it in.
        ImportBatch.objects.filter(pk=self.batch.pk).update(defaults={field: value})
        [created] = self.create().created
        self.assertEqual(created.pk, row.pk)
        self.assertEqual(getattr(created.edition, field), value)

    def test_a_rejected_printing_fails_the_row(self) -> None:
        series = series_of(BATMAN_TID)
        variant = series.variants[0]
        series.variants = [replace(variant, details=replace(variant.details, printing=7))]
        row = self.built_row(series)
        self.assertEqual(row.proposal["rejected"], ["printing"])
        self.assert_fails_on(row, "printing", "6th")

    def test_a_rejected_variant_fails_the_row_instead_of_becoming_the_main_cover(self) -> None:
        series = series_of(BATMAN_TID)
        series.variants = [replace(series.variants[0], suffix="X" * 31)]  # longer than Edition.variant
        row = self.built_row(series)
        self.assertEqual(row.proposal["rejected"], ["variant"])
        self.assertNotIn("variant", row.proposal)
        self.assert_fails_on(row, "variant", "XRI")

    def test_an_override_of_a_rejected_variant_lets_the_row_be_created(self) -> None:
        series = series_of(BATMAN_TID)
        series.variants = [replace(series.variants[0], suffix="X" * 31)]
        row = self.built_row(series)
        row.overrides = {"variant": "XRI"}
        row.save()
        [created] = self.create().created
        self.assertEqual(created.edition.variant, "XRI")

    def test_a_rejected_format_fails_the_row(self) -> None:
        series = series_of(BATMAN_TID)
        series.variants = series.variants[:1]
        original = mycomicshop.to_source_issue

        def with_series_type(*args: Any) -> SourceIssue:
            return replace(original(*args), series_type="Mystery Type")

        with mock.patch.object(mycomicshop, "to_source_issue", side_effect=with_series_type):
            row = self.built_row(series)
        self.assertEqual(row.proposal["rejected"], ["format"])
        self.assertNotIn("format", row.proposal)
        self.assert_fails_on(row, "format", "trade_paperback")


class NewArtistsTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        group = make_number(self.batch, "1", creators=[credit("Jason Fab")])
        for position, variant in enumerate("AB"):
            covers = [credit("Jason Fab")]
            self.row(group, 100 + position, position, variant, proposal=proposal(variant, cover_artists=covers))

    def test_a_new_artist_is_created_once_with_its_corrected_name(self) -> None:
        resolve_name(self.batch, "Jason Fab", "new", new_name="Jason Fabok")
        result = self.create()
        fabok = Artist.objects.get(name="Jason Fabok")
        self.assertEqual(result.artists, [fabok])
        self.assertFalse(Artist.objects.filter(name="Jason Fab").exists())
        self.assertEqual(len(result.created), 2)
        for row in result.created:
            self.assertEqual(list(row.edition.cover_artists.all()), [fabok])
        self.assertEqual(list(result.created[0].edition.issue.creators.all()), [fabok])
        # The resolution now names the artist, so a later run finds it as any other.
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.name_resolutions["jason fab"], {"name": "Jason Fab", "action": "artist", "artist_id": fabok.pk}
        )

    def test_an_artist_created_since_the_resolution_is_reused(self) -> None:
        resolve_name(self.batch, "Jason Fab", "new", new_name="Jason Fabok")
        fabok = Artist.objects.create(name="JASON  FABOK")
        result = self.create()
        self.assertEqual(result.artists, [])
        self.assertEqual(Artist.objects.filter(name__icontains="fabok").count(), 1)
        self.assertEqual(list(result.created[0].edition.cover_artists.all()), [fabok])

    def test_an_artist_created_by_another_request_meanwhile_is_retried_once(self) -> None:
        resolve_name(self.batch, "Jason Fab", "new")
        ensure_named = Artist.objects.ensure_named
        calls: list[int] = []

        def clash_first(names: list[str]) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError("Duplicate entry")
            return ensure_named(names)

        with mock.patch.object(Artist.objects, "ensure_named", side_effect=clash_first):
            result = self.create()
        self.assertEqual(len(calls), 2)
        self.assertEqual([artist.name for artist in result.artists], ["Jason Fab"])
        self.assertEqual(len(result.created), 2)

    def test_artists_that_cannot_be_created_reject_the_run(self) -> None:
        resolve_name(self.batch, "Jason Fab", "new")
        with (
            mock.patch.object(Artist.objects, "ensure_named", side_effect=IntegrityError("Duplicate entry")),
            self.assertRaises(ValidationError) as raised,
        ):
            self.create()
        self.assertEqual(raised.exception.messages, ["The artists could not be created. Try again."])
        self.assertFalse(Edition.objects.exists())
        self.assertFalse(self.batch.rows.exclude(status=ImportBatchRow.Status.PENDING).exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions["jason fab"]["action"], "new")

    def test_new_artists_of_rows_not_tried_are_not_created(self) -> None:
        resolve_name(self.batch, "Jason Fab", "new")
        self.batch.rows.update(selected=False)
        result = self.create()
        self.assertEqual((result.created, result.failed, result.artists), ([], [], []))
        self.assertFalse(Artist.objects.filter(name="Jason Fab").exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions["jason fab"]["action"], "new")


class CreateRowsRejectionTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pending = self.row(make_number(self.batch), 100, 0, "A")

    def assert_rejected(self, message: str) -> None:
        with self.assertRaises(ValidationError) as raised:
            self.create()
        self.assertEqual(raised.exception.messages, [message])
        self.pending.refresh_from_db()
        self.assertEqual((self.pending.status, self.pending.errors), (ImportBatchRow.Status.PENDING, {}))
        self.assertFalse(Edition.objects.exists())

    def test_a_closed_batch_is_rejected(self) -> None:
        ImportBatch.objects.filter(pk=self.batch.pk).update(status=ImportBatch.Status.CLOSED)
        self.assert_rejected("The batch is closed.")

    def test_a_batch_without_publishing_is_rejected(self) -> None:
        ImportBatch.objects.filter(pk=self.batch.pk).update(publishing=None)
        self.assert_rejected("Choose the publishing of the batch first.")


class LinkRowTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.main = make_edition(publishing=self.publishing, number="1", variant="A")
        self.reprint = make_edition(publishing=self.publishing, number="1", variant="A", printing="2nd")
        self.group = make_number(self.batch, issue=self.main.issue)
        self.probable = self.row(self.group, 500, 0, "A", match=ImportBatchRow.Match.PROBABLE, selected=False)
        self.probable.probable_editions.set([self.main, self.reprint])

    def test_a_probable_row_is_linked_to_one_of_its_editions(self) -> None:
        link = link_row(self.batch, self.probable, self.reprint.pk, user=self.user)
        self.assertTrue(link.changed)
        self.assertTrue(link.series_id_filled)
        self.assertIs(link.row, self.probable)
        self.probable.refresh_from_db()
        self.assertEqual(
            (self.probable.status, self.probable.edition, self.probable.errors),
            (ImportBatchRow.Status.LINKED, self.reprint, {}),
        )
        self.reprint.refresh_from_db()
        self.assertEqual(self.reprint.mycomicshop_id, 500)
        self.assertEqual(versions_of(Edition, self.reprint.pk)[0].history_user, self.user)
        self.publishing.refresh_from_db()
        self.assertEqual(self.publishing.mycomicshop_series_id, TID)
        # A linked row is not created afterwards.
        self.probable.selected = True
        self.probable.save()
        self.assertEqual(self.create().created, [])

    def test_invalid_links_are_rejected(self) -> None:
        other_batch = make_batch(publishing=self.publishing)
        foreign = self.row(make_number(other_batch), 600, 0, "A")
        unrelated = make_edition(publishing=self.publishing, number="1", variant="B")
        created = self.row(self.group, 501, 1, "A", status=ImportBatchRow.Status.CREATED)
        created.probable_editions.set([self.main])
        cases = [
            (self.probable, unrelated.pk, "Choose one of the editions the variant may be."),
            (foreign, self.main.pk, "The variant is not in this batch."),
            (created, self.main.pk, "The variant already has its edition."),
        ]
        for row, edition_id, message in cases:
            with self.subTest(message=message), self.assertRaises(ValidationError) as raised:
                link_row(self.batch, row, edition_id, user=self.user)
            self.assertEqual(raised.exception.messages, [message])
        ImportBatch.objects.filter(pk=self.batch.pk).update(status=ImportBatch.Status.CLOSED)
        with self.assertRaises(ValidationError) as raised:
            link_row(self.batch, self.probable, self.main.pk, user=self.user)
        self.assertEqual(raised.exception.messages, ["The batch is closed."])
        self.assertFalse(Edition.objects.filter(mycomicshop_id__isnull=False).exists())

    def test_an_edition_deleted_meanwhile_is_raised_and_the_row_is_unchanged(self) -> None:
        def deleted_first(edition_id: int, *args: Any, **kwargs: Any) -> Any:
            Edition.objects.filter(pk=edition_id).delete()  # by another request, after the check
            return link_mycomicshop_edition(edition_id, *args, **kwargs)

        with (
            mock.patch("comics.catalog_sources.batch_creation.link_mycomicshop_edition", side_effect=deleted_first),
            self.assertRaises(NotFound),
        ):
            link_row(self.batch, self.probable, self.reprint.pk, user=self.user)
        self.probable.refresh_from_db()
        self.assertEqual(
            (self.probable.status, self.probable.edition, self.probable.errors),
            (ImportBatchRow.Status.PENDING, None, {}),
        )

    def test_a_link_conflict_is_raised_and_the_row_is_unchanged(self) -> None:
        Edition.objects.filter(pk=self.main.pk).update(mycomicshop_id=999)
        with self.assertRaises(Conflict):
            link_row(self.batch, self.probable, self.main.pk, user=self.user)
        self.probable.refresh_from_db()
        self.assertEqual((self.probable.status, self.probable.edition), (ImportBatchRow.Status.PENDING, None))
