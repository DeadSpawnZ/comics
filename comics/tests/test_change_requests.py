"""Change requests (comics.change_requests): submitting corrections and new issues or editions, the
state machine and its permissions per role, approval through the history (with conflicts and
deleted targets), the author's notifications, the Reportero achievement and the pages."""

import importlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection
from django.urls import reverse

from comics import change_requests
from comics.achievements import engine
from comics.change_requests import InvalidTransition, approve, reject, review_request, submit, validate, withdraw
from comics.history import CHANGED, CREATED, history_model, versions_of
from comics.models import Artist, ChangeRequest, Edition, Issue, Notification, UserAchievement
from comics.roles import ADMINISTRATORS, REVIEWERS
from comics.versions import apply_changes

from .base import ComiTestCase
from .factories import make_edition, make_publishing, make_superuser, make_user

permissions_migration = importlib.import_module("comics.migrations.0110_change_request_permissions")
schema_migration = importlib.import_module("comics.migrations.0109_change_requests")


def edition_data(edition: Edition, **changes: Any) -> dict[str, Any]:
    """The proposal form as it is submitted for `edition` unchanged, plus `changes`."""
    data = {
        "publishing": edition.publishing_id,
        "number": edition.number,
        "variant": edition.variant,
        "printing": edition.printing,
        "format": edition.format,
        "rarity": edition.rarity,
        "release_date": edition.release_date.isoformat(),
        "cover_price": str(edition.cover_price),
        "ratio": edition.ratio,
        "limited_to": edition.limited_to,
        "retailer_exclusive": edition.retailer_exclusive,
        "event_exclusive": edition.event_exclusive,
        "notes": edition.notes,
        "cover_artists": [artist.pk for artist in edition.cover_artists.all()],
        "reason": "The cover says otherwise.",
        "evidence_url": "",
    }
    data.update(changes)
    return data


def new_edition_data(publishing_id: int, **changes: Any) -> dict[str, Any]:
    data = {
        "publishing": publishing_id,
        "number": "7",
        "variant": "B",
        "printing": "1st",
        "format": "single_issue",
        "rarity": "common",
        "release_date": "2021-05-04",
        "cover_price": "4.99",
        "ratio": "",
        "limited_to": "",
        "retailer_exclusive": "",
        "event_exclusive": "",
        "notes": "",
        "reason": "It is missing.",
        "evidence_url": "https://example.com/catalogue/7",
    }
    data.update(changes)
    return data


def fresh(change_request: ChangeRequest) -> ChangeRequest:
    return ChangeRequest.objects.get(pk=change_request.pk)


class ChangeRequestTestCase(ComiTestCase):
    def setUp(self) -> None:
        self.author = make_user(username="author")
        self.reviewer = make_user(username="reviewer", role=REVIEWERS)
        self.admin = make_user(username="admin", role=ADMINISTRATORS)
        self.edition = make_edition(notes="Old notes", variant="A")
        self.artist = Artist.objects.create(name="Greg Capullo")

    def update_request(self, **changes: Any) -> ChangeRequest:
        """A pending correction of self.edition by self.author (as the form would submit it)."""
        changes = changes or {"notes": "New notes"}
        encoded = {
            name: change_requests.encode_value(Edition._meta.get_field(name), value) for name, value in changes.items()
        }
        return submit(
            author=self.author,
            model=Edition,
            changes=encoded,
            label=change_requests.target_label(self.edition),
            reason="Wrong data",
            target=self.edition,
        )


# ---------- Submitting ----------


