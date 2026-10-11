"""An artist cannot be deleted while an issue credits it as a creator or an edition as a cover
artist (IssueCreator / EditionCoverArtist protect it); unlinked artists can be. The relations keep
working as plain many-to-many relations, in the admin (as inlines) and in the history."""

from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from comics.history import versions_of
from comics.models import Artist, Edition, EditionCoverArtist, Issue, IssueCreator
from comics.versions import apply_changes, revert_to_version

from .base import ComiTestCase
from .factories import make_edition, make_superuser


class ArtistDeletionTests(ComiTestCase):
    def setUp(self) -> None:
        self.artist = Artist.objects.create(name="Todd McFarlane")
        self.edition = make_edition()
        self.issue = self.edition.issue

    def test_an_artist_credited_as_cover_artist_cannot_be_deleted(self) -> None:
        self.edition.cover_artists.add(self.artist)

        with self.assertRaises(ProtectedError):
            self.artist.delete()

        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())
        self.assertEqual(list(self.edition.cover_artists.all()), [self.artist])

    def test_an_artist_credited_as_creator_cannot_be_deleted(self) -> None:
        self.issue.creators.add(self.artist)

        with self.assertRaises(ProtectedError):
            Artist.objects.filter(pk=self.artist.pk).delete()

        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())
        self.assertEqual(list(self.issue.creators.all()), [self.artist])

    @skipUnless(connection.vendor == "mysql", "MySQL checks foreign keys immediately; SQLite defers them")
    def test_the_database_rejects_deleting_a_credited_artist(self) -> None:
        self.issue.creators.add(self.artist)
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {Artist._meta.db_table} WHERE id = %s", [self.artist.pk])
        self.assertEqual(list(self.issue.creators.all()), [self.artist])

    def test_an_unlinked_artist_can_be_deleted(self) -> None:
        self.artist.delete()
        self.assertFalse(Artist.objects.filter(pk=self.artist.pk).exists())

    def test_an_artist_can_be_deleted_once_its_credits_are_removed(self) -> None:
        self.edition.cover_artists.add(self.artist)
        self.issue.creators.add(self.artist)
        self.edition.cover_artists.remove(self.artist)
        self.issue.creators.clear()

        self.artist.delete()

        self.assertFalse(Artist.objects.filter(pk=self.artist.pk).exists())

    def test_deleting_the_edition_or_issue_removes_its_credits_only(self) -> None:
        self.edition.cover_artists.add(self.artist)
        self.issue.creators.add(self.artist)
        issue = self.issue

        self.edition.delete()
        issue.delete()

        self.assertFalse(EditionCoverArtist.objects.exists())
        self.assertFalse(IssueCreator.objects.exists())
        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())


class RelationManagerTests(ComiTestCase):
    """The explicit through models have no extra fields: the related managers work as before."""

    def setUp(self) -> None:
        self.artists = [Artist.objects.create(name=name) for name in ("Alan Moore", "Dave Gibbons", "Frank Miller")]
        self.edition = make_edition()
        self.issue = self.edition.issue

    def test_set_add_remove_and_clear(self) -> None:
        first, second, third = self.artists
        for relation in (self.edition.cover_artists, self.issue.creators):
            with self.subTest(relation=relation.prefetch_cache_name):
                relation.set([first, second])
                self.assertEqual(set(relation.all()), {first, second})
                relation.add(third, first)  # an artist already linked is not added twice
                self.assertEqual(relation.count(), 3)
                relation.remove(second)
                self.assertEqual(set(relation.all()), {first, third})
                relation.set([third])
                self.assertEqual(list(relation.all()), [third])
                relation.clear()
                self.assertEqual(relation.count(), 0)

    def test_the_reverse_relations(self) -> None:
        artist = self.artists[0]
        artist.covers.add(self.edition)
        artist.issues.add(self.issue)
        self.assertEqual(list(self.edition.cover_artists.all()), [artist])
        self.assertEqual(list(self.issue.creators.all()), [artist])
        self.assertEqual(list(Artist.objects.filter(cover_credits__edition=self.edition)), [artist])


