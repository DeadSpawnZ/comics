"""Collectables: the Article catalogue, each collector's pieces (Collectable), the Collectables
pages (collection, articles, article detail), their admin, and the migration that split the old
ownerless GeekCollectable."""

import datetime
import importlib
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import StateApps
from django.http import HttpResponse
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, reverse_lazy
from PIL import Image

from comics.forms import DUPLICATE_COLLECTABLE, CollectableEntryForm
from comics.models import Article, Collectable
from comics.models import collections as collection_models
from comics.roles import ADMINISTRATORS
from comics.views import collectables as collectables_views

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import (
    image_upload,
    make_article,
    make_collectable,
    make_dealer,
    make_superuser,
    make_user,
)

move_migration = importlib.import_module("comics.migrations.0118_move_geek_collectables")

BUYING = Collectable.TradeChoices.BUYING
SELLING = Collectable.TradeChoices.SELLING


class ArticleImageTests(ComiTestCase):
    """An article only converts its image when a new one is uploaded."""

    def create(self, **fields: object) -> Article:
        return Article.objects.create(name="Spawn figure", **fields)

    def test_new_upload_is_converted_to_jpeg(self) -> None:
        item = self.create(image=image_upload("figure.png"))
        self.assertTrue(item.image.name.startswith("articles/Spawn_figure_"))
        self.assertTrue(item.image.name.endswith(".jpg"))
        with Image.open(item.image.path) as img:
            self.assertEqual((img.format, img.mode), ("JPEG", "RGB"))

    def test_saving_again_keeps_the_image_untouched(self) -> None:
        item = self.create(image=image_upload())
        stored_name = item.image.name

        item.description = "Edited"
        with mock.patch.object(
            collection_models, "generate_image_jpeg", wraps=collection_models.generate_image_jpeg
        ) as convert:
            item.save()
            Article.objects.get(pk=item.pk).save()  # fresh instance, unchanged image

        convert.assert_not_called()
        self.assertEqual(Article.objects.get(pk=item.pk).image.name, stored_name)

    def test_replacing_the_image_converts_the_new_one(self) -> None:
        item = self.create(image=image_upload())
        old_name = item.image.name

        item.image = image_upload("new.png", mode="RGB")
        with mock.patch.object(
            collection_models, "generate_image_jpeg", wraps=collection_models.generate_image_jpeg
        ) as convert:
            item.save()

        convert.assert_called_once()
        self.assertNotEqual(item.image.name, old_name)
        self.assertTrue(item.image.name.endswith(".jpg"))

    def test_without_image_nothing_is_stored(self) -> None:
        item = self.create()
        item.save()
        self.assertFalse(Article.objects.get(pk=item.pk).image)

    def test_defaults_and_ordering(self) -> None:
        make_article(name="Zed")
        first = make_article(name="Alpha", limited_to=500)
        self.assertEqual(first.category, Article.CategoryChoices.OTHER)
        self.assertEqual(list(Article.objects.values_list("name", flat=True)), ["Alpha", "Zed"])
        make_article(name="Alpha")  # two articles may share a name