class SubmitTests(ChangeRequestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.author)

    def test_a_correction_stores_only_the_changed_fields_and_the_base_version(self) -> None:
        url = reverse("comics_edition_suggest", args=[self.edition.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = edition_data(
            self.edition, notes="Signed by the artist", cover_price="3.99", cover_artists=[self.artist.pk]
        )
        response = self.client.post(url, data)

        change_request = ChangeRequest.objects.get()
        self.assertRedirects(response, reverse("comics_request_detail", args=[change_request.pk]))
        self.assertEqual(
            change_request.changes,
            {"notes": "Signed by the artist", "cover_price": "3.99", "cover_artists": [self.artist.pk]},
        )
        self.assertEqual(
            (change_request.kind, change_request.action, change_request.status),
            (ChangeRequest.Kind.EDITION, ChangeRequest.Action.UPDATE, ChangeRequest.Status.PENDING),
        )
        self.assertEqual(change_request.target_pk, self.edition.pk)
        self.assertEqual(change_request.base_history_id, versions_of(Edition, self.edition.pk).first().history_id)
        self.assertEqual(change_request.author, self.author)
        self.assertEqual(change_request.reason, "The cover says otherwise.")
        self.assertEqual(change_request.target_label, self.edition.short_name)
        # Nothing was applied: the edition and its history are as they were.
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.notes, "Old notes")
        self.assertEqual(versions_of(Edition, self.edition.pk).count(), 1)

    def test_a_correction_without_changes_is_refused(self) -> None:
        response = self.client.post(reverse("comics_edition_suggest", args=[self.edition.pk]), edition_data(self.edition))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change at least one value")
        self.assertFalse(ChangeRequest.objects.exists())

    def test_proposals_are_validated_like_gestion(self) -> None:
        url = reverse("comics_edition_suggest", args=[self.edition.pk])
        other = make_edition(publishing=self.edition.publishing, number="99", variant="A")
        cases = {
            "ratio": edition_data(self.edition, ratio="lots"),
            "reason": edition_data(self.edition, notes="x", reason=""),
            "evidence": edition_data(self.edition, notes="x", evidence_url="javascript:alert(1)"),
            "duplicate": edition_data(self.edition, number="99"),  # same publishing, number, variant as `other`
        }
        for case, data in cases.items():
            with self.subTest(case=case):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(ChangeRequest.objects.exists())
        self.assertTrue(Edition.objects.filter(pk=other.pk).exists())

    def test_proposing_a_new_edition_stores_the_whole_object(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        url = reverse("comics_request_new", args=["edicion"])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, new_edition_data(publishing.pk, cover_artists=[self.artist.pk]))

        change_request = ChangeRequest.objects.get()
        self.assertRedirects(response, reverse("comics_request_detail", args=[change_request.pk]))
        self.assertEqual(change_request.action, ChangeRequest.Action.CREATE)
        self.assertIsNone(change_request.target_pk)
        self.assertIsNone(change_request.base_history_id)
        self.assertEqual(change_request.target_label, "Spawn #7 B")
        self.assertEqual(change_request.evidence_url, "https://example.com/catalogue/7")
        self.assertEqual(change_request.changes["publishing"], publishing.pk)
        self.assertEqual(change_request.changes["release_date"], "2021-05-04")
        self.assertEqual(change_request.changes["cover_artists"], [self.artist.pk])
        self.assertEqual(
            set(change_request.changes),
            set(edition_data(self.edition)) - {"reason", "evidence_url"},
        )
        self.assertFalse(Edition.objects.filter(publishing=publishing).exists())

    def test_proposing_a_new_issue(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        url = reverse("comics_request_new", args=["issue"])
        data = {"publishing": publishing.pk, "number": "300", "synopsis": "Big one", "reason": "Missing"}
        self.client.post(url, data)
        change_request = ChangeRequest.objects.get()
        self.assertEqual((change_request.kind, change_request.target_label), ("issue", "Spawn #300"))
        self.assertEqual(
            change_request.changes, {"publishing": publishing.pk, "number": "300", "synopsis": "Big one", "creators": []}
        )

        # An issue that already exists cannot be proposed again.
        response = self.client.post(url, {**data, "number": self.edition.issue.number, "publishing": self.edition.publishing_id})
        self.assertContains(response, "An issue with that publishing and number already exists.")
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_an_issue_correction_covers_synopsis_and_creators_only(self) -> None:
        issue = self.edition.issue
        url = reverse("comics_issue_suggest", args=[issue.pk])
        data = {"synopsis": "Al Simmons returns.", "creators": [self.artist.pk], "number": "999", "reason": "Plot"}
        self.client.post(url, data)
        change_request = ChangeRequest.objects.get()
        self.assertEqual(change_request.changes, {"synopsis": "Al Simmons returns.", "creators": [self.artist.pk]})
        self.assertEqual((change_request.kind, change_request.target_pk), ("issue", issue.pk))

    def test_unknown_kind_and_missing_target_are_404(self) -> None:
        self.assertEqual(self.client.get(reverse("comics_request_new", args=["arc"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("comics_edition_suggest", args=[999999])).status_code, 404)

    def test_edition_page_links_to_the_suggestions(self) -> None:
        response = self.client.get(reverse("comics_edition_detail", args=[self.edition.pk]))
        self.assertContains(response, reverse("comics_edition_suggest", args=[self.edition.pk]))
        self.assertContains(response, reverse("comics_issue_suggest", args=[self.edition.issue_id]))


# ---------- Permissions and the state machine ----------


class PermissionTests(ChangeRequestTestCase):
    """Who may do what, through the pages (anonymous, collector, reviewer, administrator)."""

    def post_step(self, user: User | None, step: str, change_request: ChangeRequest) -> Any:
        self.client.logout()
        if user is not None:
            self.client.force_login(user)
        return self.client.post(reverse(f"review_{step}", args=[change_request.pk]), {"note": ""})

    def test_anonymous_users_are_sent_to_log_in(self) -> None:
        change_request = self.update_request()
        urls = [
            reverse("comics_edition_suggest", args=[self.edition.pk]),
            reverse("comics_request_new", args=["edicion"]),
            reverse("comics_requests"),
            reverse("review_queue"),
            reverse("review_detail", args=[change_request.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))
        for step in ("validate", "approve", "reject"):
            with self.subTest(step=step):
                self.assertEqual(self.post_step(None, step, change_request).status_code, 302)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)

    def test_collectors_submit_but_cannot_review(self) -> None:
        collector = make_user()
        change_request = self.update_request()
        self.client.force_login(collector)
        self.assertEqual(self.client.get(reverse("review_queue")).status_code, 403)
        self.assertEqual(self.client.get(reverse("review_detail", args=[change_request.pk])).status_code, 403)
        for step in ("validate", "approve", "reject"):
            with self.subTest(step=step):
                self.assertEqual(self.post_step(collector, step, change_request).status_code, 403)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)

    def test_reviewers_validate_and_reject_but_cannot_approve_nor_open_gestion(self) -> None:
        change_request = self.update_request()
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("review_queue")).status_code, 200)
        self.assertEqual(self.client.get(reverse("manage_home")).status_code, 403)
        self.assertEqual(self.client.get(reverse("manage_editions")).status_code, 403)

        self.assertEqual(self.post_step(self.reviewer, "approve", change_request).status_code, 403)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)

        self.post_step(self.reviewer, "validate", change_request)
        change_request = fresh(change_request)
        self.assertEqual((change_request.status, change_request.validated_by), ("validated", self.reviewer))

        # Once validated, the decision belongs to an administrator.
        response = self.post_step(self.reviewer, "reject", change_request)
        self.assertRedirects(response, reverse("review_detail", args=[change_request.pk]))
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.VALIDATED)

        pending = self.update_request()
        self.post_step(self.reviewer, "reject", pending)
        self.assertEqual(fresh(pending).status, ChangeRequest.Status.REJECTED)

    def test_administrators_approve_straight_from_pending(self) -> None:
        change_request = self.update_request()
        response = self.post_step(self.admin, "approve", change_request)
        self.assertRedirects(response, reverse("review_detail", args=[change_request.pk]))
        change_request = fresh(change_request)
        self.assertEqual((change_request.status, change_request.decided_by), ("approved", self.admin))
        self.assertIsNone(change_request.validated_at)
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).notes, "New notes")

    def test_administrators_validate_then_approve_or_reject(self) -> None:
        first, second = self.update_request(), self.update_request(notes="Other")
        for change_request in (first, second):
            self.post_step(self.admin, "validate", change_request)
        self.post_step(self.admin, "approve", first)
        self.post_step(self.admin, "reject", second)
        self.assertEqual(fresh(first).status, ChangeRequest.Status.APPROVED)
        self.assertEqual(fresh(second).status, ChangeRequest.Status.REJECTED)

    def test_superusers_can_do_everything(self) -> None:
        change_request = self.update_request()
        self.post_step(make_superuser(), "approve", change_request)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.APPROVED)

    def test_steps_are_post_only(self) -> None:
        change_request = self.update_request()
        self.client.force_login(self.admin)
        for step in ("validate", "approve", "reject"):
            with self.subTest(step=step):
                self.assertEqual(self.client.get(reverse(f"review_{step}", args=[change_request.pk])).status_code, 405)
        self.assertEqual(
            self.client.get(reverse("comics_request_withdraw", args=[change_request.pk])).status_code, 405
        )

    def test_only_the_author_withdraws_and_only_while_pending(self) -> None:
        change_request = self.update_request()
        url = reverse("comics_request_withdraw", args=[change_request.pk])
        self.client.force_login(make_user())
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)

        self.client.force_login(self.author)
        self.client.post(url)
        change_request = fresh(change_request)
        self.assertEqual((change_request.status, change_request.decided_by), ("withdrawn", self.author))

        validated = self.update_request()
        validate(validated, user=self.reviewer)
        self.client.post(reverse("comics_request_withdraw", args=[validated.pk]))
        self.assertEqual(fresh(validated).status, ChangeRequest.Status.VALIDATED)

    def test_nobody_reviews_their_own_request(self) -> None:
        own = submit(
            author=self.admin, model=Edition, changes={"notes": "Mine"}, label="x", reason="r", target=self.edition
        )
        self.assertEqual(own.allowed_actions(self.admin), frozenset({"withdraw"}))
        with self.assertRaises(InvalidTransition):
            approve(own, user=self.admin)
        self.assertEqual(fresh(own).status, ChangeRequest.Status.PENDING)

    def test_users_only_see_their_own_requests(self) -> None:
        change_request = self.update_request()
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(reverse("comics_request_detail", args=[change_request.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("comics_requests")), change_request.title)
        self.client.force_login(self.author)
        self.assertContains(self.client.get(reverse("comics_requests")), change_request.title)
        self.assertContains(self.client.get(reverse("comics_request_detail", args=[change_request.pk])), "Withdraw")


class StateMachineTests(ChangeRequestTestCase):
    def test_allowed_actions_per_status_and_role(self) -> None:
        change_request = self.update_request()
        expected = {
            ChangeRequest.Status.PENDING: {
                "reviewer": {"validate", "reject"},
                "admin": {"validate", "approve", "reject"},
                "author": {"withdraw"},
            },
            ChangeRequest.Status.VALIDATED: {"reviewer": set(), "admin": {"approve", "reject"}, "author": set()},
            ChangeRequest.Status.APPROVED: {"reviewer": set(), "admin": set(), "author": set()},
            ChangeRequest.Status.REJECTED: {"reviewer": set(), "admin": set(), "author": set()},
            ChangeRequest.Status.WITHDRAWN: {"reviewer": set(), "admin": set(), "author": set()},
        }
        users = {"reviewer": self.reviewer, "admin": self.admin, "author": self.author}
        for status, per_user in expected.items():
            change_request.status = status
            for name, actions in per_user.items():
                with self.subTest(status=status, user=name):
                    self.assertEqual(change_request.allowed_actions(users[name]), actions)
        self.assertEqual(change_request.allowed_actions(make_user()), frozenset())

    def test_invalid_transitions_are_refused_and_change_nothing(self) -> None:
        rejected = self.update_request()
        reject(rejected, user=self.reviewer, note="No")
        withdrawn = self.update_request()
        withdraw(withdrawn, user=self.author)
        approved = self.update_request()
        approve(approved, user=self.admin)
        validated = self.update_request(notes="Other")
        validate(validated, user=self.reviewer)

        attempts = [
            (approve, rejected, self.admin),
            (validate, rejected, self.reviewer),
            (approve, withdrawn, self.admin),
            (reject, approved, self.admin),
            (approve, approved, self.admin),
            (validate, validated, self.admin),
        ]
        for step, change_request, user in attempts:
            with self.subTest(step=step.__name__, status=change_request.status):
                before = fresh(change_request)
                with self.assertRaises(InvalidTransition):
                    step(change_request, user=user)
                after = fresh(change_request)
                self.assertEqual(
                    (after.status, after.decided_at, after.validated_at),
                    (before.status, before.decided_at, before.validated_at),
                )
        with self.assertRaises(InvalidTransition):
            withdraw(validated, user=self.author)

    def test_services_check_the_role_too(self) -> None:
        change_request = self.update_request()
        with self.assertRaises(PermissionDenied):
            approve(change_request, user=self.reviewer)
        with self.assertRaises(PermissionDenied):
            validate(change_request, user=make_user())
        with self.assertRaises(PermissionDenied):
            reject(change_request, user=make_user())

    def test_steps_record_who_when_and_the_note(self) -> None:
        change_request = self.update_request()
        validate(change_request, user=self.reviewer, note="Checked the cover")
        approve(change_request, user=self.admin, note="Thanks")
        change_request = fresh(change_request)
        self.assertEqual(
            (change_request.validated_by, change_request.validation_note, change_request.decided_by),
            (self.reviewer, "Checked the cover", self.admin),
        )
        self.assertEqual(change_request.decision_note, "Thanks")
        self.assertIsNotNone(change_request.validated_at)
        self.assertGreaterEqual(change_request.decided_at, change_request.validated_at)


# ---------- Approval ----------


class ApprovalTests(ChangeRequestTestCase):
    def test_a_correction_is_applied_through_the_history(self) -> None:
        change_request = self.update_request(notes="New notes", cover_artists=[self.artist])
        version = approve(change_request, user=self.admin)

        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertEqual(edition.notes, "New notes")
        self.assertEqual(list(edition.cover_artists.all()), [self.artist])
        latest = versions_of(Edition, edition.pk).first()
        self.assertEqual(latest.history_id, version.history_id)
        self.assertEqual((latest.history_type, latest.history_user), (CHANGED, self.admin))
        self.assertEqual(latest.history_change_reason, f"Change request #{change_request.pk}: Wrong data")
        change_request = fresh(change_request)
        self.assertEqual(change_request.applied_history_id, version.history_id)
        self.assertEqual(change_request.target_pk, edition.pk)

    def test_only_the_requested_fields_are_written(self) -> None:
        change_request = self.update_request(notes="New notes")
        apply_changes(Edition.objects.get(pk=self.edition.pk), {"variant": "C"}, user=self.admin)
        approve(change_request, user=self.admin)
        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertEqual((edition.notes, edition.variant), ("New notes", "C"))

    def test_the_history_does_not_refer_to_requests(self) -> None:
        for model in (Edition, Issue):
            with self.subTest(model=model.__name__):
                names = {field.name for field in history_model(model)._meta.get_fields()}
                self.assertFalse({name for name in names if "request" in name})
                related = {field.related_model for field in history_model(model)._meta.get_fields() if field.is_relation}
                self.assertNotIn(ChangeRequest, related)

    def test_a_new_edition_is_created_with_a_created_version(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        values = new_edition_data(publishing.pk, cover_artists=[self.artist.pk])
        changes = {
            name: values[name] for name in values if name not in ("reason", "evidence_url")
        }
        change_request = submit(
            author=self.author, model=Edition, changes=changes, label="Spawn #7 B", reason="Missing"
        )
        with mock.patch.object(Edition, "process_image"):  # no cover: nothing to process
            version = approve(change_request, user=self.admin)

        edition = Edition.objects.get(publishing=publishing, number="7")
        self.assertEqual((edition.variant, edition.cover_price), ("B", Decimal("4.99")))
        self.assertEqual(list(edition.cover_artists.all()), [self.artist])
        self.assertIsNotNone(edition.issue)  # its own issue, as in Gestión
        self.assertEqual((version.history_type, version.history_user, version.id), (CREATED, self.admin, edition.pk))
        change_request = fresh(change_request)
        self.assertEqual((change_request.target_pk, change_request.applied_history_id), (edition.pk, version.history_id))
        self.assertEqual(change_request.target_label, edition.short_name)

    def test_a_new_issue_is_created(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        change_request = submit(
            author=self.author,
            model=Issue,
            changes={"publishing": publishing.pk, "number": "300", "synopsis": "", "creators": [self.artist.pk]},
            label="Spawn #300",
            reason="Missing",
        )
        version = approve(change_request, user=self.admin)
        issue = Issue.objects.get(publishing=publishing, number="300")
        self.assertEqual(list(issue.creators.all()), [self.artist])
        self.assertEqual((version.history_type, fresh(change_request).target_pk), (CREATED, issue.pk))

    def test_a_proposal_that_breaks_a_model_rule_is_not_applied(self) -> None:
        make_edition(publishing=self.edition.publishing, number="99", variant="A")
        change_request = self.update_request(number="99")
        with self.assertRaises(ValidationError):
            approve(change_request, user=self.admin)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)
        self.assertEqual(Edition.objects.get(pk=self.edition.pk).number, self.edition.number)
        self.assertFalse(Notification.objects.filter(recipient=self.author).exists())

    def test_a_concurrent_duplicate_is_an_error_not_a_crash(self) -> None:
        # Another approval saved the same unique values after this one was validated.
        change_request = self.update_request()
        self.client.force_login(self.admin)
        with mock.patch.object(change_requests, "apply_changes", side_effect=IntegrityError("duplicate")):
            with self.assertRaises(ValidationError) as caught:
                approve(change_request, user=self.admin)
            self.assertEqual(caught.exception.code, "duplicate")
            response = self.client.post(reverse("review_approve", args=[change_request.pk]), {"note": ""}, follow=True)
        self.assertContains(response, "was just saved")
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)

    def test_a_proposal_pointing_to_a_deleted_artist_is_not_applied(self) -> None:
        change_request = self.update_request(cover_artists=[self.artist])
        self.artist.delete()
        with self.assertRaisesMessage(ValidationError, "no longer exists"):
            approve(change_request, user=self.admin)
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)


