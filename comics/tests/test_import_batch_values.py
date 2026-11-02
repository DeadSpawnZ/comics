"""Editing what an import batch will create (comics.import_batch_values): the values every
variant inherits, each variant's own changes and cover artists, and changes made while the batch's
rows are being created. Cover downloads are mocked."""

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError

from comics.import_batch_values import clear_row_overrides, set_defaults, set_row_overrides
from comics.catalog_sources.batches import close_batch, pending_names, resolve_name
from comics.models import Artist, ImportBatch, ImportBatchRow

from .factories import make_edition
from .test_import_batch_creation import BatchCreationTestCase, credit, proposal
from .test_import_batches import make_batch, make_number


def chosen(artist: Artist) -> dict[str, Any]:
    return {"name": artist.name, "artist_id": artist.pk, "uncertain": False, "chosen": True}


class SetDefaultsTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.group = make_number(self.batch)

    def test_values_are_stored_as_the_edition_form_is_posted(self) -> None:
        values = {
            "release_date": "2024-03-05",
            "cover_price": "3.5",
            "format": "trade_paperback",
            "rarity": "",
            "printing": "",
            "notes": "  Reprint  ",
        }
        stored = set_defaults(self.batch, values)
        expected = {"release_date": "2024-03-05", "cover_price": "3.5", "format": "trade_paperback", "notes": "Reprint"}
        self.assertEqual(stored, expected)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, expected)

    def test_an_empty_field_is_not_set(self) -> None:
        set_defaults(self.batch, {"format": "trade_paperback", "notes": "Reprint"})
        set_defaults(self.batch, {"format": "", "notes": "Reprint"})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {"notes": "Reprint"})

    def test_only_the_batch_wide_values_are_taken(self) -> None:
        set_defaults(self.batch, {"variant": "B", "ratio": "1:25", "rarity": "rare"})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {"rarity": "rare"})

    def test_invalid_values_are_rejected_with_the_edition_forms_errors(self) -> None:
        set_defaults(self.batch, {"rarity": "rare"})
        with self.assertRaises(ValidationError) as caught:
            set_defaults(
                self.batch,
                {"cover_price": "abc", "release_date": "2024-02-31", "printing": "7th", "notes": "x" * 501},
            )
        self.assertEqual(set(caught.exception.message_dict), {"cover_price", "release_date", "printing", "notes"})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {"rarity": "rare"})

    def test_a_closed_batch_is_rejected(self) -> None:
        close_batch(self.batch)
        with self.assertRaisesMessage(ValidationError, "The batch is closed."):
            set_defaults(self.batch, {"rarity": "rare"})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {})

    def test_the_rows_are_created_with_the_defaults(self) -> None:
        inheriting = self.row(self.group, 101, 0, "A")
        overriding = self.row(self.group, 102, 1, "B", overrides={"release_date": "2024-02-02"})
        set_defaults(self.batch, {"format": "trade_paperback", "release_date": "2024-01-01", "cover_price": "5.99"})
        created = {row.pk: row.edition for row in self.create().created}
        first, second = created[inheriting.pk], created[overriding.pk]
        self.assertEqual(
            (first.format, first.release_date, first.cover_price),
            ("trade_paperback", date(2024, 1, 1), Decimal("5.99")),
        )
        self.assertEqual((second.format, second.release_date), ("trade_paperback", date(2024, 2, 2)))

    def test_failed_rows_are_pending_again_only_when_the_defaults_change(self) -> None:
        failed = self.row(self.group, 101, 0, "A", status=ImportBatchRow.Status.FAILED, errors={"__all__": ["Boom."]})
        set_defaults(self.batch, {})
        failed.refresh_from_db()
        self.assertEqual(failed.status, ImportBatchRow.Status.FAILED)
        set_defaults(self.batch, {"rarity": "rare"})
        failed.refresh_from_db()
        self.assertEqual((failed.status, failed.errors), (ImportBatchRow.Status.PENDING, {}))