class CollectableTests(ComiTestCase):
    def setUp(self) -> None:
        self.collector = make_user()
        self.article = make_article()
        self.purchase = make_collectable(collector=self.collector, article=self.article)

    def sale(self, previous: Collectable | None, **fields: object) -> Collectable:
        values = {"collector": self.collector, "article": self.article, "trade_type": SELLING, "amount": Decimal("99")}
        values.update(fields)
        return Collectable(previous_trade=previous, **values)

    def assert_previous_trade_rejected(self, piece: Collectable) -> None:
        with self.assertRaises(ValidationError) as caught:
            piece.full_clean()
        self.assertIn("previous_trade", caught.exception.message_dict)
        with self.assertRaises(ValidationError):
            piece.save()

    def test_sale_of_own_purchase_is_valid(self) -> None:
        sale = self.sale(self.purchase)
        sale.full_clean()
        sale.save()
        self.assertEqual(self.purchase.next_trades.get(), sale)

    def test_piece_cannot_be_its_own_previous_trade(self) -> None:
        self.purchase.previous_trade = self.purchase
        self.assert_previous_trade_rejected(self.purchase)

    def test_previous_trade_of_another_collector_is_rejected(self) -> None:
        other = make_collectable(collector=make_user(), article=self.article)
        self.assert_previous_trade_rejected(self.sale(other))

    def test_previous_trade_of_another_article_is_rejected(self) -> None:
        other = make_collectable(collector=self.collector, article=make_article())
        self.assert_previous_trade_rejected(self.sale(other))

    def test_a_purchase_cannot_have_a_previous_trade(self) -> None:
        other_purchase = self.sale(self.purchase, trade_type=Collectable.TradeChoices.BUYING)
        self.assert_previous_trade_rejected(other_purchase)

    def test_a_purchase_cannot_be_sold_twice(self) -> None:
        self.sale(self.purchase).save()
        second_sale = self.sale(self.purchase, amount=Decimal("5"))
        self.assert_previous_trade_rejected(second_sale)

    def test_editing_the_sale_keeps_it_valid(self) -> None:
        sale = self.sale(self.purchase)
        sale.save()
        sale.notes = "Edited"
        sale.full_clean()
        sale.save()

    def test_limited_to_must_be_positive(self) -> None:
        article = Article(name="Print", limited_to=0)
        with self.assertRaises(ValidationError) as caught:
            article.full_clean()
        self.assertIn("limited_to", caught.exception.message_dict)

    def test_previous_trade_must_be_a_purchase(self) -> None:
        first_sale = self.sale(self.purchase)
        first_sale.save()
        self.assert_previous_trade_rejected(self.sale(first_sale, amount=Decimal("5")))

    def test_owned_by_lists_unsold_purchases_of_the_collector(self) -> None:
        sold = make_collectable(collector=self.collector, article=self.article)
        self.sale(sold).save()
        make_collectable(collector=make_user(), article=self.article)  # someone else's

        self.assertEqual(list(Collectable.objects.owned_by(self.collector)), [self.purchase])

    def test_with_sold_flag_marks_the_purchases_a_sale_points_to(self) -> None:
        sold = make_collectable(collector=self.collector, article=self.article)
        sale = self.sale(sold)
        sale.save()
        flags = dict(Collectable.objects.with_sold_flag().values_list("id", "is_sold"))
        self.assertEqual(flags, {self.purchase.pk: False, sold.pk: True, sale.pk: False})

    def test_sellable_by_keeps_the_current_sales_purchase_only_for_its_collector(self) -> None:
        sale = self.sale(self.purchase)
        sale.save()
        other = make_user()
        own = make_collectable(collector=other, article=self.article)

        mine = Collectable.objects.filter(article=self.article).sellable_by(self.collector, current=sale)
        self.assertEqual(list(mine), [self.purchase])
        # The sale switched to another collector: the first collector's purchase is not offered.
        theirs = Collectable.objects.filter(article=self.article).sellable_by(other, current=sale)
        self.assertEqual(list(theirs), [own])

    def test_duplicate_trade_of_the_same_collector_is_rejected(self) -> None:
        dealer = make_dealer()
        fields = {"article": self.article, "amount": Decimal("10"), "participant": dealer}
        make_collectable(collector=self.collector, **fields)
        make_collectable(collector=make_user(), **fields)  # another collector may record the same trade

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_collectable(collector=self.collector, **fields)