class ConflictTests(ChangeRequestTestCase):
    def test_fields_changed_since_the_request_are_flagged_and_can_be_overwritten(self) -> None:
        change_request = self.update_request(notes="Proposed notes", variant="B")
        apply_changes(Edition.objects.get(pk=self.edition.pk), {"notes": "Edited meanwhile"}, user=self.admin)

        reviews = {review.label: review for review in review_request(change_request).fields}
        self.assertEqual(
            (reviews["Notes"].base, reviews["Notes"].current, reviews["Notes"].proposed),
            ("Old notes", "Edited meanwhile", "Proposed notes"),
        )
        self.assertTrue(reviews["Notes"].conflict)
        self.assertFalse(reviews["Variant"].conflict)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("review_detail", args=[change_request.pk]))
        self.assertContains(response, "Conflict")
        self.assertContains(response, "Approving overwrites them")
        self.assertTrue(response.context["has_conflicts"])

        approve(change_request, user=self.admin)
        edition = Edition.objects.get(pk=self.edition.pk)
        self.assertEqual((edition.notes, edition.variant), ("Proposed notes", "B"))

    def test_a_decided_request_reports_no_conflicts(self) -> None:
        change_request = self.update_request(notes="Proposed notes")
        apply_changes(Edition.objects.get(pk=self.edition.pk), {"notes": "Edited meanwhile"}, user=self.admin)
        self.assertTrue(review_request(change_request).has_conflicts)
        approve(change_request, user=self.admin)

        # The target now holds the proposed values, which differ from the base ones: not a conflict.
        self.assertFalse(review_request(fresh(change_request)).has_conflicts)
        pages = [
            (self.admin, reverse("review_detail", args=[change_request.pk])),
            (self.author, reverse("comics_request_detail", args=[change_request.pk])),
        ]
        for user, url in pages:
            with self.subTest(url=url):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertNotContains(response, "Conflict")
                self.assertNotContains(response, "table-warning")

    def test_relations_are_compared_too(self) -> None:
        change_request = self.update_request(cover_artists=[self.artist])
        other = Artist.objects.create(name="Todd McFarlane")
        apply_changes(Edition.objects.get(pk=self.edition.pk), {"cover_artists": [other]}, user=self.admin)
        (review,) = review_request(change_request).fields
        self.assertEqual((review.base, review.current, review.proposed), ("—", "Todd McFarlane", "Greg Capullo"))
        self.assertTrue(review.conflict)

    def test_no_conflict_without_changes_since(self) -> None:
        change_request = self.update_request(notes="Proposed notes")
        self.assertFalse(any(review.conflict for review in review_request(change_request).fields))
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("review_detail", args=[change_request.pk])), "Approving overwrites")


