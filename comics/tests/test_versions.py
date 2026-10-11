"""Versioned history of issues and editions (comics.history, comics.versions) and its Gestión pages."""

import importlib
import json
from pathlib import Path
from typing import Any
from unittest import mock

from django.apps import apps
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.history import CHANGED, CREATED, DELETED, history_batch, relation_rows, versions_of
from comics.models import Artist, CollectedIssue, Edition, Issue
from comics.roles import ADMINISTRATORS
from comics.versions import apply_changes, revert_to_version, version_entries

from .base import ComiTestCase
from .factories import image_upload, make_edition, make_publishing, make_superuser, make_user

initial_versions = importlib.import_module("comics.migrations.0108_initial_versions")


def edition_versions(edition: Edition | int) -> list[Any]:
    """Versions of an edition, newest first."""
    return list(versions_of(Edition, getattr(edition, "pk", edition)))


def state(edition: Edition) -> dict[str, Any]:
    """What a version of an edition restores: fields and relations, as they are in the database."""
    edition = Edition.objects.get(pk=edition.pk)
    return {
        "number": edition.number,
        "variant": edition.variant,
        "rarity": edition.rarity,
        "notes": edition.notes,
        "publishing_id": edition.publishing_id,
        "issue_id": edition.issue_id,
        "image": edition.image.name or "",
        "thumbnail": edition.thumbnail.name or "",
        "relations": relation_rows(edition),
    }


def gestion_data(edition: Edition, **changes: Any) -> dict[str, Any]:
    """The Gestión edition form as it is submitted for `edition`, with `changes`."""
    data = {
        "publishing": edition.publishing_id,
        "number": edition.number,
        "variant": edition.variant,
        "printing": edition.printing,
        "format": edition.format,
        "rarity": edition.rarity,
        "release_date": edition.release_date.isoformat(),
        "cover_price": "0",
        "notes": edition.notes,
        "content": "single",
        "cover_artists": [artist.pk for artist in edition.cover_artists.all()],
    }
    data.update(changes)
    return data


