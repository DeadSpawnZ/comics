"""Versions written when the rows of a versioned many-to-many relation (IssueCreator,
EditionCoverArtist, CollectedIssue) change on their own, and the version counts of the app's own
paths, which must not get duplicates from them (see "Relation rows changed on their own" in
comics.history)."""

from typing import Any

from django.db import connection
from django.db.models.signals import m2m_changed
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.change_requests import approve, encode_value, submit, target_label
from comics.history import CHANGED, DELETED, history_batch, versions_of
from comics.models import Artist, CollectedIssue, Edition, EditionCoverArtist, Issue, IssueCreator
from comics.roles import ADMINISTRATORS
from comics.versions import apply_changes, revert_to_version

from .base import ComiTestCase
from .factories import make_edition, make_publishing, make_superuser, make_user
from .test_versions import gestion_data


def count(model: type[Issue] | type[Edition], pk: int) -> int:
    return versions_of(model, pk).count()


def latest(model: type[Issue] | type[Edition], pk: int) -> Any:
    return versions_of(model, pk).first()


def stored_creators(version: Any) -> list[int]:
    return sorted(version.creators.values_list("artist_id", flat=True))


def stored_cover_artists(version: Any) -> list[int]:
    return sorted(version.cover_artists.values_list("artist_id", flat=True))


class RelationRowTestCase(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS, username="manager")
        self.artists = [Artist.objects.create(name=name) for name in ("Ann", "Bob", "Cid")]
        self.issue = Issue.objects.create(publishing=make_publishing(), number="1")
        self.edition = make_edition()


class DirectRowChangeTests(RelationRowTestCase):
    """Rows saved or deleted through the through model, outside any batch (a script)."""

    def test_creating_a_credit_writes_one_version_of_its_issue(self) -> None:
        IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        self.assertEqual(count(Issue, self.issue.pk), 2)
        version = latest(Issue, self.issue.pk)
        self.assertEqual((version.history_type, stored_creators(version)), (CHANGED, [self.artists[0].pk]))

    def test_creating_a_cover_credit_writes_one_version_of_its_edition(self) -> None:
        EditionCoverArtist.objects.create(edition=self.edition, artist=self.artists[0])
        self.assertEqual(count(Edition, self.edition.pk), 2)
        self.assertEqual(stored_cover_artists(latest(Edition, self.edition.pk)), [self.artists[0].pk])

    def test_the_rows_user_and_reason_go_to_the_version(self) -> None:
        credit = IssueCreator(issue=self.issue, artist=self.artists[0])
        credit._history_user = self.manager
        credit._change_reason = "Credit from the indicia"
        credit.save()
        version = latest(Issue, self.issue.pk)
        self.assertEqual(
            (version.history_user, version.history_change_reason), (self.manager, "Credit from the indicia")
        )

    def test_deleting_one_credit(self) -> None:
        self.issue.creators.add(*self.artists[:2])
        before = count(Issue, self.issue.pk)
        IssueCreator.objects.get(issue=self.issue, artist=self.artists[0]).delete()
        self.assertEqual(count(Issue, self.issue.pk), before + 1)
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [self.artists[1].pk])

        EditionCoverArtist.objects.create(edition=self.edition, artist=self.artists[0])
        before = count(Edition, self.edition.pk)
        EditionCoverArtist.objects.get(edition=self.edition).delete()
        self.assertEqual(count(Edition, self.edition.pk), before + 1)
        self.assertEqual(stored_cover_artists(latest(Edition, self.edition.pk)), [])

    def test_a_queryset_delete_writes_one_version_per_owner(self) -> None:
        other = Issue.objects.create(publishing=self.issue.publishing, number="2")
        self.issue.creators.add(*self.artists)
        other.creators.add(self.artists[0])
        before = (count(Issue, self.issue.pk), count(Issue, other.pk))
        # Two rows of self.issue, one of other.
        IssueCreator.objects.filter(artist__in=self.artists[:2]).delete()
        self.assertEqual((count(Issue, self.issue.pk), count(Issue, other.pk)), (before[0] + 1, before[1] + 1))
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [self.artists[2].pk])
        self.assertEqual(stored_creators(latest(Issue, other.pk)), [])

        # The next deletion of the same issue's rows is a new operation, with its own version.
        IssueCreator.objects.filter(issue=self.issue).delete()
        self.assertEqual(count(Issue, self.issue.pk), before[0] + 2)

    def test_deleting_the_same_row_object_again_after_saving_it(self) -> None:
        credit = IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        credit.delete()
        credit.save()  # inserted again (the deletion cleared its id)
        credit.delete()
        # Created, then: added, deleted, added, deleted.
        self.assertEqual(count(Issue, self.issue.pk), 5)

    def test_unlinking_an_artist_from_everything(self) -> None:
        # The natural way to free an artist for deletion (the credits PROTECT it).
        artist = self.artists[0]
        self.issue.creators.add(artist)
        self.edition.cover_artists.add(artist)
        before = (count(Issue, self.issue.pk), count(Edition, self.edition.pk))
        IssueCreator.objects.filter(artist=artist).delete()
        EditionCoverArtist.objects.filter(artist=artist).delete()
        artist.delete()
        self.assertEqual((count(Issue, self.issue.pk), count(Edition, self.edition.pk)), (before[0] + 1, before[1] + 1))

    def test_moving_a_credit_to_another_issue_versions_both(self) -> None:
        other = Issue.objects.create(publishing=self.issue.publishing, number="2")
        credit = IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        before = (count(Issue, self.issue.pk), count(Issue, other.pk))
        credit = IssueCreator.objects.get(pk=credit.pk)
        credit.issue = other
        credit.save()
        self.assertEqual((count(Issue, self.issue.pk), count(Issue, other.pk)), (before[0] + 1, before[1] + 1))
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [])
        self.assertEqual(stored_creators(latest(Issue, other.pk)), [self.artists[0].pk])

    def test_changing_the_artist_of_a_credit(self) -> None:
        credit = IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        before = count(Issue, self.issue.pk)
        credit.artist = self.artists[1]
        credit.save()
        self.assertEqual(count(Issue, self.issue.pk), before + 1)
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [self.artists[1].pk])

    def test_collected_issues_deleted_together_write_one_version(self) -> None:
        compilation = make_edition()
        for order, edition in enumerate((make_edition(), make_edition()), start=1):
            CollectedIssue.objects.create(edition=compilation, issue=edition.issue, order=order)
        before = count(Edition, compilation.pk)
        compilation.collected_entries.all().delete()
        self.assertEqual(count(Edition, compilation.pk), before + 1)

    def test_inside_a_batch_the_rows_are_coalesced(self) -> None:
        with history_batch(user=self.manager, reason="Credits") as batch:
            IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
            IssueCreator.objects.create(issue=self.issue, artist=self.artists[1])
            IssueCreator.objects.filter(artist=self.artists[0]).delete()
        self.assertEqual(count(Issue, self.issue.pk), 2)
        version = batch.version_of(self.issue)
        self.assertEqual((version.history_user, version.history_change_reason), (self.manager, "Credits"))
        self.assertEqual(stored_creators(version), [self.artists[1].pk])

    def test_loaddata_rows_write_no_version(self) -> None:
        credit = IssueCreator(issue=self.issue, artist=self.artists[0])
        credit.save_base(raw=True)
        self.assertEqual(count(Issue, self.issue.pk), 1)


