"""Models of the MyComicShop import batches: defaults, uniqueness, deletions and labels."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from comics.models import ImportBatch, ImportBatchNumber, ImportBatchRow, Issue

from .base import ComiTestCase
from .factories import make_edition, make_publishing, make_user


def make_batch(**fields: object) -> ImportBatch:
    fields.setdefault("series_id", 12345)
    fields.setdefault("series_title", "Spawn")
    return ImportBatch.objects.create(**fields)


def make_number(batch: ImportBatch, number: str = "1", **fields: object) -> ImportBatchNumber:
    fields.setdefault("position", 0)
    return ImportBatchNumber.objects.create(batch=batch, number=number, **fields)


def make_row(group: ImportBatchNumber, ivid: int = 1001, **fields: object) -> ImportBatchRow:
    fields.setdefault("position", 0)
    fields.setdefault("label", f"{group.number}A")
    return ImportBatchRow.objects.create(batch=group.batch, group=group, ivid=ivid, **fields)


class ImportBatchDefaultsTests(ComiTestCase):
    def test_a_new_batch_is_open_and_empty(self) -> None:
        batch = make_batch()
        batch.refresh_from_db()
        self.assertEqual(batch.source, "mycomicshop")
        self.assertEqual(batch.status, ImportBatch.Status.OPEN)
        self.assertEqual(batch.defaults, {})
        self.assertEqual(batch.name_resolutions, {})
        self.assertIsNone(batch.publishing)
        self.assertIsNotNone(batch.created_at)
        self.assertIsNotNone(batch.updated_at)

    def test_a_new_row_is_pending_selected_and_without_match(self) -> None:
        row = make_row(make_number(make_batch()))
        row.refresh_from_db()
        self.assertEqual(row.status, ImportBatchRow.Status.PENDING)
        self.assertEqual(row.match, ImportBatchRow.Match.NONE)
        self.assertTrue(row.selected)
        self.assertEqual((row.proposal, row.overrides, row.errors), ({}, {}, {}))
        self.assertIsNone(row.edition)
        self.assertEqual(row.group.creators, [])

    def test_json_values_round_trip(self) -> None:
        batch = make_batch(
            defaults={"format": "single_issue", "rarity": "rare"},
            name_resolutions={"todd mcfarlane": {"name": "Todd McFarlane", "action": "new", "artist_id": None}},
        )
        group = make_number(batch, creators=[{"name": "Todd McFarlane", "artist_id": None, "uncertain": True}])
        row = make_row(group, proposal={"variant": "A", "cover_url": "https://example.com/1.jpg"})
        batch.refresh_from_db()
        group.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(batch.name_resolutions["todd mcfarlane"]["action"], "new")
        self.assertTrue(group.creators[0]["uncertain"])
        self.assertEqual(row.proposal["cover_url"], "https://example.com/1.jpg")

    def test_rows_and_numbers_follow_their_position(self) -> None:
        batch = make_batch()
        second = make_number(batch, "2", position=1)
        first = make_number(batch, "1", position=0)
        make_row(first, 11, position=1, label="1B")
        make_row(first, 10, position=0, label="1A")
        self.assertEqual(list(batch.numbers.all()), [first, second])
        self.assertEqual([row.label for row in batch.rows.all()], ["1A", "1B"])


class ImportBatchConstraintTests(ComiTestCase):
    def test_a_number_appears_once_per_batch(self) -> None:
        batch = make_batch()
        make_number(batch, "1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_number(batch, "1", position=1)
        make_number(make_batch(), "1")  # another batch may repeat it

    def test_an_ivid_appears_once_per_batch(self) -> None:
        group = make_number(make_batch())
        make_row(group, 1001)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_row(group, 1001, position=1)
        make_row(make_number(make_batch()), 1001)  # another batch may repeat it

    def test_clean_refuses_a_number_of_another_batch(self) -> None:
        group = make_number(make_batch())
        row = ImportBatchRow(batch=make_batch(), group=group, ivid=1, position=0, label="1A")
        with self.assertRaises(ValidationError) as caught:
            row.full_clean()
        self.assertIn("group", caught.exception.message_dict)
        make_row(group).full_clean()  # same batch: valid


class ImportBatchDeletionTests(ComiTestCase):
    def test_deleting_a_batch_deletes_its_numbers_and_rows(self) -> None:
        batch = make_batch()
        make_row(make_number(batch))
        other = make_row(make_number(make_batch()))
        batch.delete()
        self.assertFalse(ImportBatchNumber.objects.filter(batch_id=batch.pk).exists())
        self.assertEqual(list(ImportBatchRow.objects.all()), [other])

    def test_deleting_a_number_deletes_its_rows(self) -> None:
        group = make_number(make_batch())
        make_row(group)
        group.delete()
        self.assertFalse(ImportBatchRow.objects.exists())

    def test_deleting_the_user_keeps_the_batch(self) -> None:
        user = make_user()
        batch = make_batch(created_by=user)
        user.delete()
        batch.refresh_from_db()
        self.assertIsNone(batch.created_by)

    def test_deleting_the_publishing_keeps_the_batch(self) -> None:
        publishing = make_publishing()
        batch = make_batch(publishing=publishing)
        publishing.delete()
        batch.refresh_from_db()
        self.assertIsNone(batch.publishing)

    def test_deleting_the_issue_keeps_the_number(self) -> None:
        issue = Issue.objects.create(publishing=make_publishing(), number="1")
        group = make_number(make_batch(), issue=issue)
        issue.delete()
        group.refresh_from_db()
        self.assertIsNone(group.issue)

    def test_deleting_an_edition_keeps_the_row(self) -> None:
        created = make_edition()
        probable = make_edition()
        row = make_row(make_number(make_batch()), edition=created, match=ImportBatchRow.Match.PROBABLE)
        row.probable_editions.add(probable)
        created.delete()
        probable.delete()
        row.refresh_from_db()
        self.assertIsNone(row.edition)
        self.assertFalse(row.probable_editions.exists())


class ImportBatchLabelTests(ComiTestCase):
    def test_str(self) -> None:
        batch = make_batch(series_title="Spawn", series_year=1992)
        group = make_number(batch, "1")
        row = make_row(group, 2002, label="1RI.A")
        self.assertEqual(str(batch), f"#{batch.pk} Spawn (1992)")
        untitled_year = make_batch(series_title="Spawn")
        self.assertEqual(str(untitled_year), f"#{untitled_year.pk} Spawn")
        self.assertEqual(str(group), f"#1 (batch #{batch.pk})")
        self.assertEqual(str(row), "1RI.A (IVID 2002)")