class RecordingTests(ComiTestCase):
    """Which versions are written, by whom, from each place an edition or issue can change."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS, username="manager")
        self.client.force_login(self.manager)
        self.artist = Artist.objects.create(name="Todd McFarlane")

    def test_creating_editing_and_deleting_in_gestion(self) -> None:
        publishing = make_publishing()
        data = gestion_data(make_edition(publishing=publishing, number="90"), number="1", variant="A")
        data["cover_artists"] = [self.artist.pk]
        response = self.client.post(reverse("manage_edition_new"), data)
        self.assertEqual(response.status_code, 302)
        edition = Edition.objects.get(publishing=publishing, number="1")

        # One version per request, with the relations set after the save (form.save_m2m()).
        (created,) = edition_versions(edition)
        self.assertEqual((created.history_type, created.history_user), (CREATED, self.manager))
        self.assertEqual(list(created.cover_artists.values_list("artist_id", flat=True)), [self.artist.pk])
        self.assertEqual(list(versions_of(Issue, edition.issue_id).values_list("history_type", flat=True)), [CREATED])

        self.client.post(reverse("manage_edition_edit", args=[edition.pk]), gestion_data(edition, notes="Signed"))
        changed = edition_versions(edition)[0]
        self.assertEqual((changed.history_type, changed.notes, changed.history_user), (CHANGED, "Signed", self.manager))

        # Saving without changes writes no version.
        edition = Edition.objects.get(pk=edition.pk)
        self.client.post(reverse("manage_edition_edit", args=[edition.pk]), gestion_data(edition))
        self.assertEqual(len(edition_versions(edition)), 2)

        issue_id = edition.issue_id
        self.client.post(reverse("manage_edition_delete", args=[edition.pk]))
        self.assertFalse(Edition.objects.filter(pk=edition.pk).exists())
        deleted = edition_versions(edition)[0]
        self.assertEqual((deleted.history_type, deleted.history_user), (DELETED, self.manager))
        # Its issue, left orphaned, was deleted too.
        self.assertEqual(versions_of(Issue, issue_id).first().history_type, DELETED)

    def test_turning_an_edition_into_a_compilation_is_one_version(self) -> None:
        edition = make_edition()
        collected = [make_edition().issue, make_edition().issue]
        data = gestion_data(edition, content="compilation", collected=json.dumps([issue.pk for issue in collected]))
        self.client.post(reverse("manage_edition_edit", args=[edition.pk]), data)
        latest = edition_versions(edition)[0]
        self.assertEqual(len(edition_versions(edition)), 2)  # created by the fixture, then this request
        # Includes what sync_compilation_state() changed with QuerySet.update().
        self.assertIsNone(latest.issue_id)
        stored = latest.collected_issues.order_by("order").values_list("issue_id", "order")
        self.assertEqual(list(stored), [(collected[0].pk, 1), (collected[1].pk, 2)])

    def test_admin_changes_are_versioned_by_the_superuser(self) -> None:
        superuser = make_superuser(username="root")
        self.client.force_login(superuser)
        issue = Issue.objects.create(publishing=make_publishing(), number="7")
        data = {
            "publishing": issue.publishing_id,
            "number": "7",
            "synopsis": "Origin story",
            "creators": [self.artist.pk],
            "editions-TOTAL_FORMS": "0",
            "editions-INITIAL_FORMS": "0",
            "editions-MIN_NUM_FORMS": "0",
            "editions-MAX_NUM_FORMS": "1000",
        }
        response = self.client.post(reverse("admin:comics_issue_change", args=[issue.pk]), data)
        self.assertEqual(response.status_code, 302)
        latest = versions_of(Issue, issue.pk).first()
        self.assertEqual((latest.history_user, latest.synopsis), (superuser, "Origin story"))
        self.assertEqual(list(latest.creators.values_list("artist_id", flat=True)), [self.artist.pk])
        self.assertEqual(versions_of(Issue, issue.pk).count(), 2)

        edition = make_edition()
        self.client.post(reverse("admin:comics_edition_delete", args=[edition.pk]), {"post": "yes"})
        self.assertEqual(edition_versions(edition)[0].history_type, DELETED)
        self.assertEqual(edition_versions(edition)[0].history_user, superuser)

    def test_scripts_set_the_user_and_reason_on_the_instance(self) -> None:
        edition = make_edition()
        edition._history_user = self.manager
        edition._change_reason = "Fix typo"
        edition.notes = "Fixed"
        edition.save()
        latest = edition_versions(edition)[0]
        self.assertEqual((latest.history_user, latest.history_change_reason), (self.manager, "Fix typo"))

        # Changed from the other side of the relation (artist.covers): the edition gets the version.
        self.artist.covers.add(edition)
        self.assertEqual(list(edition_versions(edition)[0].cover_artists.values_list("artist_id", flat=True)), [self.artist.pk])

    def test_a_batch_coalesces_the_changes_of_a_row(self) -> None:
        edition = make_edition()
        collected = make_edition().issue
        with history_batch(user=self.manager, reason="Bulk fix") as batch:
            edition.notes = "One"
            edition.save()
            edition.cover_artists.add(self.artist)
            CollectedIssue.objects.create(edition=edition, issue=collected, order=1)
            edition.sync_compilation_state()
        version = batch.version_of(edition)
        self.assertEqual(edition_versions(edition)[0], version)
        self.assertEqual(len(edition_versions(edition)), 2)
        self.assertEqual((version.notes, version.issue_id, version.history_change_reason), ("One", None, "Bulk fix"))
        self.assertEqual(version.collected_issues.count(), 1)

    def test_an_empty_batch_runs_no_queries(self) -> None:
        with self.assertNumQueries(0), history_batch(user=self.manager):
            pass

    def test_a_failed_batch_writes_nothing(self) -> None:
        edition = make_edition()
        with self.assertRaises(RuntimeError), history_batch(user=self.manager):
            edition.notes = "Lost"
            edition.save()
            raise RuntimeError
        self.assertEqual(len(edition_versions(edition)), 1)


class RevertTests(ComiTestCase):
    def setUp(self) -> None:
        self.users = [make_user(username=f"user{i}") for i in range(1, 4)]
        self.manager = make_user(role=ADMINISTRATORS, username="manager")
        self.edition = make_edition(notes="original")

    def test_going_back_to_the_state_after_each_of_three_changes(self) -> None:
        artists = [Artist.objects.create(name=f"Artist {i}") for i in range(3)]
        targets = []
        for user, rarity, artist in zip(self.users, ["rare", "very_rare", "legendary"], artists, strict=True):
            version = apply_changes(
                self.edition, {"rarity": rarity, "notes": f"by {user}", "cover_artists": [artist]}, user=user
            )
            self.assertEqual(version.history_user, user)
            targets.append((version, state(self.edition)))

        for target, expected in [targets[0], targets[2], targets[1]]:
            with self.subTest(target=target.notes):
                count = len(edition_versions(self.edition))
                reverted = revert_to_version(target, user=self.manager, reason="Back")
                self.assertEqual(state(self.edition), expected)
                self.assertEqual(len(edition_versions(self.edition)), count + 1)
                self.assertEqual(edition_versions(self.edition)[0], reverted)
                self.assertEqual(
                    (reverted.reverted_to, reverted.history_user, reverted.history_change_reason, reverted.history_type),
                    (target, self.manager, "Back", CHANGED),
                )

        # Undo the last revert by reverting to the version before it.
        before_last = edition_versions(self.edition)[1]
        revert_to_version(before_last, user=self.manager)
        self.assertEqual(state(self.edition), targets[2][1])
        # A revert is a version like any other: going back to one works.
        revert_to_version(edition_versions(self.edition)[1], user=self.manager)
        self.assertEqual(state(self.edition), targets[1][1])

    def test_relations_are_restored(self) -> None:
        first, second, third = (make_edition().issue for _ in range(3))
        artist = Artist.objects.create(name="Greg Capullo")
        compilation = apply_changes(
            self.edition, {"collected_issues": [first, second], "cover_artists": [artist]}, user=self.users[0]
        )
        self.assertIsNone(Edition.objects.get(pk=self.edition.pk).issue_id)
        reordered = apply_changes(self.edition, {"collected_issues": [third, first]}, user=self.users[1])
        single = apply_changes(self.edition, {"collected_issues": [], "cover_artists": []}, user=self.users[2])
        own_issue_id = Edition.objects.get(pk=self.edition.pk).issue_id
        self.assertIsNotNone(own_issue_id)
        self.assertEqual(single.collected_issues.count(), 0)

        revert_to_version(compilation, user=self.manager)
        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertIsNone(edition.issue_id)
        self.assertEqual(list(edition.collected_entries.values_list("issue_id", "order")), [(first.pk, 1), (second.pk, 2)])
        self.assertEqual(list(edition.cover_artists.all()), [artist])

        revert_to_version(reordered, user=self.manager)
        self.assertEqual(list(edition.collected_entries.values_list("issue_id", "order")), [(third.pk, 1), (first.pk, 2)])

        revert_to_version(single, user=self.manager)
        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertFalse(edition.collected_entries.exists())
        self.assertEqual(edition.issue_id, own_issue_id)

    def test_a_deleted_issue_is_restored_with_its_id(self) -> None:
        issue = self.edition.issue
        issue.synopsis = "The story"
        issue.save()
        before = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"number": "999"}, user=self.users[0])  # its old issue is left orphaned
        self.assertFalse(Issue.objects.filter(pk=issue.pk).exists())

        revert_to_version(before, user=self.manager)
        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertEqual((edition.number, edition.issue_id), (before.number, issue.pk))
        self.assertEqual(Issue.objects.get(pk=issue.pk).synopsis, "The story")
        restored = versions_of(Issue, issue.pk).first()
        self.assertEqual((restored.history_type, restored.history_user), (CREATED, self.manager))
        self.assertIsNotNone(restored.reverted_to)
        # The issue of number 999 was left orphaned in turn.
        self.assertFalse(Issue.objects.filter(publishing=edition.publishing, number="999").exists())

    def test_a_deleted_edition_can_be_restored(self) -> None:
        last = edition_versions(self.edition)[0]
        pk = self.edition.pk
        self.edition.delete()
        revert_to_version(last, user=self.manager)
        self.assertEqual(Edition.objects.get(pk=pk).notes, "original")
        with self.assertRaisesMessage(ValidationError, "A deleted state cannot be restored"):
            revert_to_version(versions_of(Edition, pk).filter(history_type=DELETED).get(), user=self.manager)

    def test_refused_when_a_related_row_is_gone(self) -> None:
        artist = Artist.objects.create(name="Gone")
        with_artist = apply_changes(self.edition, {"cover_artists": [artist]}, user=self.users[0])
        apply_changes(self.edition, {"cover_artists": []}, user=self.users[1])
        artist.delete()
        current = state(self.edition)
        with self.assertRaisesMessage(ValidationError, "its Cover artists (#"):
            revert_to_version(with_artist, user=self.manager)
        self.assertEqual(state(self.edition), current)

        old_publishing = self.edition.publishing
        on_old_publishing = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"publishing": make_publishing()}, user=self.users[2])
        Issue.objects.filter(publishing=old_publishing).delete()
        old_publishing.delete()
        with self.assertRaisesMessage(ValidationError, "its Publishing (#"):
            revert_to_version(on_old_publishing, user=self.manager)

    def test_refused_when_another_edition_took_its_place(self) -> None:
        before = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"variant": "B"}, user=self.users[0])
        make_edition(publishing=self.edition.publishing, number=self.edition.number, variant="A")
        versions = len(edition_versions(self.edition))
        with self.assertRaisesMessage(ValidationError, "This version cannot be restored"):
            revert_to_version(before, user=self.manager)
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).variant, "B")
        self.assertEqual(len(edition_versions(self.edition)), versions)

    def test_reverting_the_rarity_reevaluates_the_owners(self) -> None:
        before = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"rarity": "legendary"}, user=self.users[0])
        with mock.patch("comics.signals.schedule_edition_owners") as schedule:
            revert_to_version(before, user=self.manager)
        schedule.assert_called_once_with(self.edition.pk)

    def test_apply_changes_validates_and_changes_nothing_on_error(self) -> None:
        with self.assertRaises(ValidationError):
            apply_changes(self.edition, {"ratio": "not a ratio"}, user=self.users[0])
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).ratio, "")
        self.assertEqual(len(edition_versions(self.edition)), 1)

    def test_apply_changes_always_writes_a_version(self) -> None:
        version = apply_changes(self.edition, {"notes": "original"}, user=self.users[0], reason="Request #1")
        self.assertEqual((version.history_user, version.history_change_reason), (self.users[0], "Request #1"))
        self.assertEqual(len(edition_versions(self.edition)), 2)


class CoverFileTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)

    def upload_cover(self, edition: Edition, name: str) -> Any:
        data = gestion_data(edition, image=image_upload(name))
        self.client.post(reverse("manage_edition_edit", args=[edition.pk]), data)
        return edition_versions(edition)[0]

    def test_reverting_keeps_the_stored_files(self) -> None:
        edition = make_edition()
        first = self.upload_cover(edition, "first.png")
        second = self.upload_cover(edition, "second.png")
        self.assertNotEqual(first.image, second.image)
        media = Path(default_storage.location)
        files_before = set(media.rglob("*"))
        # Replacing the cover deletes nothing: the first version's files are still there.
        for path in [first.image, first.thumbnail, second.image, second.thumbnail]:
            self.assertTrue(default_storage.exists(path), path)

        self.client.post(reverse("manage_edition_revert", args=[edition.pk, first.history_id]))
        edition = Edition.objects.get(pk=edition.pk)
        self.assertEqual((edition.image.name, edition.thumbnail.name), (first.image, first.thumbnail))
        # Restored as stored: not processed again into new files, and nothing deleted.
        self.assertEqual(set(media.rglob("*")), files_before)


class HistoryPageTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS, username="manager")
        self.edition = make_edition(notes="first")
        self.url = reverse("manage_edition_history", args=[self.edition.pk])

    def test_access(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 302)  # to the login
        self.client.force_login(make_user(username="collector"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        revert_url = reverse("manage_edition_revert", args=[self.edition.pk, edition_versions(self.edition)[0].history_id])
        self.assertEqual(self.client.post(revert_url).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(revert_url).status_code, 405)
        self.assertEqual(self.client.get(reverse("manage_edition_history", args=[0])).status_code, 404)

    def test_revert_needs_the_csrf_token(self) -> None:
        version = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"notes": "second"}, user=self.manager)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        response = client.post(reverse("manage_edition_revert", args=[self.edition.pk, version.history_id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).notes, "second")

    def test_lists_the_versions_with_what_changed(self) -> None:
        artist = Artist.objects.create(name="Jim Lee")
        other = make_publishing(publishing_title="Batman", year=2011)
        apply_changes(self.edition, {"notes": "second", "cover_artists": [artist]}, user=self.manager, reason="Typo")
        apply_changes(self.edition, {"publishing": other, "rarity": "legendary", "cover_artists": []}, user=self.manager)
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        entries = response.context["page_obj"].object_list
        self.assertEqual([entry.kind for entry in entries], ["changed", "changed", "created"])
        self.assertEqual([entry.user_label for entry in entries], ["manager", "manager", "System"])
        latest, middle, _first = entries
        changes = {change.label: (change.old, change.new) for change in latest.changes}
        self.assertEqual(changes["Rarity"], ("Common", "Legendary"))
        self.assertEqual(changes["Publishing"][1], "Batman (2011) 1st")
        self.assertEqual([(c.label, c.added, c.removed) for c in latest.relation_changes], [("Cover artists", [], ["Jim Lee"])])
        self.assertEqual({c.label: (c.old, c.new) for c in middle.changes}, {"Notes": ("first", "second")})
        self.assertEqual(middle.reason, "Typo")
        self.assertEqual([entry.can_revert for entry in entries], [False, True, True])
        html = response.content.decode()
        self.assertIn("Jim Lee", html)
        self.assertIn("Revert to this version", html)
        self.assertIn(reverse("manage_edition_revert", args=[self.edition.pk, entries[1].version.history_id]), html)

    def test_the_versions_are_paginated_ten_per_page(self) -> None:
        for number in range(11):  # 12 versions with the initial one
            apply_changes(self.edition, {"notes": f"note {number}"}, user=self.manager)
        self.client.force_login(self.manager)
        first_page = self.client.get(self.url)
        self.assertEqual(len(first_page.context["page_obj"].object_list), 10)
        self.assertEqual(first_page.context["page_obj"].object_list[0].changes[0].new, "note 10")  # newest first
        second_page = self.client.get(self.url, {"page": 2})
        entries = second_page.context["page_obj"].object_list
        self.assertEqual([entry.kind for entry in entries], ["changed", "created"])
        self.assertEqual(entries[0].changes[0].old, "first")  # the diff still spans the page break

    def test_reverting_from_the_page(self) -> None:
        first = edition_versions(self.edition)[0]
        apply_changes(self.edition, {"notes": "second"}, user=self.manager)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("manage_edition_revert", args=[self.edition.pk, first.history_id]),
            {"reason": "Vandalism", "next": "/gestion/ediciones/"},
            follow=True,
        )
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).notes, "first")
        self.assertRedirects(response, f"{self.url}?next=%2Fgestion%2Fediciones%2F")
        self.assertEqual(response.context["page_obj"].object_list[0].kind, "reverted")
        self.assertIn("Restored the version of", response.content.decode())
        reverted = edition_versions(self.edition)[0]
        self.assertEqual((reverted.history_user, reverted.history_change_reason), (self.manager, "Vandalism"))
        entry = LogEntry.objects.get(object_id=str(self.edition.pk), action_flag=CHANGE)
        self.assertEqual(entry.user, self.manager)
        self.assertIn("Restaurada la versión", entry.change_message)

    def test_a_refused_revert_shows_why(self) -> None:
        artist = Artist.objects.create(name="Gone")
        with_artist = apply_changes(self.edition, {"cover_artists": [artist]}, user=self.manager)
        apply_changes(self.edition, {"cover_artists": []}, user=self.manager)
        artist.delete()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("manage_edition_revert", args=[self.edition.pk, with_artist.history_id]), follow=True
        )
        self.assertIn("This version cannot be restored", response.content.decode())

    def test_issue_history_and_revert(self) -> None:
        issue = self.edition.issue
        first = versions_of(Issue, issue.pk).first()
        apply_changes(issue, {"synopsis": "Later"}, user=self.manager)
        self.client.force_login(self.manager)
        edit_page = self.client.get(reverse("manage_edition_edit", args=[self.edition.pk])).content.decode()
        self.assertIn(reverse("manage_issue_history", args=[issue.pk]), edit_page)
        self.assertIn(self.url, edit_page)
        response = self.client.get(reverse("manage_issue_history", args=[issue.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].object_list[0].changes[0].label, "Synopsis")
        self.client.post(reverse("manage_issue_revert", args=[issue.pk, first.history_id]))
        self.assertEqual(Issue.objects.get(pk=issue.pk).synopsis, "")

    def test_query_count_does_not_grow_with_the_versions(self) -> None:
        apply_changes(self.edition, {"cover_artists": [Artist.objects.create(name="First")]}, user=self.manager)
        self.client.force_login(self.manager)
        self.client.get(self.url)  # warm up (session, roles)
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        artists = [Artist.objects.create(name=f"Artist {i}") for i in range(4)]
        for artist in artists:
            apply_changes(self.edition, {"cover_artists": [artist], "notes": artist.name}, user=self.manager)
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(many), len(few))


class InitialVersionTests(ComiTestCase):
    def test_existing_rows_get_one_initial_version(self) -> None:
        artist = Artist.objects.create(name="Frank Miller")
        compilation = make_edition()
        compilation.cover_artists.add(artist)
        CollectedIssue.objects.create(edition=compilation, issue=make_edition().issue, order=1)
        compilation.sync_compilation_state()
        versioned = make_edition()
        # Rows from before the history: no versions at all.
        history = Edition.history.model
        history.cover_artists.model.objects.exclude(history__id=versioned.pk).delete()
        history.collected_issues.model.objects.exclude(history__id=versioned.pk).delete()
        history.objects.exclude(id=versioned.pk).delete()
        Issue.history.model.creators.model.objects.all().delete()
        Issue.history.model.objects.all().delete()
        versions = len(edition_versions(versioned))

        initial_versions.create_initial_versions(apps, None)
        initial_versions.create_initial_versions(apps, None)

        (initial,) = edition_versions(compilation)
        self.assertEqual((initial.history_type, initial.is_initial, initial.history_user), (CREATED, True, None))
        self.assertIsNone(initial.issue_id)
        self.assertEqual(list(initial.cover_artists.values_list("artist_id", flat=True)), [artist.pk])
        self.assertEqual(initial.collected_issues.count(), 1)
        self.assertEqual(len(edition_versions(versioned)), versions)  # already had versions: skipped
        for issue in Issue.objects.all():
            self.assertEqual(versions_of(Issue, issue.pk).filter(is_initial=True).count(), 1)
        entries = version_entries(Edition, compilation.pk)
        self.assertEqual((entries[0].kind, entries[0].user_label), ("initial", "System"))