class CollectablesPagesTestCase(ComiTestCase):
    """Shared setup of the Collectables pages: a signed-in collector and another collector."""

    def setUp(self) -> None:
        self.collector = make_user()
        self.other = make_user()
        self.client.force_login(self.collector)

    def get(self, url: str, **params: str) -> HttpResponse:
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response

    def queries(self, url: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            self.get(url)
        return len(captured)

    def sell(self, purchase: Collectable, **fields: object) -> Collectable:
        return make_collectable(
            collector=purchase.collector,
            article=purchase.article,
            trade_type=SELLING,
            previous_trade=purchase,
            **fields,
        )


class CollectablesCollectionPageTests(CollectablesPagesTestCase):
    url = reverse_lazy("collectables")

    def names(self, **params: str) -> list[str]:
        return [piece.article.name for piece in self.get(self.url, **params).context["page_obj"]]

    def test_requires_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_shows_only_the_pieces_the_user_still_owns(self) -> None:
        mine = make_article(name="Batman bust", description="Resin", category=Article.CategoryChoices.FIGURE)
        make_collectable(collector=self.collector, article=mine, participant=make_dealer(name="Comic Shop"))
        self.sell(make_collectable(collector=self.collector, article=make_article(name="Sold pin")))
        make_collectable(collector=self.other, article=make_article(name="Not mine"))
        make_collectable(collector=self.other, article=mine, notes="Note of another collector")

        self.assertEqual(self.names(), ["Batman bust"])
        response = self.get(self.url)
        for text in ["Resin", "Comic Shop", "Figure", reverse("collectables_article_detail", args=[mine.pk])]:
            self.assertContains(response, text)
        for text in ["Not mine", "Sold pin", "Note of another collector"]:
            self.assertNotContains(response, text)

    def test_letter_search_and_category_filter_by_the_article(self) -> None:
        for name, description, category in [
            ("Spawn figure", "", Article.CategoryChoices.FIGURE),
            ("Batman print", "Signed by the Spawn artist", Article.CategoryChoices.PRINT),
            ("Batman pin", "", Article.CategoryChoices.PIN),
        ]:
            article = make_article(name=name, description=description, category=category)
            make_collectable(collector=self.collector, article=article)

        self.assertEqual(self.names(letter="S"), ["Spawn figure"])
        self.assertEqual(self.names(q="spawn"), ["Batman print", "Spawn figure"])
        self.assertEqual(self.names(category="pin"), ["Batman pin"])
        self.assertEqual(self.names(letter="B", q="spawn"), ["Batman print"])
        # An unknown category is ignored instead of hiding everything.
        self.assertEqual(self.names(category="bogus"), ["Batman pin", "Batman print", "Spawn figure"])

    def test_empty_state(self) -> None:
        self.assertContains(self.get(self.url), "No pieces match these filters.")

    def test_is_paginated(self) -> None:
        article = make_article()
        for _ in range(collectables_views.PAGE_LIMIT + 1):
            make_collectable(collector=self.collector, article=article)

        self.assertEqual(len(self.get(self.url).context["page_obj"]), collectables_views.PAGE_LIMIT)
        self.assertEqual(len(self.get(self.url, page="2").context["page_obj"]), 1)

    def test_query_count_does_not_grow_with_the_pieces(self) -> None:
        make_collectable(collector=self.collector, article=make_article(), participant=make_dealer())
        few = self.queries(self.url)
        for _ in range(5):
            make_collectable(collector=self.collector, article=make_article(), participant=make_dealer())
        self.assertEqual(self.queries(self.url), few)

    def test_top_nav_and_side_nav_mark_the_section(self) -> None:
        articles = reverse("collectables_articles")
        for url, side_link in [
            (reverse("collectables"), reverse("collectables")),
            (reverse("collectables_piece_new"), reverse("collectables")),
            (articles, articles),
            (reverse("collectables_article_detail", args=[make_article().pk]), articles),
        ]:
            with self.subTest(url=url):
                content = self.get(url).content.decode()
                self.assertRegex(content, r'md-nav-link active"\s+aria-current="page"\s+href="/collectables/"')
                self.assertRegex(content, rf'is-active"\s+aria-current="page"\s+href="{side_link}"')


class CollectablesArticleListTests(CollectablesPagesTestCase):
    url = reverse_lazy("collectables_articles")

    def names(self, **params: str) -> list[str]:
        return [article.name for article in self.get(self.url, **params).context["page_obj"]]

    def test_requires_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_lists_the_whole_catalogue_with_the_users_owned_counts(self) -> None:
        two = make_article(name="Alpha bust", limited_to=500)
        make_collectable(collector=self.collector, article=two)
        make_collectable(collector=self.collector, article=two)
        self.sell(make_collectable(collector=self.collector, article=two))  # sold: not counted
        one = make_article(name="Beta print")
        make_collectable(collector=self.collector, article=one)
        make_collectable(collector=self.other, article=make_article(name="Gamma pin"))  # only another collector
        make_collectable(collector=self.other, article=two)
        make_article(name="Delta poster")  # nobody owns it

        response = self.get(self.url)
        counts = {article.name: article.owned_count for article in response.context["page_obj"]}
        self.assertEqual(counts, {"Alpha bust": 2, "Beta print": 1, "Delta poster": 0, "Gamma pin": 0})
        self.assertContains(response, "You own 2")
        self.assertContains(response, "You own it", count=1)
        self.assertContains(response, "Limited to 500")

    def test_letter_search_and_category_filters(self) -> None:
        make_article(name="Spawn figure", category=Article.CategoryChoices.FIGURE)
        make_article(name="Batman print", description="A Spawn crossover", category=Article.CategoryChoices.PRINT)
        make_article(name="Batman pin", category=Article.CategoryChoices.PIN)

        self.assertEqual(self.names(letter="B"), ["Batman pin", "Batman print"])
        self.assertEqual(self.names(q="spawn"), ["Batman print", "Spawn figure"])
        self.assertEqual(self.names(category="figure"), ["Spawn figure"])
        self.assertEqual(self.names(letter="B", category="print"), ["Batman print"])

    def test_empty_state(self) -> None:
        self.assertContains(self.get(self.url), "No articles match these filters.")

    def test_query_count_does_not_grow_with_the_catalogue(self) -> None:
        make_collectable(collector=self.collector, article=make_article())
        few = self.queries(self.url)
        for _ in range(5):
            make_collectable(collector=self.collector, article=make_article())
            make_article()
        self.assertEqual(self.queries(self.url), few)


class CollectablesArticleDetailTests(CollectablesPagesTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.article = make_article(
            name="Spawn figure",
            description="Hand painted",
            category=Article.CategoryChoices.FIGURE,
            limited_to=300,
            image=image_upload(),
        )
        self.url = reverse("collectables_article_detail", args=[self.article.pk])

    def test_shows_the_article_and_only_the_users_trades(self) -> None:
        make_collectable(collector=self.collector, article=self.article, amount=Decimal("120.00"), notes="Mint in box")
        sold = make_collectable(collector=self.collector, article=self.article, amount=Decimal("80.00"))
        self.sell(sold, amount=Decimal("150.00"), participant=make_dealer(name="Buyer Shop"))
        make_collectable(collector=self.other, article=self.article, notes="Note of another collector")

        response = self.get(self.url)
        self.assertEqual(response.context["owned_count"], 1)
        trades = response.context["trades"]
        self.assertEqual({trade.collector_id for trade in trades}, {self.collector.pk})
        self.assertEqual([trade.amount for trade in trades], [Decimal("120.00"), Decimal("80.00"), Decimal("150.00")])
        self.assertEqual([trade.is_sold for trade in trades], [False, True, False])
        for text in ["Hand painted", "Figure", "300", self.article.image.url, "Mint in box", "Buyer Shop", "Sold"]:
            self.assertContains(response, text)
        self.assertNotContains(response, "Note of another collector")

    def test_without_trades(self) -> None:
        make_collectable(collector=self.other, article=self.article)
        response = self.get(self.url)
        self.assertEqual(response.context["trades"], [])
        self.assertContains(response, "You have no purchases or sales of this article.")

    def test_missing_article_is_404(self) -> None:
        missing = reverse("collectables_article_detail", args=[self.article.pk + 1000])
        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_requires_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_back_link_only_follows_local_urls(self) -> None:
        local = "/collectables/?letter=S"
        self.assertEqual(self.get(self.url, next=local).context["back_url"], local)
        for unsafe in ["https://evil.example/", "//evil.example/", "javascript:alert(1)"]:
            with self.subTest(next=unsafe):
                response = self.get(self.url, next=unsafe)
                self.assertEqual(response.context["back_url"], reverse("collectables_articles"))

    def test_links_to_a_new_piece_of_this_article(self) -> None:
        url = f"{reverse('collectables_piece_new')}?article={self.article.pk}"
        self.assertContains(self.get(self.url), f'href="{url}"')

    def test_query_count_does_not_grow_with_the_trades(self) -> None:
        self.sell(make_collectable(collector=self.collector, article=self.article, participant=make_dealer()))
        few = self.queries(self.url)
        for _ in range(5):
            purchase = make_collectable(collector=self.collector, article=self.article, participant=make_dealer())
            self.sell(purchase, participant=make_dealer())
        self.assertEqual(self.queries(self.url), few)


class CollectablePieceNewTests(CollectablesPagesTestCase):
    """ "New piece" of Collectables: the signed-in collector registers a purchase or a sale."""

    url = reverse_lazy("collectables_piece_new")

    def setUp(self) -> None:
        super().setUp()
        self.article = make_article(name="Spawn figure", category=Article.CategoryChoices.FIGURE, image=image_upload())
        self.dealer = make_dealer(name="Toy Shop")

    def post(self, **fields: object) -> HttpResponse:
        data = {
            "article": self.article.pk,
            "trade_type": BUYING,
            "trade_date": "2024-05-10",
            "amount": "100.00",
            "valuation": "0",
            "notes": "",
        }
        data.update(fields)
        return self.client.post(self.url, {key: value for key, value in data.items() if value is not None})

    def assert_previous_trade_error(self, response: HttpResponse) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertIn("previous_trade", response.context["form"].errors)
        self.assertFalse(Collectable.objects.filter(trade_type=SELLING).exists())

    def test_requires_login(self) -> None:
        self.client.logout()
        for response in [self.client.get(self.url), self.post()]:
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(Collectable.objects.exists())

    def test_form_for_the_signed_in_collector(self) -> None:
        make_article(name="Spawn print", category=Article.CategoryChoices.PRINT)
        response = self.get(self.url)
        form = response.context["form"]
        self.assertNotIn("collector", form.fields)
        groups = [label for label, _options in form.fields["article"].widget.choices[1:]]
        self.assertEqual(groups, ["Figure", "Print"])
        self.assertEqual(response.context["form_data"]["images"], {str(self.article.pk): self.article.image.url})
        self.assertContains(response, reverse("collectables_api_purchases"))

    def test_collection_page_links_here(self) -> None:
        self.assertContains(self.get(reverse("collectables")), f'href="{self.url}"')

    def test_preselected_article(self) -> None:
        response = self.get(self.url, article=str(self.article.pk))
        self.assertEqual(str(response.context["form"]["article"].value()), str(self.article.pk))
        self.assertEqual(response.context["selected_article"], self.article)
        self.assertContains(response, self.article.image.url)
        # A malformed id preselects nothing and does not fail.
        self.assertIsNone(self.get(self.url, article="abc").context["selected_article"])

    def test_creates_a_purchase_of_the_user(self) -> None:
        response = self.post(participant=self.dealer.pk, notes="Mint in box")
        self.assertRedirects(response, reverse("collectables"), fetch_redirect_response=False)
        piece = Collectable.objects.get()
        self.assertEqual(
            (piece.collector, piece.article, piece.trade_type, piece.amount, piece.participant, piece.notes),
            (self.collector, self.article, BUYING, Decimal("100.00"), self.dealer, "Mint in box"),
        )
        self.assertEqual(LogEntry.objects.get(object_id=str(piece.pk)).change_message, "Creado desde Collectables.")

    def test_a_submitted_collector_is_ignored(self) -> None:
        self.post(collector=self.other.pk)
        self.assertEqual(Collectable.objects.get().collector, self.collector)

    def test_a_purchase_drops_the_previous_trade(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.article)
        self.post(previous_trade=purchase.pk, amount="5")
        self.assertIsNone(Collectable.objects.get(amount=Decimal("5")).previous_trade)

    def test_creates_a_sale_of_an_own_purchase(self) -> None:
        purchase = make_collectable(
            collector=self.collector, article=self.article, trade_date=datetime.date(2024, 1, 1)
        )
        response = self.post(trade_type=SELLING, previous_trade=purchase.pk, amount="250")
        self.assertRedirects(response, reverse("collectables"), fetch_redirect_response=False)
        sale = Collectable.objects.get(trade_type=SELLING)
        self.assertEqual((sale.collector, sale.previous_trade), (self.collector, purchase))
        self.assertFalse(Collectable.objects.owned_by(self.collector).exists())

    def test_a_sale_may_have_no_previous_trade(self) -> None:
        self.post(trade_type=SELLING)
        self.assertIsNone(Collectable.objects.get(trade_type=SELLING).previous_trade)

    def test_sale_of_another_collectors_purchase_is_rejected(self) -> None:
        purchase = make_collectable(collector=self.other, article=self.article)
        self.assert_previous_trade_error(self.post(trade_type=SELLING, previous_trade=purchase.pk))

    def test_sale_of_another_articles_purchase_is_rejected(self) -> None:
        purchase = make_collectable(collector=self.collector, article=make_article())
        self.assert_previous_trade_error(self.post(trade_type=SELLING, previous_trade=purchase.pk))

    def test_sale_of_a_sold_purchase_is_rejected(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.article)
        self.sell(purchase)
        response = self.post(trade_type=SELLING, previous_trade=purchase.pk, amount="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors["previous_trade"], ["This purchase has already been sold."])
        self.assertEqual(Collectable.objects.filter(trade_type=SELLING).count(), 1)

    def test_sale_before_the_purchase_is_rejected(self) -> None:
        purchase = make_collectable(
            collector=self.collector, article=self.article, trade_date=datetime.date(2024, 6, 1)
        )
        response = self.post(trade_type=SELLING, previous_trade=purchase.pk, trade_date="2024-05-10")
        self.assert_previous_trade_error(response)
        self.assertEqual(
            response.context["form"].errors["previous_trade"], ["Choose a purchase made on or before the sale date."]
        )

    def test_a_sale_lists_only_the_purchases_it_can_sell(self) -> None:
        own = make_collectable(collector=self.collector, article=self.article)
        sold = make_collectable(collector=self.collector, article=self.article)
        self.sell(sold)
        make_collectable(collector=self.other, article=self.article)
        make_collectable(collector=self.collector, article=self.article, trade_date=datetime.date(2025, 1, 1))
        response = self.post(trade_type=SELLING, amount="")  # invalid: the form is shown again
        choices = [value for value, _label in response.context["form"].fields["previous_trade"].widget.choices]
        self.assertEqual(choices, ["", own.pk])

    def test_duplicate_piece_is_a_form_error(self) -> None:
        self.post(participant=self.dealer.pk)
        response = self.post(participant=self.dealer.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].non_field_errors(), [str(DUPLICATE_COLLECTABLE)])
        self.assertEqual(Collectable.objects.count(), 1)

    def test_save_and_add_another_carries_the_batch_values(self) -> None:
        response = self.post(
            save_add_another="1", participant=self.dealer.pk, notes="Not carried", trade_date="2024-05-10"
        )
        query = {"trade_date": "2024-05-10", "trade_type": BUYING, "participant": str(self.dealer.pk)}
        self.assertRedirects(response, f"{self.url}?{urlencode(query)}", fetch_redirect_response=False)
        self.assertEqual(Collectable.objects.count(), 1)

        form = self.get(response["Location"]).context["form"]
        self.assertEqual(
            (form["trade_date"].value(), form["trade_type"].value(), str(form["participant"].value())),
            ("2024-05-10", BUYING, str(self.dealer.pk)),
        )
        self.assertIsNone(form["article"].value())
        self.assertFalse(form["notes"].value())


class CollectableEntryFormGestionTests(ComiTestCase):
    """The same form without a fixed collector (Gestión): the collector is a field."""

    def setUp(self) -> None:
        self.collector = make_user()
        self.article = make_article()

    def form(self, **fields: object) -> CollectableEntryForm:
        data = {
            "collector": self.collector.pk,
            "article": self.article.pk,
            "trade_type": BUYING,
            "trade_date": "2024-05-10",
            "amount": "10",
            "valuation": "0",
        }
        data.update(fields)
        return CollectableEntryForm(data)

    def test_saves_for_the_chosen_collector(self) -> None:
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().collector, self.collector)

    def test_sale_must_sell_a_purchase_of_the_chosen_collector(self) -> None:
        purchase = make_collectable(collector=make_user(), article=self.article)
        form = self.form(trade_type=SELLING, previous_trade=purchase.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("previous_trade", form.errors)

    def test_duplicate_uses_the_form_message(self) -> None:
        dealer = make_dealer()
        self.form(participant=dealer.pk).save()
        form = self.form(participant=dealer.pk)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), [str(DUPLICATE_COLLECTABLE)])


