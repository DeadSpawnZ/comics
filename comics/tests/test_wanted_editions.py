"""Hunted editions (WantedEdition): one mark per collector and edition, allowed for owned editions,
completed (kept, with its note, and notified) by a new purchase of the buyer, brought back from
there, and removed along with a deleted edition."""

import datetime
from unittest import mock

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.signals import post_save

from comics.models import Collection, Notification, NotificationManager, WantedEdition

from .base import ComiTestCase
from .factories import (
    make_collection,
    make_edition,
    make_editorial,
    make_publishing,
    make_user,
    make_wanted_edition,
)


class WantedEditionModelTests(ComiTestCase):
    def setUp(self) -> None:
        self.collector = make_user()
        self.edition = make_edition()

    def test_a_collector_can_want_an_edition_only_once(self) -> None:
        make_wanted_edition(collector=self.collector, edition=self.edition)

        with self.assertRaises(IntegrityError), transaction.atomic():
            WantedEdition.objects.create(collector=self.collector, edition=self.edition)

    def test_two_collectors_can_want_the_same_edition(self) -> None:
        other = make_user()

        make_wanted_edition(collector=self.collector, edition=self.edition)
        make_wanted_edition(collector=other, edition=self.edition)

        self.assertEqual(self.edition.wanted_by.count(), 2)

    def test_an_owned_edition_can_be_wanted(self) -> None:
        make_collection(collector=self.collector, edition=self.edition)

        mark = WantedEdition(collector=self.collector, edition=self.edition)
        mark.full_clean()
        mark.save()

        self.assertTrue(self.collector.wanted_editions.filter(edition=self.edition).exists())

    def test_a_mark_can_be_saved_with_or_without_a_note(self) -> None:
        for edition, notes in ((self.edition, ""), (make_edition(), "Looking for a 9.8 to replace my copy")):
            mark = WantedEdition(collector=self.collector, edition=edition, notes=notes)
            mark.full_clean()
            mark.save()
            mark.refresh_from_db()
            self.assertEqual(mark.notes, notes)

    def test_deleting_the_edition_deletes_its_marks(self) -> None:
        make_wanted_edition(collector=self.collector, edition=self.edition)
        make_wanted_edition(collector=make_user(), edition=self.edition)

        self.edition.delete()  # no ProtectedError

        self.assertFalse(WantedEdition.objects.exists())