class RelatedManagerTests(RelationRowTestCase):
    """add/remove/set/clear outside a batch: one version per call, as before the row receivers."""

    def test_forward_side(self) -> None:
        calls = [
            lambda: self.issue.creators.add(*self.artists[:2]),
            lambda: self.issue.creators.remove(self.artists[0]),
            lambda: self.issue.creators.remove(*self.artists[1:]),  # one row there, one not
            lambda: self.issue.creators.add(*self.artists),
            lambda: self.issue.creators.clear(),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                before = count(Issue, self.issue.pk)
                call()
                self.assertEqual(count(Issue, self.issue.pk), before + 1)
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [])

    def test_set_is_a_removal_and_an_addition(self) -> None:
        self.edition.cover_artists.add(self.artists[0])
        before = count(Edition, self.edition.pk)
        self.edition.cover_artists.set([self.artists[1]])
        self.assertEqual(count(Edition, self.edition.pk), before + 2)
        self.assertEqual(stored_cover_artists(latest(Edition, self.edition.pk)), [self.artists[1].pk])

    def test_the_instances_user_and_reason_are_kept(self) -> None:
        self.issue.creators.add(self.artists[0])
        self.issue._history_user = self.manager
        self.issue._change_reason = "Not in this one"
        self.issue.creators.remove(self.artists[0])
        version = latest(Issue, self.issue.pk)
        self.assertEqual((version.history_user, version.history_change_reason), (self.manager, "Not in this one"))

    def test_reverse_side(self) -> None:
        artist = self.artists[0]
        other = make_edition()
        artist.covers.add(self.edition, other)
        before = (count(Edition, self.edition.pk), count(Edition, other.pk))
        artist.covers.remove(self.edition)
        self.assertEqual(count(Edition, self.edition.pk), before[0] + 1)
        artist.covers.clear()
        self.assertEqual((count(Edition, self.edition.pk), count(Edition, other.pk)), (before[0] + 1, before[1] + 1))

        artist.issues.add(self.issue)
        before = count(Issue, self.issue.pk)
        artist.issues.remove(self.issue)
        self.assertEqual(count(Issue, self.issue.pk), before + 1)

    def test_removing_collected_issues(self) -> None:
        collected = make_edition().issue
        CollectedIssue.objects.create(edition=self.edition, issue=collected, order=1)
        before = count(Edition, self.edition.pk)
        self.edition.collected_issues.remove(collected)
        self.assertEqual(count(Edition, self.edition.pk), before + 1)

    def test_inside_a_batch(self) -> None:
        with history_batch(user=self.manager):
            self.issue.creators.add(*self.artists)
            self.issue.creators.remove(self.artists[0])
            self.issue.creators.set([self.artists[2]])
        self.assertEqual(count(Issue, self.issue.pk), 2)
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [self.artists[2].pk])

    def test_a_failed_removal_does_not_silence_later_deletions(self) -> None:
        IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        # A remove() that started and failed before its post_remove (e.g. a database error).
        pk_set = {self.artists[0].pk}
        m2m_changed.send(
            sender=IssueCreator, action="pre_remove", instance=self.issue, reverse=False, model=Artist, pk_set=pk_set
        )
        # Its rows stay marked until a batch ends (every request is one).
        with history_batch():
            pass
        before = count(Issue, self.issue.pk)
        IssueCreator.objects.filter(issue=self.issue).delete()
        self.assertEqual(count(Issue, self.issue.pk), before + 1)