class DeletedTargetTests(ChangeRequestTestCase):
    def test_a_request_whose_target_was_deleted_can_only_be_rejected(self) -> None:
        change_request = self.update_request()
        Edition.objects.filter(pk=self.edition.pk).delete()

        self.client.force_login(self.admin)
        response = self.client.get(reverse("review_detail", args=[change_request.pk]))
        self.assertContains(response, "it cannot be approved, only rejected")
        self.assertNotContains(response, reverse("review_approve", args=[change_request.pk]))
        self.assertContains(response, change_request.target_label)  # still named

        response = self.client.post(reverse("review_approve", args=[change_request.pk]), {"note": ""}, follow=True)
        self.assertContains(response, "no longer exists")
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.PENDING)
        self.assertFalse(Edition.objects.filter(pk=self.edition.pk).exists())

        reject(change_request, user=self.admin, note="It was deleted")
        self.assertEqual(fresh(change_request).status, ChangeRequest.Status.REJECTED)


# ---------- Notifications and the achievement ----------


class NotificationTests(ChangeRequestTestCase):
    def notifications(self) -> list[Notification]:
        return list(Notification.objects.filter(recipient=self.author).order_by("id"))

    def test_the_author_is_told_about_each_step(self) -> None:
        change_request = self.update_request()
        validate(change_request, user=self.reviewer, note="Looks right")
        approve(change_request, user=self.admin, note="Applied")
        rejected = self.update_request(notes="Other")
        reject(rejected, user=self.reviewer, note="No source")

        steps = [n for n in self.notifications() if n.change_request_id]
        self.assertEqual(
            [(n.kind, n.change_request_id) for n in steps],
            [
                (Notification.Kind.CHANGE_REQUEST_VALIDATED, change_request.pk),
                (Notification.Kind.CHANGE_REQUEST_APPROVED, change_request.pk),
                (Notification.Kind.CHANGE_REQUEST_REJECTED, rejected.pk),
            ],
        )
        title = fresh(change_request).title
        self.assertEqual(steps[0].text, f"Your request was validated: {title}")
        self.assertEqual(steps[1].text, f"Your request was approved: {title}")
        self.assertEqual([n.detail for n in steps], ["Looks right", "Applied", "No source"])
        self.assertEqual(steps[2].target_url, reverse("comics_request_detail", args=[rejected.pk]))
        self.assertEqual(steps[0].icon, "fact_check")
        # Nobody else is notified.
        self.assertEqual(Notification.objects.exclude(recipient=self.author).count(), 0)

    def test_withdrawing_notifies_nobody(self) -> None:
        withdraw(self.update_request(), user=self.author)
        self.assertFalse(Notification.objects.exists())

    def test_opening_it_leads_to_the_request(self) -> None:
        change_request = self.update_request()
        validate(change_request, user=self.reviewer)
        notification = Notification.objects.get(recipient=self.author)
        self.client.force_login(self.author)
        response = self.client.post(reverse("notification_open", args=[notification.pk]))
        self.assertRedirects(response, reverse("comics_request_detail", args=[change_request.pk]))
        self.assertContains(self.client.get(reverse("notifications")), "Your request was validated")


