"""Choosing the publishing of an import batch (batches.set_publishing: the numbers' issues and the rows'
probable editions looked up again, without asking MyComicShop) and selecting its rows
(batches.set_selection). HTTP is mocked with the MyComicShop fixture pages."""

from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from comics.catalog_sources.batches import create_batch, pending_names, set_publishing, set_selection
from comics.models import ImportBatch, ImportBatchRow, Publishing

from .factories import make_edition, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase
from .test_import_batch_service import TMNT_IVIDS
from .test_import_batches import make_batch, make_number, make_row
from .test_mycomicshop_import import BATMAN_TID, TMNT_TID, page_answer
from .test_mycomicshop_link import IVID_1A, IVID_1L, IVID_1U


class BatchPublishingTestCase(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user()

    def create(self, tid: int, ivids: list[int]) -> ImportBatch:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            return create_batch(tid, ivids, self.user)

    def rows(self, batch: ImportBatch) -> dict[int, ImportBatchRow]:
        """The rows of `batch` by IVID, read again."""
        return {row.ivid: row for row in batch.rows.all()}


class SetPublishingTests(BatchPublishingTestCase):
    def test_choosing_a_publishing_finds_its_issue_and_probable_editions(self) -> None:
        # No publishing of Comi matches the series when the batch is built.
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1L, IVID_1U])
        self.assertIsNone(batch.publishing)
        target = make_publishing(publishing_title="Batman/Spawn")
        main = make_edition(publishing=target, number="1", variant="A")
        make_edition(publishing=make_publishing(), number="1", variant="L")  # in another publishing
        self.assertTrue(set_publishing(batch, target))
        self.assertEqual(batch.publishing, target)
        batch.refresh_from_db()
        self.assertEqual((batch.publishing, batch.warnings), (target, []))
        number = batch.numbers.get()
        self.assertEqual(number.issue, main.issue)
        self.assertTrue(number.creators)  # kept, but unused while the issue exists
        self.assertFalse([name for name in pending_names(batch) if name.numbers])
        rows = self.rows(batch)
        self.assertEqual((rows[IVID_1A].match, rows[IVID_1A].selected), (ImportBatchRow.Match.PROBABLE, False))
        self.assertEqual(list(rows[IVID_1A].probable_editions.all()), [main])
        for ivid in (IVID_1L, IVID_1U):
            self.assertEqual((rows[ivid].match, rows[ivid].selected), (ImportBatchRow.Match.NONE, True))
            self.assertFalse(rows[ivid].probable_editions.exists())

    def test_the_stored_cover_keeps_the_modal_rule(self) -> None:
        """An unstated printing matches every printing; a stated one only itself (service.CoverKey)."""
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1U])
        target = make_publishing(publishing_title="Batman/Spawn")
        main = make_edition(publishing=target, number="1", variant="A")
        reprint = make_edition(publishing=target, number="1", variant="A", printing="2nd")
        make_edition(publishing=target, number="1", variant="U")  # 1U is a 2nd printing: not this one
        make_edition(publishing=target, number="1", variant="U", printing="2nd", retailer_exclusive="X")
        set_publishing(batch, target)
        rows = self.rows(batch)
        self.assertEqual(set(rows[IVID_1A].probable_editions.all()), {main, reprint})
        # The regular 2nd printing U is no candidate: the only 2nd printing U is an exclusive.
        self.assertFalse(rows[IVID_1U].probable_editions.exists())
        self.assertEqual(rows[IVID_1U].match, ImportBatchRow.Match.NONE)

    def test_leaving_the_matched_publishing_makes_the_issue_new(self) -> None:
        matched = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        main = make_edition(publishing=matched, number="1", variant="A")
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1L])
        self.assertEqual((batch.publishing, batch.numbers.get().issue), (matched, main.issue))
        before = self.rows(batch)
        self.assertEqual((before[IVID_1A].match, before[IVID_1A].selected), (ImportBatchRow.Match.PROBABLE, False))
        empty = make_publishing(publishing_title="Batman/Spawn")
        set_publishing(batch, empty)
        number = batch.numbers.get()
        self.assertIsNone(number.issue)
        # The story credits proposed when the batch was built are used now: their names are pending.
        self.assertIn("Jonathan Glapion", [name.name for name in pending_names(batch) if name.numbers])
        rows = self.rows(batch)
        # No longer probable: selected, as a batch built with this publishing would have it.
        self.assertEqual((rows[IVID_1A].match, rows[IVID_1A].selected), (ImportBatchRow.Match.NONE, True))
        self.assertFalse(rows[IVID_1A].probable_editions.exists())
        self.assertTrue(rows[IVID_1L].selected)

    def test_a_row_still_probable_keeps_its_selection(self) -> None:
        matched = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        make_edition(publishing=matched, number="1", variant="A")
        batch = self.create(BATMAN_TID, [IVID_1A])
        batch.rows.update(selected=True)  # the user chose to create it anyway
        target = make_publishing(publishing_title="Batman/Spawn")
        other_main = make_edition(publishing=target, number="1", variant="A")
        set_publishing(batch, target)
        row = batch.rows.get()
        self.assertEqual((row.match, row.selected), (ImportBatchRow.Match.PROBABLE, True))
        self.assertEqual(list(row.probable_editions.all()), [other_main])

    def test_failed_rows_are_pending_again_and_the_warnings_dropped(self) -> None:
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1L])
        batch.warnings = ["Matched “Batman Spawn” as “Batman / Spawn”: check that it is the same series."]
        batch.save()
        batch.rows.filter(ivid=IVID_1A).update(status=ImportBatchRow.Status.FAILED, errors={"number": ["Taken."]})
        set_publishing(batch, make_publishing())
        batch.refresh_from_db()
        self.assertEqual(batch.warnings, [])
        row = batch.rows.get(ivid=IVID_1A)
        self.assertEqual((row.status, row.errors), (ImportBatchRow.Status.PENDING, {}))

    def test_the_same_publishing_only_drops_the_warnings(self) -> None:
        publishing = make_publishing()
        batch = make_batch(publishing=publishing, warnings=["Check it."])
        row = make_row(make_number(batch), status=ImportBatchRow.Status.FAILED, errors={"number": ["Taken."]})
        self.assertFalse(set_publishing(batch, publishing))
        self.assertEqual(batch.warnings, [])
        batch.refresh_from_db()
        self.assertEqual(batch.warnings, [])
        row.refresh_from_db()
        self.assertEqual(row.status, ImportBatchRow.Status.FAILED)  # nothing recomputed

    def test_a_batch_with_editions_cannot_change_its_publishing(self) -> None:
        matched = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        main = make_edition(publishing=matched, number="1", variant="A")
        batch = self.create(BATMAN_TID, [IVID_1A, IVID_1L])
        other = make_publishing()
        for status in (ImportBatchRow.Status.CREATED, ImportBatchRow.Status.LINKED):
            with self.subTest(status=status):
                batch.rows.filter(ivid=IVID_1L).update(status=status)
                with self.assertRaises(ValidationError) as raised:
                    set_publishing(batch, other)
                self.assertEqual(
                    raised.exception.messages,
                    ["The publishing can no longer change: some variants of the batch already have their edition."],
                )
                batch.refresh_from_db()
                self.assertEqual((batch.publishing, batch.numbers.get().issue), (matched, main.issue))
                self.assertEqual(batch.rows.get(ivid=IVID_1A).match, ImportBatchRow.Match.PROBABLE)

    def test_a_closed_batch_cannot_change_its_publishing(self) -> None:
        batch = make_batch(status=ImportBatch.Status.CLOSED)
        publishing = make_publishing()
        with self.assertRaises(ValidationError) as raised:
            set_publishing(batch, publishing)
        self.assertEqual(raised.exception.messages, ["The batch is closed."])
        batch.refresh_from_db()
        self.assertIsNone(batch.publishing)

    def test_a_row_without_a_stored_cover_is_never_probable(self) -> None:
        batch = make_batch()
        row = make_row(make_number(batch), proposal={"variant": "A", "printing": "1st"})
        target = make_publishing()
        make_edition(publishing=target, number="1", variant="A")
        set_publishing(batch, target)
        row.refresh_from_db()
        self.assertEqual(row.match, ImportBatchRow.Match.NONE)

    def test_queries_do_not_grow_with_the_batch(self) -> None:
        small = self.create(TMNT_TID, TMNT_IVIDS[:1])
        large = self.create(TMNT_TID, TMNT_IVIDS)  # 3 numbers, 5 rows
        self.assertEqual((small.publishing, large.publishing), (None, None))
        target = make_publishing(publishing_title="TMNT vs. Street Fighter")
        make_edition(publishing=target, number="1", variant="A")
        make_edition(publishing=target, number="3", variant="A")
        for batch in (small, large):
            batch.rows.filter(ivid=TMNT_IVIDS[0]).update(status=ImportBatchRow.Status.FAILED)

        def queries(batch: ImportBatch) -> int:
            with CaptureQueriesContext(connection) as captured:
                set_publishing(batch, Publishing.objects.get(pk=target.pk))
            return len(captured)

        self.assertEqual(queries(small), queries(large))
        self.assertEqual(
            sorted(large.rows.filter(match=ImportBatchRow.Match.PROBABLE).values_list("ivid", flat=True)),
            sorted([TMNT_IVIDS[0], TMNT_IVIDS[2]]),
        )


