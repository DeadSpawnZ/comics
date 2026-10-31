"""A purchase that has been sold cannot be deleted (previous_trade is PROTECT), for comic pieces
(Collection) and collectables (Collectable): the model refuses, the Gestión delete views explain why,
and the delete buttons open the shared dialog as a notice instead of a confirmation."""

import datetime

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.messages import get_messages
from django.db import connection
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape

from comics.models import Artist, Collectable, Collection, Signature
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_article, make_collectable, make_collection, make_edition, make_user

BUYING = Collection.TradeChoices.BUYING
SELLING = Collection.TradeChoices.SELLING
PURCHASE_DATE = datetime.date(2024, 1, 10)
SALE_DATE = datetime.date(2024, 3, 1)
SOLD_REASON = "It was sold on March 1, 2024: delete the sale first."


class SoldPurchaseTestCase(ComiTestCase):
    """A collector with a sold purchase and an unsold one of a comic, and the same with an article."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.collector = make_user()
        self.client.force_login(self.manager)

        self.edition = make_edition(number="7")
        self.purchase = make_collection(collector=self.collector, edition=self.edition, trade_date=PURCHASE_DATE)
        self.sale = make_collection(
            collector=self.collector,
            edition=self.edition,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.purchase,
        )
        self.unsold = make_collection(collector=self.collector, edition=self.edition, trade_date=PURCHASE_DATE)

        self.article = make_article(name="Spawn figure")
        self.piece = make_collectable(collector=self.collector, article=self.article, trade_date=PURCHASE_DATE)
        self.piece_sale = make_collectable(
            collector=self.collector,
            article=self.article,
            trade_type=SELLING,
            trade_date=SALE_DATE,
            previous_trade=self.piece,
        )
        self.unsold_piece = make_collectable(collector=self.collector, article=self.article, trade_date=PURCHASE_DATE)

    def messages(self, response: HttpResponse) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def deletion_logged(self, obj: Collection | Collectable) -> bool:
        return LogEntry.objects.filter(
            object_id=str(obj.pk), content_type__model=obj._meta.model_name, action_flag=DELETION
        ).exists()


class ModelProtectionTests(SoldPurchaseTestCase):
    def test_deleting_a_sold_purchase_raises_protected_error(self) -> None:
        for purchase, sale in [(self.purchase, self.sale), (self.piece, self.piece_sale)]:
            with self.subTest(model=type(purchase).__name__):
                with self.assertRaises(ProtectedError) as raised:
                    purchase.delete()
                self.assertEqual(list(raised.exception.protected_objects), [sale])
                self.assertTrue(type(purchase).objects.filter(pk=purchase.pk).exists())

    def test_deleting_the_sale_keeps_the_purchase(self) -> None:
        for purchase, sale in [(self.purchase, self.sale), (self.piece, self.piece_sale)]:
            with self.subTest(model=type(purchase).__name__):
                sale.delete()
                self.assertTrue(type(purchase).objects.filter(pk=purchase.pk).exists())
                purchase.delete()  # no longer sold, so it can go
                self.assertFalse(type(purchase).objects.filter(pk=purchase.pk).exists())

    def test_with_sale_date_annotates_the_sold_purchases_only(self) -> None:
        for model, purchase, sale, unsold in [
            (Collection, self.purchase, self.sale, self.unsold),
            (Collectable, self.piece, self.piece_sale, self.unsold_piece),
        ]:
            with self.subTest(model=model.__name__):
                sold_on = dict(model.objects.with_sale_date().values_list("pk", "sold_on"))
                self.assertEqual(sold_on, {purchase.pk: SALE_DATE, sale.pk: None, unsold.pk: None})


class CollectionDeleteViewTests(SoldPurchaseTestCase):
    def delete(self, piece: Collection) -> HttpResponse:
        response = self.client.post(reverse("manage_collection_delete", args=[piece.pk]))
        self.assertRedirects(response, reverse("manage_collections"), fetch_redirect_response=False)
        return response

    def sign(self, piece: Collection) -> None:
        Signature.objects.create(artist=Artist.objects.create(name="Todd McFarlane"), collectable=piece)

    def test_a_sold_purchase_is_refused_naming_the_sale(self) -> None:
        response = self.delete(self.purchase)
        self.assertEqual(self.messages(response), [f"“{self.purchase}” cannot be deleted. {SOLD_REASON}"])
        self.assertTrue(Collection.objects.filter(pk=self.purchase.pk).exists())
        self.assertFalse(self.deletion_logged(self.purchase))

    def test_a_signed_piece_keeps_the_signatures_message(self) -> None:
        self.sign(self.unsold)
        response = self.delete(self.unsold)
        expected = f"“{self.unsold}” cannot be deleted: it has signatures registered."
        self.assertEqual(self.messages(response), [expected])
        self.assertTrue(Collection.objects.filter(pk=self.unsold.pk).exists())
        self.assertFalse(self.deletion_logged(self.unsold))

    def test_a_sold_and_signed_purchase_gives_both_reasons(self) -> None:
        self.sign(self.purchase)
        response = self.delete(self.purchase)
        reason = "It was sold on March 1, 2024 and it has signatures registered."
        expected = f"“{self.purchase}” cannot be deleted. {reason}"
        self.assertEqual(self.messages(response), [expected])
        self.assertTrue(Collection.objects.filter(pk=self.purchase.pk).exists())
        self.assertFalse(self.deletion_logged(self.purchase))

    def assert_deleted(self, piece: Collection) -> None:
        response = self.delete(piece)
        self.assertEqual(self.messages(response), [f"Piece deleted: {piece}"])
        self.assertFalse(Collection.objects.filter(pk=piece.pk).exists())
        self.assertTrue(self.deletion_logged(piece))

    def test_the_sale_is_deleted_and_the_purchase_stays(self) -> None:
        self.assert_deleted(self.sale)
        self.assertTrue(Collection.objects.filter(pk=self.purchase.pk).exists())

    def test_an_unsold_purchase_is_deleted(self) -> None:
        self.assert_deleted(self.unsold)


class CollectableDeleteViewTests(SoldPurchaseTestCase):
    def delete(self, piece: Collectable) -> HttpResponse:
        response = self.client.post(reverse("manage_collectable_delete", args=[piece.pk]))
        self.assertRedirects(response, reverse("manage_collectables"), fetch_redirect_response=False)
        return response

    def test_a_sold_purchase_is_refused_naming_the_sale(self) -> None:
        response = self.delete(self.piece)
        self.assertEqual(self.messages(response), [f"“Spawn figure” cannot be deleted. {SOLD_REASON}"])
        self.assertTrue(Collectable.objects.filter(pk=self.piece.pk).exists())
        self.assertFalse(self.deletion_logged(self.piece))

    def test_the_sale_is_deleted_and_the_purchase_stays(self) -> None:
        response = self.delete(self.piece_sale)
        self.assertEqual(self.messages(response), ["Piece deleted: Spawn figure"])
        self.assertFalse(Collectable.objects.filter(pk=self.piece_sale.pk).exists())
        self.assertTrue(Collectable.objects.filter(pk=self.piece.pk).exists())
        self.assertTrue(self.deletion_logged(self.piece_sale))


class DeleteButtonBlockedTests(SoldPurchaseTestCase):
    """The delete buttons of a sold purchase carry data-delete-blocked; every other button does not."""

    BLOCKED = f'data-delete-blocked="{SOLD_REASON}"'

    def get(self, url: str, **params: str) -> HttpResponse:
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response

    def button(self, url_name: str, piece: Collection | Collectable, name: str, blocked: bool) -> str:
        """The data attributes of `piece`'s delete button, as rendered."""
        url = reverse(url_name, args=[piece.pk])
        suffix = f" {self.BLOCKED}>" if blocked else ">"
        return f'data-delete-url="{url}" data-delete-name="{escape(name)}"{suffix}'

    def test_collection_list_marks_only_the_sold_purchase(self) -> None:
        # Filtered by collector so the "recent additions" block does not repeat the rows.
        response = self.get(reverse("manage_collections"), collector=str(self.collector.pk))
        self.assertContains(response, self.BLOCKED, count=1)
        name = str(self.edition)
        self.assertContains(response, self.button("manage_collection_delete", self.purchase, name, True))
        self.assertContains(response, self.button("manage_collection_delete", self.sale, name, False))
        self.assertContains(response, self.button("manage_collection_delete", self.unsold, name, False))

    def test_collectable_list_marks_only_the_sold_purchase(self) -> None:
        response = self.get(reverse("manage_collectables"), collector=str(self.collector.pk))
        self.assertContains(response, self.BLOCKED, count=1)
        name = "Spawn figure"
        self.assertContains(response, self.button("manage_collectable_delete", self.piece, name, True))
        self.assertContains(response, self.button("manage_collectable_delete", self.piece_sale, name, False))
        self.assertContains(response, self.button("manage_collectable_delete", self.unsold_piece, name, False))

    def test_recent_additions_are_marked_too(self) -> None:
        # Unfiltered first page: the sold purchase shows in the recent block and in the A-Z list.
        for url in [reverse("manage_collections"), reverse("manage_collectables")]:
            with self.subTest(url=url):
                self.assertContains(self.get(url), self.BLOCKED, count=2)

    def test_edit_pages_mark_only_the_sold_purchase(self) -> None:
        for url_name, purchase, others in [
            ("manage_collection_edit", self.purchase, [self.sale, self.unsold]),
            ("manage_collectable_edit", self.piece, [self.piece_sale, self.unsold_piece]),
        ]:
            with self.subTest(url_name=url_name):
                self.assertContains(self.get(reverse(url_name, args=[purchase.pk])), self.BLOCKED, count=1)
                for other in others:
                    self.assertNotContains(self.get(reverse(url_name, args=[other.pk])), "data-delete-blocked=")

    def test_the_dialog_can_show_the_reason(self) -> None:
        response = self.get(reverse("manage_collections"))
        for element in ['id="deleteDialogBlocked" hidden', 'id="deleteDialogBlockedReason"', 'id="deleteDialogSubmit"']:
            self.assertContains(response, element)

    def test_the_lists_do_not_query_per_row(self) -> None:
        def queries(url: str) -> int:
            with CaptureQueriesContext(connection) as captured:
                self.get(url, collector=str(self.collector.pk))
            return len(captured)

        urls = [reverse("manage_collections"), reverse("manage_collectables")]
        before = [queries(url) for url in urls]
        for _ in range(3):
            purchase = make_collection(collector=self.collector, edition=self.edition, trade_date=PURCHASE_DATE)
            sale = {"trade_type": SELLING, "trade_date": SALE_DATE}
            make_collection(collector=self.collector, edition=self.edition, previous_trade=purchase, **sale)
            piece = make_collectable(collector=self.collector, article=self.article, trade_date=PURCHASE_DATE)
            make_collectable(collector=self.collector, article=self.article, previous_trade=piece, **sale)
        self.assertEqual([queries(url) for url in urls], before)
