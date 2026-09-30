"""Malformed client input is reported as a validation error, never a server error (point 2)."""

import json

from django.urls import reverse

from comics.forms import CollectionEntryForm
from comics.models import Collection, Connecting, Issue, ReadingArc

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_user

MALFORMED_DATES = ["2024-02-30", "not-a-date", "2024-13-01"]


class CollectionEntryFormMalformedTests(ComiTestCase):
    def setUp(self) -> None:
        self.staff = make_user(staff=True)
        self.edition = make_edition()

    def valid_data(self, **overrides: str) -> dict[str, str]:
        data = {
            "collector": str(self.staff.pk),
            "publishing": str(self.edition.publishing_id),
            "edition": str(self.edition.pk),
            "trade_type": Collection.TradeChoices.BUYING,
            "trade_date": "2020-05-01",
            "amount": "10.00",
            "valuation": "0",
        }
        data.update(overrides)
        return data

    def test_valid_submission_still_saves(self) -> None:
        form = CollectionEntryForm(data=self.valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        piece = form.save()
        self.assertEqual(piece.edition, self.edition)

    def test_non_numeric_ids_are_reported_by_field_validation(self) -> None:
        form = CollectionEntryForm(data=self.valid_data(publishing="abc", edition="xyz", collector="zz"))
        self.assertFalse(form.is_valid())
        self.assertIn("edition", form.errors)
        self.assertIn("collector", form.errors)

    def test_malformed_dates_are_reported_by_field_validation(self) -> None:
        for value in MALFORMED_DATES:
            with self.subTest(trade_date=value):
                form = CollectionEntryForm(data=self.valid_data(trade_date=value))
                self.assertFalse(form.is_valid())
                self.assertIn("trade_date", form.errors)

    def test_malformed_carried_initial_values_are_ignored(self) -> None:
        # "Save and add another" carries values through the query string into `initial`.
        form = CollectionEntryForm(initial={"publishing": "abc", "trade_date": "2024-02-30", "collector": "x"})
        self.assertEqual(form.fields["edition"].widget.choices[0][0], "")

    def test_manage_view_redisplays_the_form_on_malformed_post(self) -> None:
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("manage_collection_new"),
            self.valid_data(publishing="abc", edition="xyz", trade_date="2024-02-30"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Collection.objects.exists())

    def test_purchases_api_ignores_an_impossible_date(self) -> None:
        make_collection(collector=self.staff, edition=self.edition)
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("comics_api_purchases"), {"edition": self.edition.pk, "before": "2024-02-30"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)


class ConnectingEditorMalformedTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(staff=True))
        self.url = reverse("manage_connecting_new")

    def post_json(self, body: str) -> dict:
        response = self.client.post(self.url, body, content_type="application/json")
        self.assertEqual(response.status_code, 400, body)
        return response.json()

    def test_non_object_json_bodies_get_the_400_error(self) -> None:
        for body in ["[]", "1", '"text"', "null", "true", "{not json", '{"name": "W", "pieces": 5}']:
            with self.subTest(body=body):
                self.assertEqual(self.post_json(body), {"errors": ["The request is not valid."]})
        self.assertFalse(Connecting.objects.exists())

    def test_valid_object_is_saved(self) -> None:
        edition = make_edition()
        payload = {
            "name": "Wall",
            "rows": 1,
            "columns": 2,
            "notes": "",
            "pieces": [{"row": 1, "column": 2, "edition": edition.pk}],
        }
        response = self.client.post(self.url, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        connecting = Connecting.objects.get(pk=response.json()["id"])
        self.assertEqual([(p.row, p.column, p.edition_id) for p in connecting.pieces.all()], [(1, 2, edition.pk)])


class ArcFormMalformedTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(staff=True))
        self.issue = make_edition().issue

    def test_unhashable_issue_ids_redisplay_the_form(self) -> None:
        issues = json.dumps([[1], {"a": 1}, "2", True, self.issue.pk])
        for name in ["", "Origins"]:  # invalid form, then invalid issue list
            with self.subTest(name=name):
                response = self.client.post(reverse("manage_arc_new"), {"name": name, "issues": issues})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["issue_errors"] != [], bool(name))
                items = response.context["form_data"]["issues"]
                self.assertEqual(items, [{"id": self.issue.pk, "label": str(self.issue)}])
        self.assertFalse(ReadingArc.objects.exists())

    def test_valid_issue_list_is_saved(self) -> None:
        response = self.client.post(
            reverse("manage_arc_new"), {"name": "Origins", "issues": json.dumps([self.issue.pk])}
        )
        self.assertEqual(response.status_code, 302)
        arc = ReadingArc.objects.get(name="Origins")
        self.assertEqual(list(arc.issues.all()), [Issue.objects.get(pk=self.issue.pk)])
