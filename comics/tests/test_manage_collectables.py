"""Gestión of the Collectables module: the article catalogue (manage_article*) and the pieces of every
collector (manage_collectable*), the "Collectables" submenu of the Gestión side nav, the Gestión home
entries, and the query-string helpers shared with the comics piece forms (views.utils)."""

import datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, reverse_lazy
from PIL import Image

from comics.forms import CollectableEntryForm, CollectionEntryForm
from comics.models import Article, Collectable, Collection
from comics.roles import ADMINISTRATORS
from comics.views.manage_editions import PAGE_SIZE, RECENT_COUNT
from comics.views.utils import carry_over_query, initial_from_query

from .base import ComiTestCase
from .factories import (
    image_upload,
    make_article,
    make_collectable,
    make_dealer,
    make_edition,
    make_user,
)

BUYING = Collectable.TradeChoices.BUYING
SELLING = Collectable.TradeChoices.SELLING
JANUARY = datetime.date(2024, 1, 1)  # before the date the piece forms submit


class ManageCollectablesTestCase(ComiTestCase):
    """A Gestión user (signed in), a plain collector and a helper to read the flash messages."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.collector = make_user()
        self.client.force_login(self.manager)

    def get(self, url: str, **params: str) -> HttpResponse:
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response

    def messages(self, response: HttpResponse) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def queries(self, url: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            self.get(url)
        return len(captured)

    def log(self, obj: Article | Collectable | Collection) -> LogEntry:
        return LogEntry.objects.get(object_id=str(obj.pk), content_type__model=obj._meta.model_name)


class ManageCollectablesPermissionTests(ManageCollectablesTestCase):
    """Every view follows management_required: anonymous users go to the login, collectors get 403."""

    def setUp(self) -> None:
        super().setUp()
        self.article = make_article()
        self.piece = make_collectable(collector=self.collector, article=self.article)

    def pages(self) -> list[str]:
        return [
            reverse("manage_articles"),
            reverse("manage_article_new"),
            reverse("manage_article_edit", args=[self.article.pk]),
            reverse("manage_collectables"),
            reverse("manage_collectable_new"),
            reverse("manage_collectable_edit", args=[self.piece.pk]),
        ]

    def deletions(self) -> list[str]:
        return [
            reverse("manage_collectable_delete", args=[self.piece.pk]),
            reverse("manage_article_delete", args=[self.article.pk]),
        ]

    def test_gestion_users_can_open_every_page(self) -> None:
        for url in self.pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_collectors_get_403(self) -> None:
        self.client.force_login(self.collector)
        for url in self.pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"name": "Changed"}).status_code, 403)
        for url in self.deletions():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(Collectable.objects.filter(pk=self.piece.pk).exists())
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())
        self.assertFalse(Article.objects.filter(name="Changed").exists())

    def test_anonymous_users_go_to_the_login(self) -> None:
        self.client.logout()
        for url in self.pages() + self.deletions():
            with self.subTest(url=url):
                response = self.client.post(url) if "eliminar" in url else self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])
        self.assertEqual(Collectable.objects.count(), 1)

    def test_deletions_only_accept_post(self) -> None:
        for url in self.deletions():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Collectable.objects.count(), 1)

    def test_missing_objects_are_404(self) -> None:
        for name in ["manage_article_edit", "manage_collectable_edit"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[999999])).status_code, 404)
        for name in ["manage_article_delete", "manage_collectable_delete"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[999999])).status_code, 404)


# ---------- Articles ----------


class ManageArticleListTests(ManageCollectablesTestCase):
    url = reverse_lazy("manage_articles")

    def names(self, **params: str) -> list[str]:
        return [article.name for article in self.get(self.url, **params).context["page_obj"]]

    def test_rows_show_image_category_limit_and_piece_count(self) -> None:
        figure = make_article(
            name="Spawn figure", category=Article.CategoryChoices.FIGURE, limited_to=500, image=image_upload()
        )
        make_collectable(collector=self.collector, article=figure)
        make_collectable(collector=self.manager, article=figure)
        make_article(name="Spawn pin", category=Article.CategoryChoices.PIN)

        response = self.get(self.url, q="spawn")
        rows = {article.name: article for article in response.context["page_obj"]}
        self.assertEqual((rows["Spawn figure"].piece_count, rows["Spawn pin"].piece_count), (2, 0))
        self.assertContains(response, figure.image.url)
        self.assertContains(response, "Limited to 500")
        self.assertContains(response, "2 pieces")
        self.assertContains(response, f"{reverse('manage_collectables')}?article={figure.pk}")
        self.assertContains(response, reverse("manage_article_delete", args=[figure.pk]))

    def test_search_and_category_filter(self) -> None:
        make_article(name="Spawn figure", category=Article.CategoryChoices.FIGURE)
        make_article(name="Batman print", category=Article.CategoryChoices.PRINT, description="Spawn's artist")
        make_article(name="Venom pin", category=Article.CategoryChoices.PIN)
        self.assertEqual(self.names(q="spawn"), ["Batman print", "Spawn figure"])  # name or description
        self.assertEqual(self.names(category="pin"), ["Venom pin"])
        self.assertEqual(self.names(q="spawn", category="figure"), ["Spawn figure"])
        # An unknown category is ignored instead of emptying the list.
        self.assertEqual(len(self.names(category="nope")), 3)
        self.assertContains(self.get(self.url, q="nothing-like-this"), "No articles match these filters.")

    def test_recent_additions_only_on_the_unfiltered_first_page(self) -> None:
        articles = [make_article(name=f"Article {index:02d}") for index in range(RECENT_COUNT + 2)]
        recent = self.get(self.url).context["recent"]
        self.assertEqual([item.pk for item in recent], [item.pk for item in reversed(articles)][:RECENT_COUNT])
        self.assertTrue(all(hasattr(item, "piece_count") for item in recent))
        self.assertEqual(self.get(self.url, q="Article").context["recent"], [])

    def test_is_paginated(self) -> None:
        for index in range(PAGE_SIZE + 1):
            make_article(name=f"Article {index:03d}")
        self.assertEqual(len(self.names()), PAGE_SIZE)
        self.assertEqual(self.names(page="2"), [f"Article {PAGE_SIZE:03d}"])

    def test_query_count_does_not_grow_with_the_articles(self) -> None:
        article = make_article(image=image_upload())
        make_collectable(collector=self.collector, article=article)
        few = self.queries(self.url)
        for _ in range(8):
            make_collectable(collector=self.collector, article=make_article(image=image_upload()))
        self.assertEqual(self.queries(self.url), few)


class ManageArticleFormTests(ManageCollectablesTestCase):
    new_url = reverse_lazy("manage_article_new")

    def data(self, **fields: object) -> dict[str, object]:
        values = {"name": "Spawn figure", "category": "figure", "limited_to": "", "description": ""}
        values.update(fields)
        return values

    def test_new_form(self) -> None:
        response = self.get(self.new_url)
        self.assertIsNone(response.context["article"])
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'id="image-edit"')
        self.assertContains(response, "js/custom/article_form.js")

    def test_creates_an_article_with_its_image_converted_to_jpeg(self) -> None:
        back = reverse("manage_articles") + "?category=figure"
        data = self.data(limited_to="300", description="Mint", image=image_upload("figure.png"), next=back)
        response = self.client.post(self.new_url, data)
        self.assertRedirects(response, back, fetch_redirect_response=False)

        article = Article.objects.get()
        self.assertEqual(
            (article.name, article.category, article.limited_to, article.description),
            ("Spawn figure", "figure", 300, "Mint"),
        )
        self.assertTrue(article.image.name.endswith(".jpg"))
        with Image.open(article.image.path) as img:
            self.assertEqual(img.format, "JPEG")
        entry = self.log(article)
        self.assertEqual(
            (entry.user, entry.action_flag, entry.change_message), (self.manager, ADDITION, "Creado desde Gestión.")
        )
        self.assertEqual(self.messages(response), ["Article created: Spawn figure"])

    def test_save_and_continue_opens_the_new_article(self) -> None:
        response = self.client.post(self.new_url, self.data(save_continue="1"))
        article = Article.objects.get()
        next_query = urlencode({"next": reverse("manage_articles")})
        expected = f"{reverse('manage_article_edit', args=[article.pk])}?{next_query}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_editing_without_a_new_image_keeps_the_current_one(self) -> None:
        article = make_article(name="Old name", image=image_upload())
        image_name = article.image.name
        url = reverse("manage_article_edit", args=[article.pk])
        response = self.get(url)
        self.assertContains(response, article.image.url)
        self.assertContains(response, "0 pieces registered")

        response = self.client.post(url, self.data(name="New name", category="print"))
        self.assertRedirects(response, reverse("manage_articles"), fetch_redirect_response=False)
        article.refresh_from_db()
        self.assertEqual((article.name, article.category, article.image.name), ("New name", "print", image_name))
        self.assertEqual(self.log(article).action_flag, CHANGE)
        self.assertEqual(self.messages(response), ["Article saved: New name"])

    def test_replacing_the_image(self) -> None:
        article = make_article(image=image_upload())
        old_name = article.image.name
        url = reverse("manage_article_edit", args=[article.pk])
        self.client.post(url, self.data(image=image_upload("other.webp", mode="RGB", fmt="WEBP")))
        article.refresh_from_db()
        self.assertNotEqual(article.image.name, old_name)
        self.assertTrue(article.image.name.endswith(".jpg"))

    def test_invalid_input_redisplays_the_form(self) -> None:
        not_an_image = SimpleUploadedFile("figure.png", b"not an image", content_type="image/png")
        response = self.client.post(self.new_url, self.data(name="", limited_to="0", image=not_an_image))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["form"].errors), {"name", "limited_to", "image"})
        self.assertContains(response, "Check the highlighted fields.")
        self.assertFalse(Article.objects.exists())

    def test_back_url_only_follows_local_urls(self) -> None:
        response = self.client.post(self.new_url, self.data(next="https://evil.example/"))
        self.assertRedirects(response, reverse("manage_articles"), fetch_redirect_response=False)


class ManageArticleDeleteTests(ManageCollectablesTestCase):
    def test_deletes_an_article_without_pieces(self) -> None:
        article = make_article(name="Spawn pin")
        back = reverse("manage_articles") + "?q=spawn"
        response = self.client.post(reverse("manage_article_delete", args=[article.pk]), {"next": back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertFalse(Article.objects.exists())
        entry = LogEntry.objects.get(object_id=str(article.pk), content_type__model="article")
        self.assertEqual((entry.action_flag, entry.change_message), (DELETION, "Eliminado desde Gestión."))
        self.assertEqual(self.messages(response), ["Article deleted: Spawn pin"])

    def test_an_article_with_pieces_is_kept_with_a_clear_message(self) -> None:
        article = make_article(name="Spawn figure")
        make_collectable(collector=self.collector, article=article)
        make_collectable(collector=self.manager, article=article)
        response = self.client.post(reverse("manage_article_delete", args=[article.pk]))
        self.assertRedirects(response, reverse("manage_articles"), fetch_redirect_response=False)
        self.assertTrue(Article.objects.filter(pk=article.pk).exists())
        self.assertEqual(Collectable.objects.count(), 2)
        self.assertEqual(self.messages(response), ["“Spawn figure” cannot be deleted: 2 pieces of it are registered."])
        # The history entry is rolled back with the failed deletion.
        self.assertFalse(LogEntry.objects.filter(object_id=str(article.pk), action_flag=DELETION).exists())


# ---------- Pieces ----------


class ManageCollectableListTests(ManageCollectablesTestCase):
    url = reverse_lazy("manage_collectables")

    def setUp(self) -> None:
        super().setUp()
        self.figure = make_article(name="Spawn figure", image=image_upload())
        self.pin = make_article(name="Venom pin")
        self.shop = make_dealer(name="Toy Shop")

    def ids(self, **params: str) -> list[int]:
        return [piece.pk for piece in self.get(self.url, **params).context["page_obj"]]

    def test_lists_every_collectors_pieces(self) -> None:
        own = make_collectable(collector=self.manager, article=self.pin)
        other = make_collectable(
            collector=self.collector, article=self.figure, participant=self.shop, amount=Decimal("120.50")
        )
        response = self.get(self.url)
        self.assertEqual(self.ids(), [other.pk, own.pk])  # by article name
        self.assertContains(response, "$120.50 MXN")
        self.assertContains(response, "Toy Shop")
        self.assertContains(response, self.collector.username)
        self.assertContains(response, self.figure.image.url)
        self.assertContains(response, reverse("manage_collectable_edit", args=[other.pk]))
        self.assertContains(response, reverse("manage_collectable_delete", args=[other.pk]))

    def test_filters_by_collector_article_search_and_type(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.figure, participant=self.shop)
        sale = make_collectable(
            collector=self.collector, article=self.figure, trade_type=SELLING, previous_trade=purchase
        )
        other_collector = make_collectable(collector=self.manager, article=self.figure)
        pin = make_collectable(collector=self.collector, article=self.pin)

        self.assertEqual(set(self.ids(collector=str(self.collector.pk))), {purchase.pk, sale.pk, pin.pk})
        self.assertEqual(set(self.ids(article=str(self.pin.pk))), {pin.pk})
        self.assertEqual(set(self.ids(q="venom")), {pin.pk})
        self.assertEqual(set(self.ids(q="toy shop")), {purchase.pk})  # participant
        self.assertEqual(set(self.ids(tipo=SELLING)), {sale.pk})
        self.assertEqual(
            set(self.ids(collector=str(self.collector.pk), article=str(self.figure.pk), tipo=BUYING)), {purchase.pk}
        )
        self.assertEqual(set(self.ids(collector=str(self.manager.pk))), {other_collector.pk})
        # Malformed ids are ignored.
        self.assertEqual(len(self.ids(collector="x", article="y")), 4)

        tabs = self.get(self.url, collector=str(self.collector.pk)).context["tabs"]
        self.assertEqual([count for _value, _label, count in tabs], [3, 2, 1])

    def test_new_piece_link_keeps_the_article_filter(self) -> None:
        response = self.get(self.url, article=str(self.pin.pk))
        self.assertContains(response, f"{reverse('manage_collectable_new')}?article={self.pin.pk}&amp;next=")
        self.assertContains(response, f'<option value="{self.pin.pk}" selected>Venom pin</option>', html=True)

    def test_recent_additions_only_on_the_unfiltered_first_page(self) -> None:
        pieces = [make_collectable(collector=self.collector, article=self.pin) for _ in range(RECENT_COUNT + 1)]
        recent = self.get(self.url).context["recent"]
        self.assertEqual([piece.pk for piece in recent], [piece.pk for piece in reversed(pieces)][:RECENT_COUNT])
        self.assertEqual(self.get(self.url, tipo=BUYING).context["recent"], [])
        self.assertEqual(self.get(self.url, article=str(self.pin.pk)).context["recent"], [])

    def test_empty_state_and_pagination(self) -> None:
        self.assertContains(self.get(self.url, q="nothing"), "No pieces match these filters.")
        for _ in range(PAGE_SIZE + 1):
            make_collectable(collector=self.collector, article=self.pin)
        self.assertEqual(len(self.ids()), PAGE_SIZE)
        self.assertEqual(len(self.ids(page="2")), 1)

    def test_query_count_does_not_grow_with_the_pieces(self) -> None:
        make_collectable(collector=self.collector, article=self.figure, participant=self.shop)
        few = self.queries(self.url)
        for _ in range(8):
            make_collectable(
                collector=make_user(), article=make_article(image=image_upload()), participant=make_dealer()
            )
        self.assertEqual(self.queries(self.url), few)


class ManageCollectableFormTests(ManageCollectablesTestCase):
    new_url = reverse_lazy("manage_collectable_new")

    def setUp(self) -> None:
        super().setUp()
        self.article = make_article(name="Spawn figure", image=image_upload())
        self.shop = make_dealer(name="Toy Shop")

    def data(self, **fields: object) -> dict[str, object]:
        values = {
            "collector": self.collector.pk,
            "article": self.article.pk,
            "trade_type": BUYING,
            "trade_date": "2024-05-10",
            "amount": "100.00",
            "valuation": "0",
            "notes": "",
        }
        values.update(fields)
        return {key: value for key, value in values.items() if value is not None}

    def test_new_form_has_the_collector_field_and_defaults_to_the_signed_in_user(self) -> None:
        response = self.get(self.new_url, article=str(self.article.pk))
        form = response.context["form"]
        self.assertIsInstance(form, CollectableEntryForm)
        self.assertIn("collector", form.fields)
        self.assertEqual(form["collector"].value(), self.manager.pk)
        self.assertEqual(str(form["article"].value()), str(self.article.pk))
        self.assertEqual(response.context["selected_article"], self.article)
        self.assertContains(response, "js/custom/collectable_form.js")
        self.assertContains(response, reverse("collectables_api_purchases"))

    def test_creates_a_piece_for_another_collector(self) -> None:
        back = reverse("manage_collectables") + "?tipo=buying"
        response = self.client.post(self.new_url, self.data(participant=self.shop.pk, next=back))
        self.assertRedirects(response, back, fetch_redirect_response=False)
        piece = Collectable.objects.get()
        self.assertEqual((piece.collector, piece.article, piece.participant), (self.collector, self.article, self.shop))
        entry = self.log(piece)
        self.assertEqual(
            (entry.user, entry.action_flag, entry.change_message), (self.manager, ADDITION, "Creado desde Gestión.")
        )
        self.assertEqual(self.messages(response), ["Piece added: Spawn figure"])

    def test_links_a_sale_to_the_chosen_collectors_purchase(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.article, trade_date=JANUARY)
        response = self.client.post(self.new_url, self.data(trade_type=SELLING, previous_trade=purchase.pk))
        self.assertEqual(response.status_code, 302)
        sale = Collectable.objects.get(trade_type=SELLING)
        self.assertEqual((sale.collector, sale.previous_trade), (self.collector, purchase))

    def test_a_sale_of_another_collectors_purchase_is_rejected(self) -> None:
        purchase = make_collectable(collector=self.manager, article=self.article, trade_date=JANUARY)
        response = self.client.post(self.new_url, self.data(trade_type=SELLING, previous_trade=purchase.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn("previous_trade", response.context["form"].errors)
        self.assertFalse(Collectable.objects.filter(trade_type=SELLING).exists())

    def test_save_and_add_another_carries_the_batch_and_the_collector(self) -> None:
        back = reverse("manage_collectables")
        response = self.client.post(self.new_url, self.data(save_add_another="1", participant=self.shop.pk, next=back))
        carried = {
            "trade_date": "2024-05-10",
            "trade_type": BUYING,
            "participant": str(self.shop.pk),
            "collector": str(self.collector.pk),
        }
        expected = f"{self.new_url}?{urlencode(carried)}&{urlencode({'next': back})}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        form = self.get(response["Location"]).context["form"]
        self.assertEqual(str(form["collector"].value()), str(self.collector.pk))
        self.assertIsNone(form["article"].value())

    def test_save_and_continue_opens_the_piece(self) -> None:
        response = self.client.post(self.new_url, self.data(save_continue="1"))
        piece = Collectable.objects.get()
        next_query = urlencode({"next": reverse("manage_collectables")})
        expected = f"{reverse('manage_collectable_edit', args=[piece.pk])}?{next_query}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_edits_a_sale_keeping_its_purchase(self) -> None:
        purchase = make_collectable(collector=self.collector, article=self.article, trade_date=JANUARY)
        sale = make_collectable(
            collector=self.collector,
            article=self.article,
            trade_type=SELLING,
            previous_trade=purchase,
            trade_date=datetime.date(2024, 3, 1),
        )
        url = reverse("manage_collectable_edit", args=[sale.pk])
        response = self.get(url)
        choices = [value for value, _label in response.context["form"].fields["previous_trade"].widget.choices]
        self.assertIn(purchase.pk, choices)
        self.assertEqual(response.context["form_data"]["current"], sale.pk)
        self.assertContains(response, reverse("manage_collectable_delete", args=[sale.pk]))

        response = self.client.post(url, self.data(trade_type=SELLING, previous_trade=purchase.pk, amount="150"))
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual((sale.amount, sale.previous_trade), (Decimal("150"), purchase))
        entry = self.log(sale)
        self.assertEqual((entry.action_flag, entry.change_message), (CHANGE, "Modificado desde Gestión."))
        self.assertEqual(self.messages(response), ["Piece saved: Spawn figure"])

    def test_duplicate_piece_is_a_form_error(self) -> None:
        make_collectable(
            collector=self.collector,
            article=self.article,
            trade_date=datetime.date(2024, 5, 10),
            amount=Decimal("100.00"),
            participant=self.shop,
        )
        response = self.client.post(self.new_url, self.data(participant=self.shop.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(Collectable.objects.count(), 1)


class ManageCollectableDeleteTests(ManageCollectablesTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.article = make_article(name="Spawn figure")

    def test_deletes_another_collectors_piece(self) -> None:
        piece = make_collectable(collector=self.collector, article=self.article)
        back = reverse("manage_collectables") + "?collector=" + str(self.collector.pk)
        response = self.client.post(reverse("manage_collectable_delete", args=[piece.pk]), {"next": back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertFalse(Collectable.objects.exists())
        entry = LogEntry.objects.get(object_id=str(piece.pk), content_type__model="collectable")
        self.assertEqual((entry.action_flag, entry.change_message), (DELETION, "Eliminado desde Gestión."))
        self.assertEqual(self.messages(response), ["Piece deleted: Spawn figure"])

    def test_deleting_a_sold_purchase_keeps_the_sale_unlinked(self) -> None:
        # Same as the comics collection: previous_trade is SET_NULL, so the sale stays.
        purchase = make_collectable(collector=self.collector, article=self.article)
        sale = make_collectable(
            collector=self.collector, article=self.article, trade_type=SELLING, previous_trade=purchase
        )
        response = self.client.post(reverse("manage_collectable_delete", args=[purchase.pk]))
        self.assertRedirects(response, reverse("manage_collectables"), fetch_redirect_response=False)
        sale.refresh_from_db()
        self.assertIsNone(sale.previous_trade)
        self.assertEqual(list(Collectable.objects.all()), [sale])


# ---------- Side nav, Gestión home ----------


class ManageSideNavCollectablesTests(ManageCollectablesTestCase):
    """The "Collectables" submenu after "Announcements": a labelled group with two links."""

    def nav(self, url: str) -> str:
        content = self.get(url).content.decode()
        start = content.index('class="md-side-nav"')
        return content[start : content.index("</nav>", start)]

    def active_links(self, url: str) -> list[str]:
        nav = self.nav(url)
        chunks = nav.split('class="md-side-nav__link is-active"')[1:]
        return [chunk.split('href="', 1)[1].split('"', 1)[0] for chunk in chunks]

    def test_submenu_follows_announcements(self) -> None:
        nav = self.nav(reverse("manage_home"))
        announcements = nav.index(f'href="{reverse("manage_announcements")}"')
        group = nav.index('class="md-side-nav__group')
        articles = nav.index(f'href="{reverse("manage_articles")}"')
        pieces = nav.index(f'href="{reverse("manage_collectables")}"')
        self.assertLess(announcements, group)
        self.assertLess(group, articles)
        self.assertLess(articles, pieces)
        self.assertIn('id="manage-nav-collectables"', nav)
        self.assertIn('aria-labelledby="manage-nav-collectables"', nav)
        self.assertNotIn("has-active", nav)

    def test_children_are_active_on_their_pages(self) -> None:
        article = make_article()
        piece = make_collectable(collector=self.collector, article=article)
        articles, pieces = reverse("manage_articles"), reverse("manage_collectables")
        cases = {
            reverse("manage_articles"): articles,
            reverse("manage_article_new"): articles,
            reverse("manage_article_edit", args=[article.pk]): articles,
            reverse("manage_collectables"): pieces,
            reverse("manage_collectable_new"): pieces,
            reverse("manage_collectable_edit", args=[piece.pk]): pieces,
        }
        for url, active in cases.items():
            with self.subTest(url=url):
                # Only that child is active (not Collections nor Reading arcs, whose names overlap).
                self.assertEqual(self.active_links(url), [active])
                nav = self.nav(url)
                self.assertIn("md-side-nav__group has-active", nav)
                self.assertEqual(nav.count('aria-current="page"'), 1)

    def test_other_pages_keep_their_own_active_link(self) -> None:
        self.assertEqual(self.active_links(reverse("manage_collections")), [reverse("manage_collections")])
        self.assertEqual(self.active_links(reverse("manage_arcs")), [reverse("manage_arcs")])


class ManageHomeCollectablesTests(ManageCollectablesTestCase):
    def test_stats_and_history_link_to_the_gestion_pages(self) -> None:
        article = make_article(name="Spawn figure")
        make_collectable(collector=self.collector, article=article)
        edit_url = reverse("manage_article_edit", args=[article.pk])
        self.client.post(edit_url, {"name": "Spawn figure", "category": "figure"})
        response = self.get(reverse("manage_home"))
        stats = {url_name: count for _label, count, url_name, _icon in response.context["stats"]}
        self.assertEqual((stats["manage_articles"], stats["manage_collectables"]), (1, 1))
        self.assertContains(response, f"{reverse('manage_article_edit', args=[article.pk])}?next=")
        filters = [value for value, _label in response.context["type_filters"]]
        self.assertIn("article", filters)
        self.assertIn("collectable", filters)
        self.assertEqual(len(self.get(reverse("manage_home"), tipo="article").context["days"]), 1)
        self.assertEqual(self.get(reverse("manage_home"), tipo="collectable").context["days"], [])


# ---------- Shared helpers and the comics piece forms ----------


class QueryStringHelperTests(ComiTestCase):
    def test_initial_from_query_keeps_only_the_non_empty_keys(self) -> None:
        request = RequestFactory().get("/", {"trade_date": "2024-05-10", "participant": "", "other": "x"})
        self.assertEqual(
            initial_from_query(request, ("trade_date", "participant", "collector")), {"trade_date": "2024-05-10"}
        )

    def test_carry_over_query_encodes_the_submitted_keys(self) -> None:
        data = {"trade_date": "2024-05-10", "trade_type": "buying", "notes": "x", "participant": ""}
        form = CollectableEntryForm(data)
        self.assertEqual(
            carry_over_query(form, ("trade_date", "trade_type", "participant")),
            urlencode({"trade_date": "2024-05-10", "trade_type": "buying"}),
        )


class ComicsPieceFormsLogOriginTests(ComiTestCase):
    """The comics piece forms still log where a piece was made, now passed explicitly."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.edition = make_edition()
        self.client.force_login(self.manager)

    def data(self, **fields: str) -> dict[str, str]:
        values = {
            "collector": str(self.manager.pk),
            "publishing": str(self.edition.publishing_id),
            "edition": str(self.edition.pk),
            "trade_type": Collection.TradeChoices.BUYING,
            "trade_date": "2020-05-01",
            "amount": "10.00",
            "valuation": "0",
            "signatures-TOTAL_FORMS": "0",
            "signatures-INITIAL_FORMS": "0",
        }
        values.update(fields)
        return values

    def change_message(self) -> str:
        piece = Collection.objects.get()
        return LogEntry.objects.get(object_id=str(piece.pk), content_type__model="collection").change_message

    def test_gestion_form(self) -> None:
        response = self.client.post(reverse("manage_collection_new"), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.change_message(), "Creado desde Gestión.")

    def test_my_comics_form_and_save_and_add_another(self) -> None:
        data = self.data(save_add_another="1")
        del data["collector"]  # not a field of the Mis comics form
        response = self.client.post(reverse("comics_piece_new"), data)
        carried = {"publishing": str(self.edition.publishing_id), "trade_date": "2020-05-01", "trade_type": "buying"}
        self.assertRedirects(
            response, f"{reverse('comics_piece_new')}?{urlencode(carried)}", fetch_redirect_response=False
        )
        self.assertEqual(self.change_message(), "Creado desde Mis comics.")
        form = self.client.get(response["Location"]).context["form"]
        self.assertEqual(form.initial["trade_date"], "2020-05-01")

    def test_fixed_collector_limits_the_purchases_a_sale_can_point_to(self) -> None:
        own = Collection.objects.create(collector=self.manager, edition=self.edition, amount=1)
        other = Collection.objects.create(collector=make_user(), edition=self.edition, amount=2)
        fixed = CollectionEntryForm(collector=self.manager).fields["previous_trade"].queryset
        self.assertEqual(list(fixed), [own])
        # Without a fixed collector (Gestión) every purchase is valid; the model checks the owner.
        free = CollectionEntryForm().fields["previous_trade"].queryset
        self.assertEqual(set(free), {own, other})