class HuntCompletedByPurchaseTests(ComiTestCase):
    def setUp(self) -> None:
        self.buyer = make_user()
        self.other = make_user()
        self.edition = make_edition()

    def hunt_notifications(self) -> list[Notification]:
        return list(Notification.objects.filter(kind=Notification.Kind.HUNT_COMPLETED))

    def test_a_new_purchase_completes_the_buyers_mark_and_notifies_them(self) -> None:
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition, notes="A 9.8 to replace mine")

        make_collection(collector=self.buyer, edition=self.edition, trade_type=Collection.TradeChoices.BUYING)

        mark.refresh_from_db()  # the row is kept
        self.assertIsNotNone(mark.completed_at)
        self.assertEqual(mark.notes, "A 9.8 to replace mine")
        [notification] = self.hunt_notifications()
        self.assertEqual(notification.recipient, self.buyer)
        self.assertEqual(notification.edition, self.edition)
        self.assertTrue(notification.is_unread)

    def test_only_the_buyers_mark_of_that_edition_is_completed(self) -> None:
        own_other_edition = make_wanted_edition(collector=self.buyer, edition=make_edition())
        others_mark = make_wanted_edition(collector=self.other, edition=self.edition)
        make_wanted_edition(collector=self.buyer, edition=self.edition)

        make_collection(collector=self.buyer, edition=self.edition)

        self.assertEqual(
            set(WantedEdition.objects.active().values_list("pk", flat=True)), {own_other_edition.pk, others_mark.pk}
        )
        self.assertEqual([item.recipient for item in self.hunt_notifications()], [self.buyer])

    def test_a_purchase_without_an_active_mark_does_nothing(self) -> None:
        mark = make_wanted_edition(collector=self.other, edition=self.edition)

        make_collection(collector=self.buyer, edition=self.edition)

        self.assertEqual(list(WantedEdition.objects.active()), [mark])
        self.assertEqual(self.hunt_notifications(), [])

    def test_a_purchase_with_an_already_completed_mark_does_not_notify_again(self) -> None:
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition)
        make_collection(collector=self.buyer, edition=self.edition)
        mark.refresh_from_db()
        first_completion = mark.completed_at

        make_collection(collector=self.buyer, edition=self.edition)  # a second copy

        mark.refresh_from_db()
        self.assertEqual(mark.completed_at, first_completion)
        self.assertEqual(len(self.hunt_notifications()), 1)

    def test_buying_again_after_bringing_the_hunt_back_notifies_again(self) -> None:
        make_wanted_edition(collector=self.buyer, edition=self.edition, notes="Signed")
        make_collection(collector=self.buyer, edition=self.edition)
        WantedEdition.objects.resume_hunt(self.buyer, self.edition)

        make_collection(collector=self.buyer, edition=self.edition)

        mark = WantedEdition.objects.get(collector=self.buyer, edition=self.edition)
        self.assertIsNotNone(mark.completed_at)
        self.assertEqual(mark.notes, "Signed")
        self.assertEqual(len(self.hunt_notifications()), 2)

    def test_a_sale_keeps_the_mark_active(self) -> None:
        purchase = make_collection(collector=self.buyer, edition=self.edition, trade_date=datetime.date(2020, 1, 1))
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition)

        make_collection(
            collector=self.buyer,
            edition=self.edition,
            trade_type=Collection.TradeChoices.SELLING,
            trade_date=datetime.date(2021, 1, 1),  # sold a year after the purchase
            previous_trade=purchase,
        )

        self.assertEqual(list(WantedEdition.objects.active()), [mark])
        self.assertEqual(self.hunt_notifications(), [])

    def test_editing_an_existing_purchase_keeps_a_mark_created_afterwards(self) -> None:
        purchase = make_collection(collector=self.buyer, edition=self.edition)
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition)

        purchase.amount += 1
        purchase.save()

        self.assertEqual(list(WantedEdition.objects.active()), [mark])
        self.assertEqual(self.hunt_notifications(), [])

    def test_a_raw_save_from_loaddata_keeps_the_mark(self) -> None:
        purchase = make_collection(collector=self.buyer, edition=self.edition)
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition)

        post_save.send(sender=Collection, instance=purchase, created=True, raw=True, using="default")

        self.assertEqual(list(WantedEdition.objects.active()), [mark])
        self.assertEqual(self.hunt_notifications(), [])

    def test_a_failed_notification_rolls_the_purchase_back(self) -> None:
        mark = make_wanted_edition(collector=self.buyer, edition=self.edition)

        with (
            mock.patch.object(NotificationManager, "hunt_completed", side_effect=DatabaseError("boom")),
            self.assertRaises(DatabaseError),
        ):
            make_collection(collector=self.buyer, edition=self.edition)

        mark.refresh_from_db()
        self.assertIsNone(mark.completed_at)
        self.assertFalse(Collection.objects.exists())


class WantedEditionQuerySetTests(ComiTestCase):
    def setUp(self) -> None:
        self.collector = make_user()
        self.other = make_user()

    def test_for_collector_lists_only_their_marks_newest_first(self) -> None:
        # `newer` gets the lower id, so only ordering by created_at puts it first.
        newer = make_wanted_edition(collector=self.collector, edition=make_edition())
        older = make_wanted_edition(collector=self.collector, edition=make_edition())
        make_wanted_edition(collector=self.other, edition=older.edition)
        WantedEdition.objects.filter(pk=older.pk).update(created_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))

        self.assertEqual(list(WantedEdition.objects.for_collector(self.collector)), [newer, older])

    def test_for_collector_loads_what_the_edition_names_read(self) -> None:
        for _ in range(3):
            publishing = make_publishing(editorials=[make_editorial()])
            make_wanted_edition(collector=self.collector, edition=make_edition(publishing=publishing))

        # The marks with their editions and publishings, then the editorials of all of them.
        with self.assertNumQueries(2):
            names = [str(mark.edition) for mark in WantedEdition.objects.for_collector(self.collector)]

        self.assertEqual(len(names), 3)
        self.assertTrue(all(" US-" in name for name in names))

    def test_edition_ids_wanted_by_returns_the_wanted_ids_among_the_given_ones(self) -> None:
        wanted, not_listed, not_wanted, wanted_by_other = (make_edition() for _ in range(4))
        make_wanted_edition(collector=self.collector, edition=wanted)
        make_wanted_edition(collector=self.collector, edition=not_listed)
        make_wanted_edition(collector=self.other, edition=wanted_by_other)

        ids = WantedEdition.objects.edition_ids_wanted_by(
            self.collector, [wanted.pk, not_wanted.pk, wanted_by_other.pk]
        )

        self.assertEqual(ids, {wanted.pk})

    def test_edition_ids_wanted_by_with_no_ids_is_empty(self) -> None:
        make_wanted_edition(collector=self.collector, edition=make_edition())

        self.assertEqual(WantedEdition.objects.edition_ids_wanted_by(self.collector, []), set())

    def test_completed_marks_are_not_wanted(self) -> None:
        active = make_wanted_edition(collector=self.collector, edition=make_edition())
        completed = make_wanted_edition(collector=self.collector, edition=make_edition())
        make_collection(collector=self.collector, edition=completed.edition)

        self.assertEqual(list(WantedEdition.objects.for_collector(self.collector)), [active])
        self.assertEqual(
            WantedEdition.objects.edition_ids_wanted_by(self.collector, [active.edition_id, completed.edition_id]),
            {active.edition_id},
        )