class SetSelectionTests(BatchPublishingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.batch = make_batch()
        group = make_number(self.batch)
        self.pending = make_row(group, 1, position=0)
        self.failed = make_row(group, 2, position=1, status=ImportBatchRow.Status.FAILED)
        self.probable = make_row(group, 3, position=2, match=ImportBatchRow.Match.PROBABLE, selected=False)
        self.created = make_row(group, 4, position=3, status=ImportBatchRow.Status.CREATED)
        self.linked = make_row(group, 5, position=4, status=ImportBatchRow.Status.LINKED, selected=False)

    def selection(self) -> dict[int, bool]:
        return dict(self.batch.rows.values_list("ivid", "selected"))

    def test_the_given_rows_are_selected_and_the_others_not(self) -> None:
        foreign = make_row(make_number(make_batch()), 6, selected=False)
        changed = set_selection(self.batch, {self.failed.pk, self.probable.pk, self.linked.pk, foreign.pk})
        self.assertEqual(changed, 2)  # the pending row unselected, the probable one selected
        # Rows with their edition never change, nor do rows of another batch.
        self.assertEqual(self.selection(), {1: False, 2: True, 3: True, 4: True, 5: False})
        foreign.refresh_from_db()
        self.assertFalse(foreign.selected)

    def test_all_and_none(self) -> None:
        self.assertEqual(set_selection(self.batch, None), 1)
        self.assertEqual(self.selection(), {1: True, 2: True, 3: True, 4: True, 5: False})
        self.assertEqual(set_selection(self.batch, set()), 3)
        self.assertEqual(self.selection(), {1: False, 2: False, 3: False, 4: True, 5: False})

    def test_a_closed_batch_cannot_change_its_selection(self) -> None:
        ImportBatch.objects.filter(pk=self.batch.pk).update(status=ImportBatch.Status.CLOSED)
        with self.assertRaises(ValidationError) as raised:
            set_selection(self.batch, None)
        self.assertEqual(raised.exception.messages, ["The batch is closed."])
        self.assertFalse(self.batch.rows.get(ivid=3).selected)

    def test_queries_do_not_grow_with_the_rows(self) -> None:
        small = make_batch()
        make_row(make_number(small), 10)
        with CaptureQueriesContext(connection) as one:
            set_selection(small, [])
        with self.assertNumQueries(len(one.captured_queries)):
            set_selection(self.batch, [])