class ReporteroTests(ChangeRequestTestCase):
    def setUp(self) -> None:
        # Committed, so the fixtures' own evaluations do not absorb the approval's (see ComiTestCase).
        with self.committed():
            super().setUp()

    def test_awarded_when_a_request_is_approved(self) -> None:
        change_request = self.update_request()
        reject(self.update_request(notes="x"), user=self.admin)
        self.assertFalse(UserAchievement.objects.filter(user=self.author, achievement__code="reportero").exists())
        with self.committed():
            approve(change_request, user=self.admin)
        award = UserAchievement.objects.get(user=self.author, achievement__code="reportero")
        self.assertEqual(award.level, 1)
        self.assertTrue(
            Notification.objects.filter(recipient=self.author, kind=Notification.Kind.ACHIEVEMENT).exists()
        )
        # The approver gets nothing for approving.
        self.assertFalse(UserAchievement.objects.filter(user=self.admin, achievement__code="reportero").exists())

    def test_retroactive_and_category(self) -> None:
        change_request = self.update_request()
        with self.committed(), mock.patch.object(change_requests, "schedule"):  # approved before the rule existed
            approve(change_request, user=self.admin)
        self.assertFalse(UserAchievement.objects.filter(user=self.author, achievement__code="reportero").exists())
        self.assertEqual(engine.evaluate(self.author, ["reportero"]), ["reportero"])
        achievement = UserAchievement.objects.get(user=self.author, achievement__code="reportero").achievement
        self.assertEqual((achievement.category.code, achievement.name_es), ("random", "Reportero"))