class SetWantedTests(ComiTestCase):
    def setUp(self) -> None:
        self.collector = make_user()
        self.edition = make_edition()

    def complete(self, notes: str = "") -> WantedEdition:
        """A mark of the collector completed by a purchase."""
        mark = make_wanted_edition(collector=self.collector, edition=self.edition, notes=notes)
        make_collection(collector=self.collector, edition=self.edition)
        mark.refresh_from_db()
        self.assertIsNotNone(mark.completed_at)
        return mark

    def test_marking_creates_an_active_mark(self) -> None:
        WantedEdition.objects.set_wanted(self.collector, self.edition, True)

        mark = WantedEdition.objects.get()
        self.assertIsNone(mark.completed_at)

    def test_marking_reactivates_a_completed_mark_with_its_note(self) -> None:
        completed = self.complete(notes="A 9.8 to replace mine")

        WantedEdition.objects.set_wanted(self.collector, self.edition, True)

        mark = WantedEdition.objects.get()
        self.assertEqual(mark.pk, completed.pk)
        self.assertIsNone(mark.completed_at)
        self.assertEqual(mark.notes, "A 9.8 to replace mine")
        self.assertEqual(mark.created_at, completed.created_at)

    def test_unmarking_deletes_an_active_mark(self) -> None:
        make_wanted_edition(collector=self.collector, edition=self.edition)

        WantedEdition.objects.set_wanted(self.collector, self.edition, False)

        self.assertFalse(WantedEdition.objects.exists())

    def test_unmarking_never_deletes_a_completed_mark(self) -> None:
        completed = self.complete(notes="Keep me")

        for _ in range(2):
            WantedEdition.objects.set_wanted(self.collector, self.edition, False)

        self.assertEqual(list(WantedEdition.objects.all()), [completed])
        self.assertEqual(WantedEdition.objects.get().notes, "Keep me")

    def test_resume_hunt_reactivates_a_completed_mark_and_is_idempotent(self) -> None:
        self.complete(notes="Signed copy")

        for _ in range(2):
            self.assertTrue(WantedEdition.objects.resume_hunt(self.collector, self.edition))

        mark = WantedEdition.objects.get()
        self.assertIsNone(mark.completed_at)
        self.assertEqual(mark.notes, "Signed copy")

    def test_resume_hunt_without_a_mark_changes_nothing(self) -> None:
        make_wanted_edition(collector=make_user(), edition=self.edition)  # someone else's

        self.assertFalse(WantedEdition.objects.resume_hunt(self.collector, self.edition))

        self.assertFalse(WantedEdition.objects.filter(collector=self.collector).exists())

    def test_resume_hunt_of_an_active_mark_keeps_it_active(self) -> None:
        make_wanted_edition(collector=self.collector, edition=self.edition, notes="Signed copy")

        self.assertTrue(WantedEdition.objects.resume_hunt(self.collector, self.edition))

        mark = WantedEdition.objects.get()
        self.assertIsNone(mark.completed_at)
        self.assertEqual(mark.notes, "Signed copy")

    def test_save_note_writes_only_the_note_of_an_active_mark(self) -> None:
        mark = make_wanted_edition(collector=self.collector, edition=self.edition, notes="Old")

        mark.notes = "New"
        self.assertTrue(mark.save_note())
        mark.notes = "New"  # an unchanged note still counts as saved
        self.assertTrue(mark.save_note())

        self.assertEqual(WantedEdition.objects.get().notes, "New")

    def test_save_note_does_not_undo_a_completion_made_meanwhile(self) -> None:
        mark = make_wanted_edition(collector=self.collector, edition=self.edition, notes="Old")
        make_collection(collector=self.collector, edition=self.edition)  # completes the stored row

        mark.notes = "New"  # `mark` still says active (completed_at None)
        self.assertFalse(mark.save_note())

        stored = WantedEdition.objects.get()
        self.assertIsNotNone(stored.completed_at)
        self.assertEqual(stored.notes, "Old")

    def test_complete_hunt_touches_only_an_active_mark(self) -> None:
        self.assertFalse(WantedEdition.objects.complete_hunt(self.collector.pk, self.edition.pk))

        make_wanted_edition(collector=self.collector, edition=self.edition)
        self.assertTrue(WantedEdition.objects.complete_hunt(self.collector.pk, self.edition.pk))
        self.assertFalse(WantedEdition.objects.complete_hunt(self.collector.pk, self.edition.pk))
