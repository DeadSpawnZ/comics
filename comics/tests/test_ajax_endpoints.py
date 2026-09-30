"""Admin AJAX endpoints (point 3): previous trades without participant, comics by publishing."""

import datetime

from django.urls import reverse

from comics.models import Collection

from .base import ComiTestCase
from .factories import make_collection, make_dealer, make_edition, make_publishing, make_user


class PreviousTradesTests(ComiTestCase):
    def setUp(self) -> None:
        self.staff = make_user(staff=True)
        self.client.force_login(self.staff)
        self.edition = make_edition()
        self.url = reverse("get_previous_trades", args=[self.edition.pk])

    def texts(self, **params: str) -> list[str]:
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return [item["text"] for item in response.json()["results"]]

    def test_purchase_without_participant_is_listed_as_unknown(self) -> None:
        make_collection(collector=self.staff, edition=self.edition, trade_date=datetime.date(2020, 1, 1))
        make_collection(
            collector=self.staff,
            edition=self.edition,
            trade_date=datetime.date(2020, 2, 1),
            participant=make_dealer(name="Comic Shop"),
        )
        texts = self.texts()
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].endswith("|| 2020-01-01 || Unknown"), texts[0])
        self.assertTrue(texts[1].endswith("|| 2020-02-01 || Comic Shop"), texts[1])

    def test_sold_purchases_and_later_purchases_are_excluded(self) -> None:
        sold = make_collection(collector=self.staff, edition=self.edition, trade_date=datetime.date(2020, 1, 1))
        make_collection(
            collector=self.staff,
            edition=self.edition,
            trade_type=Collection.TradeChoices.SELLING,
            trade_date=datetime.date(2020, 3, 1),
            previous_trade=sold,
        )
        make_collection(collector=self.staff, edition=self.edition, trade_date=datetime.date(2021, 1, 1))
        self.assertEqual(self.texts(before="2020-12-31"), [])
        self.assertEqual(len(self.texts()), 1)

    def test_impossible_before_date_is_ignored(self) -> None:
        make_collection(collector=self.staff, edition=self.edition)
        self.assertEqual(len(self.texts(before="2024-02-30")), 1)

    def test_requires_staff(self) -> None:
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self.url).status_code, 302)


class ComicsByPublishingTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(staff=True))

    def test_lists_the_editions_of_the_publishing(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        first = make_edition(publishing=publishing, number="1")
        second = make_edition(publishing=publishing, number="2")
        make_edition()  # another publishing

        response = self.client.get(reverse("get_comics_by_publishing", args=[publishing.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"results": [{"id": first.pk, "text": str(first)}, {"id": second.pk, "text": str(second)}]},
        )

    def test_missing_publishing_is_a_json_404(self) -> None:
        response = self.client.get(reverse("get_comics_by_publishing", args=[999999]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"results": []})
