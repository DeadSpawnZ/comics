"""Previous trade rules of comic pieces (Collection), the same as collectables (Collectable): only a
sale has a previous trade, a purchase of the same edition by the same collector that no other sale
has sold yet. Saving a sale locks that purchase (SELECT ... FOR UPDATE), for both models, so two sales
of the same purchase saved at the same time cannot both pass the "already sold" check."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.forms import CollectionEntryForm, CollectionForm
from comics.models import Collectable, Collection
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import (
    make_article,
    make_collectable,
    make_collection,
    make_edition,
    make_superuser,
    make_user,
)

BUYING = Collection.TradeChoices.BUYING
SELLING = Collection.TradeChoices.SELLING
PURCHASE_DATE = datetime.date(2024, 1, 10)
SALE_DATE = datetime.date(2024, 3, 1)
WRONG_PURCHASE = "The previous trade must be a purchase of the same edition by the same collector."
ALREADY_SOLD = "This purchase has already been sold."


class CollectionPreviousTradeTests(ComiTestCase):
    def setUp(self) -> None:
        self.collector = make_user()
        self.edition = make_edition()
        self.purchase = make_collection(collector=self.collector, edition=self.edition, trade_date=PURCHASE_DATE)

    def sale(self, previous: Collection | None, **fields: object) -> Collection:
        values = {
            "collector": self.collector,
            "edition": self.edition,
            "trade_type": SELLING,
            "trade_date": SALE_DATE,
            "amount": Decimal("99"),
        }
        values.update(fields)
        return Collection(previous_trade=previous, **values)

    def assert_previous_trade_rejected(self, piece: Collection, message: str) -> None:
        """Both clean() (forms, admin) and save() refuse it, as a field error on previous_trade."""
        with self.assertRaisesMessage(ValidationError, message) as caught:
            piece.full_clean()
        self.assertEqual(caught.exception.message_dict["previous_trade"], [message])
        with self.assertRaisesMessage(ValidationError, message) as caught:
            piece.save()
        self.assertEqual(caught.exception.message_dict["previous_trade"], [message])

    def test_sale_of_own_purchase_is_valid(self) -> None:
        sale = self.sale(self.purchase)
        sale.full_clean()
        sale.save()
        self.assertEqual(self.purchase.next_trades.get(), sale)

    def test_a_sale_may_have_no_previous_trade(self) -> None:
        sale = self.sale(None)
        sale.full_clean()
        sale.save()

    def test_piece_cannot_be_its_own_previous_trade(self) -> None:
        self.purchase.previous_trade = self.purchase
        self.assert_previous_trade_rejected(self.purchase, "A piece can't be its own previous trade.")

    def test_a_purchase_cannot_have_a_previous_trade(self) -> None:
        other_purchase = self.sale(self.purchase, trade_type=BUYING)
        self.assert_previous_trade_rejected(other_purchase, "Only a sale can have a previous trade.")

    def test_previous_trade_of_another_collector_is_rejected(self) -> None:
        theirs = make_collection(collector=make_user(), edition=self.edition, trade_date=PURCHASE_DATE)
        self.assert_previous_trade_rejected(self.sale(theirs), WRONG_PURCHASE)

    def test_previous_trade_of_another_edition_is_rejected(self) -> None:
        other = make_collection(collector=self.collector, edition=make_edition(), trade_date=PURCHASE_DATE)
        self.assert_previous_trade_rejected(self.sale(other), WRONG_PURCHASE)

    def test_previous_trade_must_be_a_purchase(self) -> None:
        first_sale = self.sale(self.purchase)
        first_sale.save()
        self.assert_previous_trade_rejected(self.sale(first_sale, amount=Decimal("5")), WRONG_PURCHASE)

    def test_a_purchase_cannot_be_sold_twice(self) -> None:
        self.sale(self.purchase).save()
        self.assert_previous_trade_rejected(self.sale(self.purchase, amount=Decimal("5")), ALREADY_SOLD)

    def test_editing_the_sale_keeps_it_valid(self) -> None:
        sale = self.sale(self.purchase)
        sale.save()
        sale.notes = "Edited"
        sale.amount = Decimal("120")
        sale.full_clean()
        sale.save()
        sale.refresh_from_db()
        self.assertEqual((sale.notes, sale.previous_trade), ("Edited", self.purchase))


class PreviousTradeLockTests(ComiTestCase):
    """Saving a sale locks its purchase before the "already sold" check; validating it does not (forms
    validate outside a transaction). The SQL is only asserted where the database locks rows (MySQL)."""

    def setUp(self) -> None:
        self.collector = make_user()
        self.purchase = make_collection(collector=self.collector, edition=make_edition(), trade_date=PURCHASE_DATE)
        self.piece = make_collectable(collector=self.collector, article=make_article(), trade_date=PURCHASE_DATE)

    def comic_sale(self) -> Collection:
        return Collection(
            collector=self.collector,
            edition=self.purchase.edition,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.purchase,
        )

    def collectable_sale(self) -> Collectable:
        return Collectable(
            collector=self.collector,
            article=self.piece.article,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.piece,
        )

    def locking_queries(self, queries: CaptureQueriesContext) -> list[str]:
        return [query["sql"] for query in queries.captured_queries if "FOR UPDATE" in query["sql"]]

    @skipUnlessDBFeature("has_select_for_update")
    def test_saving_a_sale_locks_its_purchase(self) -> None:
        for sale, purchase in [(self.comic_sale(), self.purchase), (self.collectable_sale(), self.piece)]:
            with self.subTest(model=type(sale).__name__), CaptureQueriesContext(connection) as queries:
                sale.save()
            locked = self.locking_queries(queries)
            quote = connection.ops.quote_name
            # The purchase row first, then the other sales of it (read the latest committed rows).
            self.assertEqual(len(locked), 2, locked)
            self.assertIn(f"{quote(type(sale)._meta.db_table)}.{quote('id')} = {purchase.pk}", locked[0])
            self.assertIn(f"{quote('previous_trade_id')} = {purchase.pk}", locked[1])

    @skipUnlessDBFeature("has_select_for_update")
    def test_validating_a_sale_does_not_lock(self) -> None:
        for sale in [self.comic_sale(), self.collectable_sale()]:
            with self.subTest(model=type(sale).__name__), CaptureQueriesContext(connection) as queries:
                sale.full_clean()
            self.assertEqual(self.locking_queries(queries), [])

    @skipUnlessDBFeature("has_select_for_update")
    def test_a_piece_without_previous_trade_locks_nothing(self) -> None:
        with CaptureQueriesContext(connection) as queries:
            make_collection(collector=self.collector, edition=self.purchase.edition, trade_date=SALE_DATE)
            make_collectable(collector=self.collector, article=self.piece.article, trade_date=SALE_DATE)
        self.assertEqual(self.locking_queries(queries), [])


class CollectionPreviousTradeFormsTests(ComiTestCase):
    """The forms that create or edit comic pieces explain the previous trade rules on the field."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.collector = make_user()
        self.edition = make_edition()
        self.purchase = make_collection(collector=self.collector, edition=self.edition, trade_date=PURCHASE_DATE)
        self.client.force_login(self.manager)

    def data(self, **fields: object) -> dict[str, object]:
        values: dict[str, object] = {
            "collector": self.collector.pk,
            "publishing": self.edition.publishing_id,
            "edition": self.edition.pk,
            "trade_type": SELLING,
            "trade_date": SALE_DATE.isoformat(),
            "amount": "50",
            "valuation": "0",
            "previous_trade": self.purchase.pk,
            "signatures-TOTAL_FORMS": "0",
            "signatures-INITIAL_FORMS": "0",
        }
        values.update(fields)
        return values

    def test_gestion_form_creates_a_sale_and_edits_it(self) -> None:
        response = self.client.post(reverse("manage_collection_new"), self.data())
        self.assertEqual(response.status_code, 302)
        sale = Collection.objects.get(trade_type=SELLING)
        self.assertEqual(sale.previous_trade, self.purchase)

        response = self.client.post(reverse("manage_collection_edit", args=[sale.pk]), self.data(notes="Edited"))
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual((sale.notes, sale.previous_trade), ("Edited", self.purchase))

    def test_gestion_form_explains_each_rule_once(self) -> None:
        theirs = make_collection(collector=make_user(), edition=self.edition, trade_date=PURCHASE_DATE)
        make_collection(
            collector=self.collector,
            edition=self.edition,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.purchase,
        )
        cases = [
            (theirs.pk, SALE_DATE, WRONG_PURCHASE),
            (self.purchase.pk, SALE_DATE, ALREADY_SOLD),
            (theirs.pk, datetime.date(2024, 1, 1), "Choose a purchase made on or before the sale date."),
        ]
        for previous, sale_date, message in cases:
            with self.subTest(message=message):
                data = self.data(previous_trade=previous, trade_date=sale_date.isoformat(), amount="7")
                response = self.client.post(reverse("manage_collection_new"), data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["form"].errors["previous_trade"], [message])
        self.assertEqual(Collection.objects.filter(trade_type=SELLING).count(), 1)

    def test_gestion_form_moving_a_sale_to_another_collector_shows_one_error(self) -> None:
        sale = make_collection(
            collector=self.collector,
            edition=self.edition,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.purchase,
        )
        form = CollectionEntryForm(data=self.data(collector=make_user().pk, amount=str(sale.amount)), instance=sale)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["previous_trade"], [WRONG_PURCHASE])

    def test_my_comics_form_sells_an_own_purchase(self) -> None:
        self.client.force_login(self.collector)
        data = self.data()
        del data["collector"]  # not a field of the Mis comics form
        response = self.client.post(reverse("comics_piece_new"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Collection.objects.get(trade_type=SELLING).previous_trade, self.purchase)

    def test_admin_rejects_another_collectors_purchase_on_the_field(self) -> None:
        self.client.force_login(make_superuser())
        theirs = make_collection(collector=make_user(), edition=self.edition, trade_date=PURCHASE_DATE)
        data = self.data(previous_trade=theirs.pk, participant="", notes="")
        data.update({"signature_set-TOTAL_FORMS": "0", "signature_set-INITIAL_FORMS": "0"})
        response = self.client.post(reverse("admin:comics_collection_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["adminform"].form.errors["previous_trade"], [WRONG_PURCHASE])
        self.assertFalse(Collection.objects.filter(trade_type=SELLING).exists())

    def test_admin_form_edit_lists_only_the_collectors_purchases(self) -> None:
        make_collection(collector=make_user(), edition=self.edition, trade_date=PURCHASE_DATE)
        sale = make_collection(collector=self.collector, edition=self.edition, trade_type=SELLING, trade_date=SALE_DATE)
        form = CollectionForm(instance=sale)
        self.assertEqual(list(form.fields["previous_trade"].queryset), [self.purchase])

    def test_admin_ajax_filters_by_the_collector_of_the_sale(self) -> None:
        self.client.force_login(make_superuser())
        theirs = make_collection(collector=make_user(), edition=self.edition, trade_date=PURCHASE_DATE)
        url = reverse("get_previous_trades", args=[self.edition.pk])

        def ids(**params: object) -> set[int]:
            return {item["id"] for item in self.client.get(url, params).json()["results"]}

        self.assertEqual(ids(collector=self.collector.pk), {self.purchase.pk})
        self.assertEqual(ids(), {self.purchase.pk, theirs.pk})  # no collector chosen yet
        self.assertEqual(ids(collector="abc"), {self.purchase.pk, theirs.pk})  # malformed: ignored