class HistoryTests(ComiTestCase):
    def setUp(self) -> None:
        self.admin = make_superuser()
        self.first, self.second = Artist.objects.create(name="Jim Lee"), Artist.objects.create(name="Scott Williams")
        self.edition = make_edition()
        self.issue = self.edition.issue

    def test_cover_artists_are_versioned_and_restored(self) -> None:
        before = apply_changes(self.edition, {"cover_artists": [self.first]}, user=self.admin)
        apply_changes(self.edition, {"cover_artists": [self.second]}, user=self.admin)

        self.assertEqual(list(before.cover_artists.values_list("artist_id", flat=True)), [self.first.pk])
        revert_to_version(before, user=self.admin)

        self.assertEqual(list(Edition.objects.get(pk=self.edition.pk).cover_artists.all()), [self.first])

    def test_creators_are_versioned_and_restored(self) -> None:
        before = apply_changes(self.issue, {"creators": [self.first, self.second]}, user=self.admin)
        apply_changes(self.issue, {"creators": []}, user=self.admin)

        self.assertEqual(
            set(before.creators.values_list("artist_id", flat=True)), {self.first.pk, self.second.pk}
        )
        revert_to_version(before, user=self.admin)

        self.assertEqual(set(Issue.objects.get(pk=self.issue.pk).creators.all()), {self.first, self.second})

    def test_a_relation_change_outside_a_batch_writes_a_version(self) -> None:
        count = versions_of(Issue, self.issue.pk).count()
        self.issue.creators.add(self.first)
        latest = versions_of(Issue, self.issue.pk).first()
        self.assertEqual(versions_of(Issue, self.issue.pk).count(), count + 1)
        self.assertEqual(list(latest.creators.values_list("artist_id", flat=True)), [self.first.pk])


class AdminTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_superuser())
        self.artist = Artist.objects.create(name="Greg Capullo")
        self.edition = make_edition()
        self.issue = self.edition.issue

    def test_deleting_a_credited_artist_shows_the_protected_objects(self) -> None:
        self.edition.cover_artists.add(self.artist)
        self.issue.creators.add(self.artist)
        url = reverse("admin:comics_artist_delete", args=[self.artist.pk])

        response = self.client.post(url, {"post": "yes"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "would require deleting the following protected related objects")
        self.assertEqual(len(response.context["protected"]), 2)
        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())
        self.assertEqual(list(self.edition.cover_artists.all()), [self.artist])

    def test_bulk_deleting_a_credited_artist_shows_the_protected_objects(self) -> None:
        self.issue.creators.add(self.artist)
        data = {"action": "delete_selected", "_selected_action": [self.artist.pk], "post": "yes"}

        response = self.client.post(reverse("admin:comics_artist_changelist"), data)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())

    def test_an_unlinked_artist_is_deleted(self) -> None:
        response = self.client.post(reverse("admin:comics_artist_delete", args=[self.artist.pk]), {"post": "yes"})

        self.assertRedirects(response, reverse("admin:comics_artist_changelist"))
        self.assertFalse(Artist.objects.filter(pk=self.artist.pk).exists())

    def test_creators_are_edited_as_inline_rows(self) -> None:
        url = reverse("admin:comics_issue_change", args=[self.issue.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "publishing": self.issue.publishing_id,
            "number": self.issue.number,
            "synopsis": "",
            "creator_entries-TOTAL_FORMS": "1",
            "creator_entries-INITIAL_FORMS": "0",
            "creator_entries-0-artist": self.artist.pk,
            "editions-TOTAL_FORMS": "1",
            "editions-INITIAL_FORMS": "1",
            "editions-0-id": self.edition.pk,
            "editions-0-issue": self.issue.pk,
        }

        response = self.client.post(url, data)

        self.assertRedirects(response, reverse("admin:comics_issue_changelist"))
        self.assertEqual(list(self.issue.creators.all()), [self.artist])
        latest = versions_of(Issue, self.issue.pk).first()
        self.assertEqual(list(latest.creators.values_list("artist_id", flat=True)), [self.artist.pk])

    def test_the_edition_change_page_shows_its_cover_artists(self) -> None:
        self.edition.cover_artists.add(self.artist)

        response = self.client.get(reverse("admin:comics_edition_change", args=[self.edition.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="cover_artist_entries-TOTAL_FORMS"')
        self.assertContains(response, "Greg Capullo")