class CollectablePurchasesApiTests(CollectablesPagesTestCase):
    """The purchases a sale of an article can point to (collectable_form.js)."""

    url = reverse_lazy("collectables_api_purchases")

    def setUp(self) -> None:
        super().setUp()
        self.article = make_article()

    def ids(self, **params: str) -> list[int]:
        return [item["id"] for item in self.get(self.url, article=str(self.article.pk), **params).json()["results"]]

    def test_requires_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get(self.url, {"article": self.article.pk}).status_code, 302)

    def test_lists_the_unsold_purchases_of_the_user_before_the_date(self) -> None:
        early = make_collectable(
            collector=self.collector,
            article=self.article,
            trade_date=datetime.date(2024, 1, 1),
            amount=Decimal("15.50"),
            participant=make_dealer(name="Shop"),
        )
        late = make_collectable(collector=self.collector, article=self.article, trade_date=datetime.date(2024, 6, 1))
        self.sell(make_collectable(collector=self.collector, article=self.article))
        make_collectable(collector=self.collector, article=make_article())
        make_collectable(collector=self.other, article=self.article)

        self.assertEqual(self.ids(), [early.pk, late.pk])
        self.assertEqual(self.ids(before="2024-03-01"), [early.pk])
        self.assertEqual(self.ids(before="2024-01-01"), [early.pk])
        self.assertEqual(self.ids(before="not-a-date"), [early.pk, late.pk])
        label = self.get(self.url, article=str(self.article.pk)).json()["results"][0]["label"]
        self.assertEqual(label, "2024-01-01 · $15.50 · Shop")

    def test_only_gestion_users_may_ask_about_another_collector(self) -> None:
        mine = make_collectable(collector=self.collector, article=self.article)
        theirs = make_collectable(collector=self.other, article=self.article)
        self.assertEqual(self.ids(collector=str(self.other.pk)), [mine.pk])

        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.assertEqual(self.ids(collector=str(self.other.pk)), [theirs.pk])

    def test_the_sale_being_edited_keeps_its_purchase(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.article)
        sale = self.sell(purchase)
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(current=str(sale.pk)), [purchase.pk])

    def test_a_sale_of_another_collector_lists_nothing(self) -> None:
        theirs = self.sell(make_collectable(collector=self.other, article=self.article))
        self.assertEqual(self.ids(current=str(theirs.pk)), [])

    def test_malformed_article_returns_nothing(self) -> None:
        make_collectable(collector=self.collector, article=self.article)
        for value in ["", "abc"]:
            with self.subTest(article=value):
                self.assertEqual(self.get(self.url, article=value).json(), {"results": []})


class CollectableAdminTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_superuser())
        self.piece = make_collectable(collector=make_user(), article=make_article(), participant=make_dealer())

    def test_pages_load(self) -> None:
        for url in [
            reverse("admin:comics_article_changelist"),
            reverse("admin:comics_article_add"),
            reverse("admin:comics_article_change", args=[self.piece.article.pk]),
            reverse("admin:comics_collectable_changelist"),
            reverse("admin:comics_collectable_add"),
            reverse("admin:comics_collectable_change", args=[self.piece.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class GeekCollectableOwnerTests(ComiTestCase):
    """Who receives the ownerless collectables in 0118."""

    def test_without_owner_or_superuser_it_fails_clearly(self) -> None:
        make_user()
        with self.assertRaisesMessage(RuntimeError, 'no user "DeadSpawnZ" and no superuser'):
            move_migration.owner_id(apps, "default")

    def test_falls_back_to_the_first_superuser(self) -> None:
        make_user()
        first = make_superuser()
        make_superuser()
        self.assertEqual(move_migration.owner_id(apps, "default"), first.pk)

    def test_prefers_deadspawnz(self) -> None:
        make_superuser()
        owner = make_user(username="DeadSpawnZ")
        self.assertEqual(move_migration.owner_id(apps, "default"), owner.pk)


class GeekCollectableReverseTests(ComiTestCase):
    """Reversing 0118 when a purchase has no participant needs a Dealer to stand in."""

    def test_without_any_dealer_it_fails_clearly(self) -> None:
        make_collectable(collector=make_user(), article=make_article())  # no participant
        historical = MigrationLoader(connection).project_state(("comics", "0118_move_geek_collectables")).apps

        with self.assertRaisesMessage(RuntimeError, "there is no Dealer to use instead"):
            move_migration.pieces_to_geek_collectables(historical, SimpleNamespace(connection=connection))
        self.assertTrue(Collectable.objects.exists())  # nothing was deleted


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class GeekCollectableMigrationTests(TransactionTestCase):
    """0117-0119 forward and backward on the test database. Migrating runs DDL, so this runs
    outside a transaction; the database is always left at the latest migration."""

    serialized_rollback = True
    before = [("comics", "0116_edition_mycomicshop_id")]
    created = [("comics", "0117_article_collectable")]
    after = [("comics", "0119_delete_geekcollectable")]

    def migrate(self, targets: list[tuple[str, str]]) -> StateApps:
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_and_backward(self) -> None:
        old = self.migrate(self.before)
        user_model = old.get_model("auth", "User")
        user_model.objects.create(username="admin", is_superuser=True)
        owner = user_model.objects.create(username="DeadSpawnZ")
        dealer = old.get_model("comics", "Dealer").objects.create(name="Dealer")
        geek = old.get_model("comics", "GeekCollectable")
        geek.objects.create(
            name="Spawn figure",
            description="Mint",
            amount=Decimal("350.00"),
            trade_date=datetime.date(2021, 5, 4),
            participant=dealer,
            image="collectables/Spawn_figure_20210504.jpg",
        )
        geek.objects.create(name="Spawn figure", amount=Decimal("10.00"), participant=dealer)  # same name

        new = self.migrate(self.after)
        self.assertNotIn("geekcollectable", {model._meta.model_name for model in new.get_models()})
        pieces = list(new.get_model("comics", "Collectable").objects.select_related("article").order_by("pk"))
        self.assertEqual(len(pieces), 2)
        self.assertEqual(new.get_model("comics", "Article").objects.count(), 2)
        first = pieces[0]
        self.assertEqual(
            (first.collector_id, first.trade_type, first.amount, first.trade_date, first.participant_id),
            (owner.pk, "buying", Decimal("350.00"), datetime.date(2021, 5, 4), dealer.pk),
        )
        self.assertEqual(
            (first.article.name, first.article.description, first.article.category, first.article.image.name),
            ("Spawn figure", "Mint", "other", "collectables/Spawn_figure_20210504.jpg"),
        )
        self.assertFalse(pieces[1].article.image)

        # A sale and a purchase without participant, which GeekCollectable could not hold.
        new_piece = new.get_model("comics", "Collectable")
        new_piece.objects.create(
            collector_id=owner.pk, article_id=first.article_id, trade_type="selling", previous_trade_id=first.pk
        )
        gift = new.get_model("comics", "Article").objects.create(name="Gift pin")
        new_piece.objects.create(collector_id=owner.pk, article=gift, amount=Decimal("0"))

        old = self.migrate(self.created)
        self.assertFalse(old.get_model("comics", "Collectable").objects.exists())
        self.assertFalse(old.get_model("comics", "Article").objects.exists())
        rows = list(old.get_model("comics", "GeekCollectable").objects.order_by("pk"))
        self.assertEqual([row.name for row in rows], ["Spawn figure", "Spawn figure", "Gift pin"])  # no sale
        self.assertEqual(
            (rows[0].description, rows[0].amount, rows[0].image.name),
            ("Mint", Decimal("350.00"), "collectables/Spawn_figure_20210504.jpg"),
        )
        self.assertEqual(rows[2].participant_id, dealer.pk)  # the first Dealer stands in