class SetRowOverridesTests(BatchCreationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.group = make_number(self.batch)
        self.batch_row = self.row(
            self.group,
            101,
            0,
            "B",
            proposal=proposal(
                "B", ratio="1:25", cover_artists=[credit("Greg Capulo"), credit("Todd McFarlane", self.todd.pk)]
            ),
        )

    def overrides(self) -> dict[str, Any]:
        self.batch_row.refresh_from_db()
        return self.batch_row.overrides

    def test_each_value_is_inherited_given_or_left_blank(self) -> None:
        changed = set_row_overrides(
            self.batch,
            self.batch_row,
            {"variant": "", "ratio": "1:50", "cover_price": "5", "blank_retailer_exclusive": "on", "rarity": "rare"},
        )
        self.assertTrue(changed)
        self.assertEqual(
            self.overrides(), {"ratio": "1:50", "cover_price": "5", "retailer_exclusive": "", "rarity": "rare"}
        )
        # Saving the same values again changes nothing.
        self.assertFalse(
            set_row_overrides(
                self.batch,
                self.batch_row,
                {"ratio": "1:50", "cover_price": "5", "blank_retailer_exclusive": "on", "rarity": "rare"},
            )
        )

    def test_leave_blank_wins_over_a_typed_value(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"notes": "Typed", "blank_notes": "on"})
        self.assertEqual(self.overrides(), {"notes": ""})

    def test_a_required_value_cannot_be_left_blank(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"blank_printing": "on", "blank_release_date": "on"})
        self.assertEqual(self.overrides(), {})

    def test_invalid_values_are_rejected_with_the_edition_forms_errors(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50"})
        with self.assertRaises(ValidationError) as caught:
            set_row_overrides(
                self.batch,
                self.batch_row,
                {"ratio": "abc", "limited_to": "12x", "release_date": "nope", "variant": "X" * 31},
            )
        self.assertEqual(set(caught.exception.message_dict), {"ratio", "limited_to", "release_date", "variant"})
        self.assertEqual(self.overrides(), {"ratio": "1:50"})

    def test_other_override_keys_are_kept(self) -> None:
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(overrides={"cover_url": "", "ratio": "1:5"})
        set_row_overrides(self.batch, self.batch_row, {"notes": "Note"})
        self.assertEqual(self.overrides(), {"cover_url": "", "notes": "Note"})

    def test_a_blank_refused_optional_value_creates_the_row_without_it(self) -> None:
        # The catalogue's variant was too long for Comi: the row fails on it until the user decides.
        refused = proposal("B", rejected=["variant"])
        del refused["variant"]
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(proposal=refused)
        [failed] = self.create().failed
        self.assertEqual(set(failed.errors), {"variant"})
        set_row_overrides(self.batch, self.batch_row, {"blank_variant": "on"})
        self.batch_row.refresh_from_db()
        self.assertEqual((self.batch_row.status, self.batch_row.errors), (ImportBatchRow.Status.PENDING, {}))
        [created] = self.create().created
        self.assertEqual(created.edition.variant, "")

    def test_cover_artists_are_dropped_and_added_for_this_row(self) -> None:
        other = self.row(self.group, 102, 1, "C", proposal=proposal("C", cover_artists=[credit("Greg Capulo")]))
        [pending] = pending_names(self.batch)
        self.assertEqual((pending.name, pending.rows), ("Greg Capulo", 2))
        changes = {"drop_artists": ["Greg Capulo"], "add_artists": [str(self.greg.pk)]}
        set_row_overrides(self.batch, self.batch_row, changes)
        self.assertEqual(self.overrides()["cover_artists"], [credit("Todd McFarlane", self.todd.pk), chosen(self.greg)])
        # The other row still has the name to resolve; this one no longer counts.
        [pending] = pending_names(self.batch)
        self.assertEqual(pending.rows, 1)
        set_row_overrides(self.batch, other, {"drop_artists": ["Greg Capulo"]})
        self.assertEqual(pending_names(self.batch), [])
        created = {row.pk: row.edition for row in self.create().created}
        self.assertEqual(set(created[self.batch_row.pk].cover_artists.all()), {self.todd, self.greg})
        self.assertEqual(list(created[other.pk].cover_artists.all()), [])

    def test_an_added_artist_replaces_the_credit_with_its_name(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"add_artists": [str(self.todd.pk)]})
        self.assertEqual(self.overrides()["cover_artists"], [credit("Greg Capulo"), chosen(self.todd)])

    def test_an_added_artist_replaces_a_certain_credit_of_that_artist_under_another_name(self) -> None:
        # The catalogue spells Todd differently, but the credit certainly is him: adding him keeps him once.
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(
            proposal=proposal("B", cover_artists=[credit("T. McFarlane", self.todd.pk), credit("Greg Capulo")])
        )
        set_row_overrides(self.batch, self.batch_row, {"add_artists": [str(self.todd.pk)]})
        self.assertEqual(self.overrides()["cover_artists"], [credit("Greg Capulo"), chosen(self.todd)])

    def test_an_added_artist_keeps_a_guessed_credit_of_that_artist_under_another_name(self) -> None:
        # A guessed credit still has to be resolved: only a certain one is that artist already.
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(
            proposal=proposal("B", cover_artists=[credit("T. McFarlane", self.todd.pk, True)])
        )
        set_row_overrides(self.batch, self.batch_row, {"add_artists": [str(self.todd.pk)]})
        self.assertEqual(
            self.overrides()["cover_artists"], [credit("T. McFarlane", self.todd.pk, True), chosen(self.todd)]
        )

    def test_an_added_artist_ignores_the_batch_resolutions(self) -> None:
        # The name is discarded for the batch (e.g. a wrong story credit), but this row chose the artist.
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(proposal=proposal("B"))
        self.group.creators = [credit("Greg Capullo", self.greg.pk, True)]
        self.group.save()
        resolve_name(self.batch, "Greg Capullo", "discard")
        set_row_overrides(self.batch, self.batch_row, {"add_artists": [str(self.greg.pk)]})
        self.assertEqual(pending_names(self.batch), [])
        [created] = self.create().created
        self.assertEqual(list(created.edition.cover_artists.all()), [self.greg])
        self.assertEqual(list(created.edition.issue.creators.all()), [])

    def test_an_unchanged_cover_list_is_not_stored(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50"})
        self.assertNotIn("cover_artists", self.overrides())

    def test_an_unknown_artist_is_rejected(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            set_row_overrides(self.batch, self.batch_row, {"add_artists": ["999999"]})
        self.assertEqual(caught.exception.message_dict, {"cover_artists": ["Choose existing artists."]})
        with self.assertRaises(ValidationError) as caught:
            set_row_overrides(self.batch, self.batch_row, {"drop_artists": ["Somebody Else"]})
        self.assertEqual(set(caught.exception.message_dict), {"cover_artists"})
        self.assertEqual(self.overrides(), {})

    def test_rows_with_their_edition_are_rejected(self) -> None:
        edition = make_edition(publishing=self.publishing, number="1", variant="Z")
        for status in (ImportBatchRow.Status.CREATED, ImportBatchRow.Status.LINKED):
            with self.subTest(status=status):
                ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(status=status, edition=edition)
                with self.assertRaisesMessage(ValidationError, "The variant already has its edition."):
                    set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50"})
                with self.assertRaisesMessage(ValidationError, "The variant already has its edition."):
                    clear_row_overrides(self.batch, self.batch_row)
                self.assertEqual(self.overrides(), {})

    def test_a_row_of_another_batch_is_rejected(self) -> None:
        other = make_batch(publishing=self.publishing)
        with self.assertRaisesMessage(ValidationError, "The variant is not in this batch."):
            set_row_overrides(other, self.batch_row, {"ratio": "1:50"})

    def test_a_closed_batch_is_rejected(self) -> None:
        close_batch(self.batch)
        with self.assertRaisesMessage(ValidationError, "The batch is closed."):
            set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50"})
        self.assertEqual(self.overrides(), {})

    def test_a_failed_row_is_pending_again(self) -> None:
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(
            status=ImportBatchRow.Status.FAILED, errors={"ratio": ["Bad."]}
        )
        set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50"})
        self.assertEqual(
            (self.batch_row.status, self.batch_row.errors, self.batch_row.overrides),
            (ImportBatchRow.Status.PENDING, {}, {"ratio": "1:50"}),
        )

    def test_clearing_goes_back_to_the_proposal(self) -> None:
        set_row_overrides(self.batch, self.batch_row, {"ratio": "1:50", "drop_artists": ["Greg Capulo"]})
        self.assertTrue(clear_row_overrides(self.batch, self.batch_row))
        self.assertEqual(self.overrides(), {})
        self.assertFalse(clear_row_overrides(self.batch, self.batch_row))

    def test_clearing_a_failed_row_makes_it_pending_again(self) -> None:
        ImportBatchRow.objects.filter(pk=self.batch_row.pk).update(
            status=ImportBatchRow.Status.FAILED, errors={"ratio": ["Bad."]}, overrides={"ratio": "abc"}
        )
        self.assertTrue(clear_row_overrides(self.batch, self.batch_row))
        self.assertEqual(
            (self.batch_row.status, self.batch_row.errors, self.batch_row.overrides),
            (ImportBatchRow.Status.PENDING, {}, {}),
        )
        self.batch_row.refresh_from_db()
        self.assertEqual((self.batch_row.status, self.batch_row.errors), (ImportBatchRow.Status.PENDING, {}))


class ChangesDuringARunTests(BatchCreationTestCase):
    """create_rows reads the batch again for every row: what changes during the run applies to the rows left."""

    def setUp(self) -> None:
        super().setUp()
        group = make_number(self.batch)
        self.first = self.row(group, 101, 0, "A")
        self.second = self.row(group, 102, 1, "B", proposal=proposal("B", cover_artists=[credit("Greg Capulo")]))

    def test_a_name_resolved_during_the_run_applies_to_the_rows_left(self) -> None:
        def resolve(row: ImportBatchRow) -> None:
            if row.pk == self.first.pk:
                resolve_name(self.batch, "Greg Capulo", "artist", self.greg.pk)

        result = self.create(on_row_created=resolve)
        self.assertEqual([row.pk for row in result.created], [self.first.pk, self.second.pk])
        self.assertEqual(list(result.created[1].edition.cover_artists.all()), [self.greg])

    def test_defaults_set_during_the_run_apply_to_the_rows_left(self) -> None:
        resolve_name(self.batch, "Greg Capulo", "discard")

        def change_defaults(row: ImportBatchRow) -> None:
            if row.pk == self.first.pk:
                set_defaults(self.batch, {"format": "trade_paperback"})

        result = self.create(on_row_created=change_defaults)
        self.assertEqual([row.edition.format for row in result.created], ["single_issue", "trade_paperback"])
        self.assertEqual(ImportBatch.objects.get(pk=self.batch.pk).defaults, {"format": "trade_paperback"})
