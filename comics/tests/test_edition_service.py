"""comics.editions.create_edition: a single-issue edition created without a request, with the
validation and saving of the Gestión edition form (its own issue, the issue section only on a new
issue, history by the given user, errors as plain JSON-serialisable data)."""

import json
from typing import Any
from unittest import mock

from django.db import IntegrityError

from comics.editions import create_edition
from comics.history import history_batch, history_model, versions_of
from comics.models import Artist, Edition, Issue
from comics.roles import ADMINISTRATORS

from .factories import make_edition, make_publishing, make_user
from .test_catalog_sources import COVER_URL, OPEN_URL, CatalogTestCase, FakeResponse, png_bytes


class CreateEditionTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.manager = make_user(role=ADMINISTRATORS)
        self.publishing = make_publishing(publishing_title="Spawn", year=1992)
        self.inker = Artist.objects.create(name="Todd McFarlane")
        self.colorist = Artist.objects.create(name="Steve Oliff")

    def edition_data(self, **changes: Any) -> dict[str, Any]:
        data = {
            "publishing": self.publishing.pk,
            "number": "1",
            "variant": "A",
            "printing": "1st",
            "format": "single_issue",
            "rarity": "common",
            "release_date": "1992-05-01",
            "cover_price": "1.95",
        }
        data.update(changes)
        return data

    def version_counts(self) -> tuple[int, int]:
        return history_model(Edition).objects.count(), history_model(Issue).objects.count()

    def test_a_new_own_issue_gets_the_issue_data(self) -> None:
        result = create_edition(
            self.edition_data(cover_artists=[self.inker.pk], mycomicshop_id="12345"),
            issue_data={"creators": [self.inker.pk, self.colorist.pk], "synopsis": "Al Simmons returns."},
            user=self.manager,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, {})
        self.assertTrue(result.issue_created)
        edition = Edition.objects.get(pk=result.edition.pk)
        self.assertEqual((edition.mycomicshop_id, list(edition.cover_artists.all())), (12345, [self.inker]))
        issue = edition.issue
        self.assertEqual((issue.publishing_id, issue.number), (self.publishing.pk, "1"))
        self.assertEqual(issue.synopsis, "Al Simmons returns.")
        self.assertEqual(set(issue.creators.all()), {self.inker, self.colorist})

    def test_an_existing_issue_is_left_untouched(self) -> None:
        existing = make_edition(publishing=self.publishing, number="1", variant="A")
        issue = existing.issue
        issue.creators.set([self.inker])
        for issue_data in (None, {"creators": [self.colorist.pk], "synopsis": "Changed"}):
            with self.subTest(issue_data=issue_data):
                variant = "B" if issue_data is None else "C"
                result = create_edition(self.edition_data(variant=variant), issue_data=issue_data, user=self.manager)
                self.assertTrue(result.ok, result.errors)
                self.assertFalse(result.issue_created)
                self.assertEqual(result.edition.issue_id, issue.pk)
                issue.refresh_from_db()
                self.assertEqual(list(issue.creators.all()), [self.inker])
                self.assertEqual(issue.synopsis, "")
        self.assertEqual(Issue.objects.count(), 1)

    def test_invalid_values_come_back_as_errors_and_write_nothing(self) -> None:
        before = self.version_counts()
        result = create_edition(
            self.edition_data(number="", cover_artists=[999_999]),
            issue_data={"metron_id": "not a number"},
            user=self.manager,
        )
        self.assertFalse(result.ok)
        self.assertIsNone(result.edition)
        self.assertEqual(set(result.errors), {"number", "cover_artists", "metron_id"})
        json.dumps(result.errors)  # stored as JSON by its callers
        self.assertFalse(Edition.objects.exists())
        self.assertFalse(Issue.objects.exists())
        self.assertEqual(self.version_counts(), before)

    def test_a_failing_save_rolls_everything_back(self) -> None:
        # The issue section's ids are checked against the issue the edition ends up with, on save.
        other = make_edition(publishing=self.publishing, number="2").issue
        Issue.objects.filter(pk=other.pk).update(metron_id=15439)
        before = (Edition.objects.count(), Issue.objects.count(), self.version_counts())
        result = create_edition(self.edition_data(), issue_data={"metron_id": "15439"}, user=self.manager)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, {"__all__": ["Another issue already has this Metron ID."]})
        self.assertEqual((Edition.objects.count(), Issue.objects.count(), self.version_counts()), before)

    def test_a_duplicate_edition_is_an_error(self) -> None:
        make_edition(publishing=self.publishing, number="1", variant="A", printing="1st")
        result = create_edition(self.edition_data(), issue_data=None, user=self.manager)
        self.assertFalse(result.ok)
        self.assertEqual(list(result.errors), ["__all__"])
        self.assertEqual(Edition.objects.count(), 1)

    def test_the_versions_are_written_by_the_given_user(self) -> None:
        result = create_edition(
            self.edition_data(cover_artists=[self.inker.pk]),
            issue_data={"creators": [self.colorist.pk]},
            user=self.manager,
            reason="MyComicShop import",
        )
        edition = result.edition
        for model, pk in ((Edition, edition.pk), (Issue, edition.issue_id)):
            with self.subTest(model=model.__name__):
                versions = list(versions_of(model, pk))
                self.assertEqual(len(versions), 1)  # one batch: one version per row, relations included
                self.assertEqual(versions[0].history_type, "+")
                self.assertEqual(versions[0].history_user, self.manager)
                self.assertEqual(versions[0].history_change_reason, "MyComicShop import")

    def test_inside_a_request_batch_the_versions_are_still_by_the_given_user(self) -> None:
        # As from a view: HistoryMiddleware opens a batch for the request's user.
        visitor = make_user()
        with history_batch(user=visitor, reason="Request"):
            result = create_edition(
                self.edition_data(cover_artists=[self.inker.pk]),
                issue_data={"creators": [self.colorist.pk]},
                user=self.manager,
                reason="MyComicShop import",
            )
        edition = result.edition
        for model, pk in ((Edition, edition.pk), (Issue, edition.issue_id)):
            with self.subTest(model=model.__name__):
                versions = list(versions_of(model, pk))
                self.assertEqual(len(versions), 1)
                self.assertEqual(versions[0].history_user, self.manager)
                self.assertEqual(versions[0].history_change_reason, "MyComicShop import")

    def test_an_integrity_error_while_saving_is_an_error(self) -> None:
        # E.g. another request saved the same IVID between the form's check and this save.
        with mock.patch("comics.editions.save_edition", side_effect=IntegrityError("duplicate")):
            result = create_edition(self.edition_data(), issue_data=None, user=self.manager)
        self.assertFalse(result.ok)
        self.assertEqual(
            result.errors,
            {"__all__": ["The edition could not be saved: something it uses changed meanwhile. Try again."]},
        )
        self.assertFalse(Edition.objects.exists())

    def test_the_catalogue_cover_is_downloaded(self) -> None:
        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())) as open_url:
            result = create_edition(self.edition_data(catalog_cover_url=COVER_URL), issue_data=None, user=self.manager)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(open_url.call_args.args[1].full_url, COVER_URL)
        edition = Edition.objects.get(pk=result.edition.pk)
        self.assertTrue(edition.image.name.endswith(".jpg"))
        self.assertTrue(edition.thumbnail)

    def test_a_cover_from_another_host_is_an_error(self) -> None:
        with mock.patch(OPEN_URL) as open_url:
            result = create_edition(
                self.edition_data(catalog_cover_url="https://evil.example/cover.jpg"), issue_data=None, user=None
            )
        open_url.assert_not_called()
        self.assertEqual(
            result.errors, {"__all__": ["Covers can only be downloaded from the catalogues Comi imports from."]}
        )
        self.assertFalse(Edition.objects.exists())

    def test_achievements_are_scheduled_as_after_a_gestion_save(self) -> None:
        with mock.patch("comics.editions.schedule_edition_owners") as schedule:
            result = create_edition(self.edition_data(), issue_data=None, user=self.manager)
        schedule.assert_called_once_with(result.edition, None, contents_changed=False)