# ---------- Pages ----------


class PageTests(ChangeRequestTestCase):
    def test_user_supplied_texts_are_escaped(self) -> None:
        change_request = submit(
            author=self.author,
            model=Edition,
            changes={"notes": "<b>bold</b>"},
            label=change_requests.target_label(self.edition),
            reason="<script>alert('reason')</script>",
            evidence_url='https://example.com/"><script>alert(1)</script>',
            target=self.edition,
        )
        validate(change_request, user=self.reviewer, note="<i>note</i>")
        pages = [
            (self.admin, reverse("review_detail", args=[change_request.pk])),
            (self.admin, reverse("review_queue")),
            (self.author, reverse("comics_request_detail", args=[change_request.pk])),
            (self.author, reverse("notifications")),
        ]
        for user, url in pages:
            with self.subTest(url=url):
                self.client.force_login(user)
                content = self.client.get(url).content.decode()
                self.assertNotIn("<script>alert", content)
                self.assertNotIn("<i>note</i>", content)
                self.assertNotIn("<b>bold</b>", content)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("review_detail", args=[change_request.pk]))
        self.assertContains(response, "&lt;script&gt;alert(&#x27;reason&#x27;)&lt;/script&gt;")
        self.assertContains(response, 'rel="noopener noreferrer nofollow"')

    def test_queue_filters_by_status_and_counts(self) -> None:
        pending, validated, rejected = self.update_request(), self.update_request(notes="v"), self.update_request(notes="r")
        validate(validated, user=self.reviewer)
        reject(rejected, user=self.reviewer)
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("review_queue"))
        self.assertEqual([item.pk for item in response.context["page_obj"]], [pending.pk, validated.pk])  # oldest first
        counts = {value: count for value, _label, count in response.context["tabs"]}
        self.assertEqual((counts["open"], counts["pending"], counts["rejected"], counts["all"]), (2, 1, 1, 3))
        response = self.client.get(reverse("review_queue"), {"status": "rejected"})
        self.assertEqual([item.pk for item in response.context["page_obj"]], [rejected.pk])
        response = self.client.get(reverse("review_queue"), {"status": "bogus"})
        self.assertEqual(response.context["selected"], "open")

    def test_review_links_per_role(self) -> None:
        self.update_request()
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("comics"))
        self.assertContains(response, reverse("review_queue"))
        self.assertNotContains(response, reverse("manage_home"))

        self.client.force_login(self.admin)
        response = self.client.get(reverse("manage_home"))
        self.assertContains(response, reverse("review_queue"))
        self.assertEqual(response.context["review_badge"].open_count, 1)

        self.client.force_login(self.author)
        self.assertNotContains(self.client.get(reverse("comics")), reverse("review_queue"))

        # Gestión without the change-request permissions: no link to the review area.
        gestion_only = make_user()
        gestion_only.user_permissions.add(Permission.objects.get(codename="access_management"))
        self.client.force_login(gestion_only)
        response = self.client.get(reverse("manage_home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("review_queue"))

    def test_review_page_offers_the_actions_of_the_role(self) -> None:
        change_request = self.update_request()
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("review_detail", args=[change_request.pk]))
        self.assertContains(response, reverse("review_validate", args=[change_request.pk]))
        self.assertNotContains(response, reverse("review_approve", args=[change_request.pk]))
        self.assertIsNone(response.context["history_url"])  # Gestión's history is not for reviewers

        self.client.force_login(self.admin)
        response = self.client.get(reverse("review_detail", args=[change_request.pk]))
        self.assertContains(response, reverse("review_approve", args=[change_request.pk]))
        self.assertEqual(response.context["history_url"], reverse("manage_edition_history", args=[self.edition.pk]))


class PermissionsMigrationTests(ComiTestCase):
    def test_grant_and_revoke(self) -> None:
        schema_editor = SimpleNamespace(connection=connection)
        permissions_migration.revoke(apps, schema_editor)
        self.assertFalse(Group.objects.get(name=REVIEWERS).permissions.exists())
        self.assertEqual(
            set(Group.objects.get(name=ADMINISTRATORS).permissions.values_list("codename", flat=True)),
            {"access_management"},
        )
        permissions_migration.grant(apps, schema_editor)
        permissions_migration.grant(apps, schema_editor)  # idempotent
        self.assertEqual(
            set(Group.objects.get(name=REVIEWERS).permissions.values_list("codename", flat=True)),
            {"validate_changerequest"},
        )
        self.assertEqual(
            set(Group.objects.get(name=ADMINISTRATORS).permissions.values_list("codename", flat=True)),
            {"access_management", "validate_changerequest", "approve_changerequest"},
        )

    def test_reversing_the_schema_drops_the_change_request_notifications(self) -> None:
        author = make_user()
        change_request = submit(
            author=author, model=Edition, changes={"notes": "x"}, label="x", reason="r", target=make_edition()
        )
        validate(change_request, user=make_user(role=REVIEWERS))
        other = Notification.objects.create(recipient=author, kind=Notification.Kind.ACHIEVEMENTS_HELD, count=1)
        schema_migration.delete_change_request_notifications(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(Notification.objects.filter(recipient=author)), [other])