class AppPathTests(RelationRowTestCase):
    """The app's own versioned paths write the same versions as before: one per change."""

    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.manager)

    def test_gestion_edition_and_issue_save(self) -> None:
        issue = self.edition.issue
        issue.creators.add(self.artists[0])
        self.edition.cover_artists.add(self.artists[0])
        before = (count(Edition, self.edition.pk), count(Issue, issue.pk))
        data = gestion_data(
            self.edition,
            cover_artists=[self.artists[1].pk, self.artists[2].pk],
            **{"issue_data-shown": issue.pk, "issue_data-creators": [self.artists[1].pk]},
        )
        response = self.client.post(reverse("manage_edition_edit", args=[self.edition.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual((count(Edition, self.edition.pk), count(Issue, issue.pk)), (before[0] + 1, before[1] + 1))
        self.assertEqual(stored_creators(latest(Issue, issue.pk)), [self.artists[1].pk])
        self.assertEqual(stored_cover_artists(latest(Edition, self.edition.pk)), [a.pk for a in self.artists[1:]])

    def test_gestion_new_edition_with_imported_credits(self) -> None:
        # What a catalogue import submits: the new edition with its cover artists and the issue's creators.
        publishing = make_publishing(publishing_title="Imported")
        data = gestion_data(
            self.edition,
            publishing=publishing.pk,
            number="7",
            cover_artists=[self.artists[0].pk],
            **{"issue_data-creators": [self.artists[1].pk, self.artists[2].pk]},
        )
        response = self.client.post(reverse("manage_edition_new"), data)
        self.assertEqual(response.status_code, 302)
        edition = Edition.objects.get(publishing=publishing, number="7")
        self.assertEqual((count(Edition, edition.pk), count(Issue, edition.issue_id)), (1, 1))
        self.assertEqual(stored_creators(latest(Issue, edition.issue_id)), [a.pk for a in self.artists[1:]])

    def test_apply_changes_and_revert(self) -> None:
        self.issue.creators.add(self.artists[0])
        before = count(Issue, self.issue.pk)
        first = latest(Issue, self.issue.pk)
        apply_changes(self.issue, {"creators": [self.artists[1], self.artists[2]]}, user=self.manager)
        self.assertEqual(count(Issue, self.issue.pk), before + 1)
        revert_to_version(first, user=self.manager)
        self.assertEqual(count(Issue, self.issue.pk), before + 2)
        self.assertEqual(stored_creators(latest(Issue, self.issue.pk)), [self.artists[0].pk])

        apply_changes(self.edition, {"cover_artists": [self.artists[0]]}, user=self.manager)
        apply_changes(self.edition, {"cover_artists": []}, user=self.manager)
        self.assertEqual(count(Edition, self.edition.pk), 3)

    def test_change_request_approval(self) -> None:
        field = Edition._meta.get_field("cover_artists")
        change_request = submit(
            author=make_user(username="author"),
            model=Edition,
            changes={"cover_artists": encode_value(field, [self.artists[0]])},
            label=target_label(self.edition),
            reason="Signed cover",
            target=self.edition,
        )
        approve(change_request, user=self.manager)
        self.assertEqual(count(Edition, self.edition.pk), 2)
        self.assertEqual(stored_cover_artists(latest(Edition, self.edition.pk)), [self.artists[0].pk])

    def test_admin_inline_rows(self) -> None:
        superuser = make_superuser(username="root")
        self.client.force_login(superuser)
        credit = IssueCreator.objects.create(issue=self.issue, artist=self.artists[0])
        before = count(Issue, self.issue.pk)
        data = {
            "publishing": self.issue.publishing_id,
            "number": self.issue.number,
            "synopsis": "",
            # Delete the existing credit and add another one.
            "creator_entries-TOTAL_FORMS": "2",
            "creator_entries-INITIAL_FORMS": "1",
            "creator_entries-0-id": credit.pk,
            "creator_entries-0-issue": self.issue.pk,
            "creator_entries-0-artist": self.artists[0].pk,
            "creator_entries-0-DELETE": "on",
            "creator_entries-1-issue": self.issue.pk,
            "creator_entries-1-artist": self.artists[1].pk,
            "editions-TOTAL_FORMS": "0",
            "editions-INITIAL_FORMS": "0",
            "editions-MIN_NUM_FORMS": "0",
            "editions-MAX_NUM_FORMS": "1000",
        }
        response = self.client.post(reverse("admin:comics_issue_change", args=[self.issue.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(count(Issue, self.issue.pk), before + 1)
        version = latest(Issue, self.issue.pk)
        self.assertEqual((version.history_user, stored_creators(version)), (superuser, [self.artists[1].pk]))


class OwnerDeletionTests(RelationRowTestCase):
    """Deleting an issue or edition takes its rows with it: only its "deleted" version is written."""

    def test_deleting_an_issue_with_credits(self) -> None:
        self.issue.creators.add(*self.artists)
        before = count(Issue, self.issue.pk)
        pk = self.issue.pk
        with CaptureQueriesContext(connection) as queries:
            self.issue.delete()
        self.assertFalse(IssueCreator.objects.filter(issue_id=pk).exists())
        self.assertEqual(count(Issue, pk), before + 1)
        self.assertEqual(latest(Issue, pk).history_type, DELETED)
        # The credits are no longer fast-deleted: read once and deleted by id, whatever their number.
        self.assertLess(len(queries), 20)

    def test_deleting_an_edition_with_cover_artists_and_collected_issues(self) -> None:
        self.edition.cover_artists.add(*self.artists)
        compilation = make_edition()
        compilation.cover_artists.add(self.artists[0])
        CollectedIssue.objects.create(edition=compilation, issue=self.edition.issue, order=1)
        pk = compilation.pk
        before = count(Edition, pk)
        compilation.delete()
        self.assertEqual(count(Edition, pk), before + 1)
        self.assertEqual(latest(Edition, pk).history_type, DELETED)

    def test_deleting_issues_with_a_queryset(self) -> None:
        other = Issue.objects.create(publishing=self.issue.publishing, number="2")
        self.issue.creators.add(self.artists[0])
        other.creators.add(self.artists[0], self.artists[1])
        before = {issue.pk: count(Issue, issue.pk) for issue in (self.issue, other)}
        Issue.objects.filter(pk__in=before).delete()
        for pk, versions in before.items():
            self.assertEqual(count(Issue, pk), versions + 1)
            self.assertEqual(latest(Issue, pk).history_type, DELETED)

    def test_deleting_inside_a_batch(self) -> None:
        self.edition.cover_artists.add(self.artists[0])
        before = count(Edition, self.edition.pk)
        pk = self.edition.pk
        with history_batch(user=self.manager):
            self.edition.delete()
        self.assertEqual(count(Edition, pk), before + 1)
        self.assertEqual((latest(Edition, pk).history_type, latest(Edition, pk).history_user), (DELETED, self.manager))

    def test_gestion_delete(self) -> None:
        self.client.force_login(self.manager)
        self.edition.cover_artists.add(self.artists[0])
        self.edition.issue.creators.add(self.artists[1])
        issue_id = self.edition.issue_id
        before = (count(Edition, self.edition.pk), count(Issue, issue_id))
        self.client.post(reverse("manage_edition_delete", args=[self.edition.pk]))
        self.assertFalse(Edition.objects.filter(pk=self.edition.pk).exists())
        # The orphaned issue goes too: one "deleted" version each.
        self.assertEqual((count(Edition, self.edition.pk), count(Issue, issue_id)), (before[0] + 1, before[1] + 1))
        self.assertEqual(latest(Issue, issue_id).history_type, DELETED)
