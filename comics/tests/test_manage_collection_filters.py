"""Filters and sorting of the Gestión collection list: title-only search, participant and status
filters, the "Sort by" select, the tab counters, the "Recently added" block, the Clear link and the
status chips of each row."""

import datetime
import html
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.db import connection
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.models import Collection, Edition, TradeStatus
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import (
    make_article,
    make_collectable,
    make_collection,
    make_dealer,
    make_edition,
    make_publishing,
    make_user,
)

BUYING = Collection.TradeChoices.BUYING
SELLING = Collection.TradeChoices.SELLING
URL = reverse("manage_collections")


class CollectionListFilterTestCase(ComiTestCase):
    """Two collectors, each with an unsold purchase and a sold one (with its sale); one of them also
    has a sale that points to no purchase. Two titles, two dealers in comic trades, one idle dealer
    and one that only trades articles."""

    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)
        self.alice = make_user(username="alice")
        self.bob = make_user(username="bob")

        self.alpha = make_dealer(name="Alpha Comics")
        self.zeta = make_dealer(name="Zeta Store")
        self.idle = make_dealer(name="Idle Dealer")
        self.articles_only = make_dealer(name="Article Dealer")
        make_collectable(collector=self.alice, article=make_article(), participant=self.articles_only)

        batman = make_edition(publishing=make_publishing(publishing_title="Batman"), number="1")
        spawn = make_edition(publishing=make_publishing(publishing_title="Spawn"), number="1")

        def piece(collector: User, edition: Edition, day: datetime.date, amount: int, **fields: Any) -> Collection:
            return make_collection(
                collector=collector, edition=edition, trade_date=day, amount=Decimal(amount), **fields
            )

        sale = {"trade_type": SELLING}
        self.a_owned = piece(self.alice, batman, datetime.date(2024, 1, 10), 10, participant=self.alpha)
        self.a_sold = piece(self.alice, spawn, datetime.date(2024, 2, 1), 30, participant=self.zeta)
        self.a_sale = piece(self.alice, spawn, datetime.date(2024, 5, 1), 50, previous_trade=self.a_sold, **sale)
        # Same date as a_sold and same amount as b_sold, so both sorts need their pk tiebreaker.
        self.b_unlinked = piece(self.bob, batman, datetime.date(2024, 2, 1), 7, participant=self.zeta, **sale)
        self.b_owned = piece(self.bob, batman, datetime.date(2023, 12, 1), 5)
        self.b_sold = piece(self.bob, batman, datetime.date(2023, 11, 1), 7, participant=self.alpha)
        self.b_sale = piece(self.bob, batman, datetime.date(2024, 6, 1), 70, previous_trade=self.b_sold, **sale)
        self.all_pieces = [
            self.a_owned,
            self.a_sold,
            self.a_sale,
            self.b_unlinked,
            self.b_owned,
            self.b_sold,
            self.b_sale,
        ]

    def get(self, **params: str) -> HttpResponse:
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return response

    def listed(self, **params: str) -> list[int]:
        """The pks of the main list (not the "Recently added" block), in order."""
        return [piece.pk for piece in self.get(**params).context["page_obj"].object_list]

    def assert_lists(self, expected: list[Collection], **params: str) -> None:
        self.assertCountEqual(self.listed(**params), [piece.pk for piece in expected])


class SearchTests(CollectionListFilterTestCase):
    def test_search_matches_the_title(self) -> None:
        batman = [self.a_owned, self.b_unlinked, self.b_owned, self.b_sold, self.b_sale]
        self.assert_lists(batman, q="bat")

    def test_search_no_longer_matches_the_participant(self) -> None:
        self.assert_lists([], q="Alpha")

    def test_the_field_is_labelled_search_title(self) -> None:
        response = self.get()
        self.assertContains(response, '<label for="filter-q">Search title</label>', html=True)
        self.assertNotContains(response, "Search title or participant")


class ParticipantFilterTests(CollectionListFilterTestCase):
    def test_filters_by_dealer(self) -> None:
        self.assert_lists([self.a_owned, self.b_sold], participant=str(self.alpha.pk))

    def test_none_lists_the_pieces_without_participant(self) -> None:
        self.assert_lists([self.a_sale, self.b_owned, self.b_sale], participant="none")

    def test_an_unknown_value_is_ignored(self) -> None:
        for value in ["abc", "-1", "None"]:
            with self.subTest(value=value):
                self.assert_lists(self.all_pieces, participant=value)

    def test_selector_lists_only_the_dealers_of_comic_trades(self) -> None:
        participants = list(self.get().context["participants"])
        self.assertEqual(participants, [self.alpha, self.zeta])

    def test_selector_keeps_the_choice(self) -> None:
        response = self.get(participant="none")
        self.assertContains(response, '<option value="none" selected>No participant</option>', html=True)


class StatusFilterTests(CollectionListFilterTestCase):
    def test_each_status(self) -> None:
        expected = {
            TradeStatus.OWNED: [self.a_owned, self.b_owned],
            TradeStatus.SOLD: [self.a_sold, self.b_sold],
            TradeStatus.UNLINKED: [self.b_unlinked],
        }
        for status, pieces in expected.items():
            with self.subTest(status=status):
                self.assert_lists(pieces, status=status.value)

    def test_an_unknown_status_is_ignored(self) -> None:
        self.assert_lists(self.all_pieces, status="lost")

    def test_status_combines_with_the_other_filters(self) -> None:
        self.assert_lists([self.b_sold], status="sold", collector=str(self.bob.pk))
        self.assert_lists([self.a_sold], status="sold", participant=str(self.zeta.pk))

    def test_queryset_method_leaves_the_queryset_alone_for_other_values(self) -> None:
        for status in ["", "lost"]:
            with self.subTest(status=status):
                self.assertEqual(Collection.objects.with_status(status).count(), len(self.all_pieces))


class SortTests(CollectionListFilterTestCase):
    def expected(self, key: Callable[[Collection], Any]) -> list[int]:
        return [piece.pk for piece in sorted(self.all_pieces, key=key)]

    def test_each_sort_option(self) -> None:
        def title(piece: Collection) -> tuple[str, datetime.date, int]:
            # Every piece is #1 of a 2000 publishing: the title, then the date, then the pk decide.
            return (piece.edition.publishing.publishing_title, piece.trade_date, piece.pk)

        orders = {
            "": self.expected(title),
            "date_desc": self.expected(lambda piece: (-piece.trade_date.toordinal(), -piece.pk)),
            "date_asc": self.expected(lambda piece: (piece.trade_date, piece.pk)),
            "amount_desc": self.expected(lambda piece: (-piece.amount, -piece.pk)),
            "amount_asc": self.expected(lambda piece: (piece.amount, piece.pk)),
            "recent": self.expected(lambda piece: -piece.pk),
        }
        for sort, expected in orders.items():
            with self.subTest(sort=sort):
                self.assertEqual(self.listed(sort=sort), expected)

    def test_an_unknown_sort_falls_back_to_title(self) -> None:
        self.assertEqual(self.listed(sort="price"), self.listed())

    def test_select_is_tied_to_the_filters_form(self) -> None:
        response = self.get(sort="amount_asc")
        self.assertContains(response, 'name="sort" form="collection-filters"')
        self.assertContains(response, 'id="collection-filters"')
        self.assertContains(response, '<option value="amount_asc" selected>Amount: lowest first</option>', html=True)


class TabCounterTests(CollectionListFilterTestCase):
    def counts(self, **params: str) -> list[int]:
        return [count for _value, _label, count in self.get(**params).context["tabs"]]

    def test_counters_follow_the_filters(self) -> None:
        cases = [
            ({}, [7, 4, 3]),
            ({"participant": "none"}, [3, 1, 2]),
            ({"status": "owned", "collector": str(self.alice.pk)}, [1, 1, 0]),
            ({"status": "unlinked"}, [1, 0, 1]),
            ({"q": "spawn"}, [2, 1, 1]),
            # The tab itself and the sort do not change the counters.
            ({"tipo": "selling", "sort": "amount_desc"}, [7, 4, 3]),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(self.counts(**params), expected)

    def test_tab_links_keep_the_active_params(self) -> None:
        response = self.get(status="sold", sort="date_desc", participant="none")
        links = re.findall(r'class="manage-tab[^"]*"[^>]*?href="([^"]+)"', response.content.decode(), re.S)
        self.assertEqual(len(links), 3)
        for link in links:
            params = parse_qs(urlsplit(html.unescape(link)).query)
            self.assertEqual(params["status"], ["sold"])
            self.assertEqual(params["sort"], ["date_desc"])
            self.assertEqual(params["participant"], ["none"])

    def test_paginator_keeps_the_active_params(self) -> None:
        # Three pieces without participant (both titles contain "a"), two per page.
        with patch("comics.views.collection_entries.PAGE_SIZE", 2):
            response = self.get(sort="amount_asc", participant="none", q="a")
        links = re.findall(r'class="md-paginator__page" href="([^"]+)"', response.content.decode())
        self.assertEqual(len(links), 1)
        params = parse_qs(urlsplit(html.unescape(links[0])).query)
        self.assertEqual(params, {"sort": ["amount_asc"], "participant": ["none"], "q": ["a"], "page": ["2"]})


class RecentBlockTests(CollectionListFilterTestCase):
    def test_shown_unfiltered_with_the_default_sort(self) -> None:
        for params in [{}, {"sort": ""}, {"sort": "unknown"}, {"participant": "abc"}]:
            with self.subTest(params=params):
                self.assertTrue(self.get(**params).context["recent"])

    def test_hidden_with_any_filter_or_another_sort(self) -> None:
        cases = [
            {"q": "bat"},
            {"collector": str(self.alice.pk)},
            {"participant": "none"},
            {"participant": str(self.alpha.pk)},
            {"status": "sold"},
            {"tipo": "buying"},
            {"sort": "recent"},
            {"sort": "amount_desc"},
        ]
        for params in cases:
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertEqual(response.context["recent"], [])
                self.assertNotContains(response, "manage-rows--recent")


class ClearLinkTests(CollectionListFilterTestCase):
    def clear_link(self, response: HttpResponse) -> str | None:
        match = re.search(r'<a class="md-btn md-btn--text" href="([^"]*)">Clear</a>', response.content.decode())
        return html.unescape(match.group(1)) if match else None

    def test_clears_the_filters_and_keeps_tab_and_sort(self) -> None:
        response = self.get(
            q="bat",
            collector=str(self.bob.pk),
            participant="none",
            status="owned",
            tipo="buying",
            sort="date_desc",
            page="1",
        )
        link = self.clear_link(response)
        self.assertIsNotNone(link)
        self.assertEqual(parse_qs(urlsplit(link).query), {"tipo": ["buying"], "sort": ["date_desc"]})

    def test_shown_for_each_filter(self) -> None:
        for params in [{"q": "bat"}, {"collector": str(self.bob.pk)}, {"participant": "none"}, {"status": "sold"}]:
            with self.subTest(params=params):
                self.assertIsNotNone(self.clear_link(self.get(**params)))

    def test_not_shown_for_the_tab_or_the_sort(self) -> None:
        self.assertIsNone(self.clear_link(self.get(tipo="selling", sort="amount_desc")))


class RowChipTests(CollectionListFilterTestCase):
    SOLD_CHIP = 'aria-hidden="true">check</span>'
    UNLINKED_CHIP = 'aria-hidden="true">link_off</span>'

    def rows(self, response: HttpResponse) -> dict[int, str]:
        """The HTML of each row of the page, by piece pk."""
        rows = {}
        for chunk in response.content.decode().split('<li class="manage-row">')[1:]:
            pk = int(re.search(r"/gestion/colecciones/(\d+)/", chunk).group(1))
            rows[pk] = chunk.split("</li>")[0]
        return rows

    def test_chips_mark_sold_purchases_and_unlinked_sales(self) -> None:
        # Not the default sort, so the "Recently added" block does not repeat the rows.
        rows = self.rows(self.get(sort="recent"))
        self.assertEqual(set(rows), {piece.pk for piece in self.all_pieces})
        for piece in self.all_pieces:
            with self.subTest(piece=piece.pk):
                self.assertEqual(self.SOLD_CHIP in rows[piece.pk], piece in [self.a_sold, self.b_sold])
                self.assertEqual(self.UNLINKED_CHIP in rows[piece.pk], piece == self.b_unlinked)
        self.assertIn("Sold", rows[self.a_sold.pk])
        self.assertIn("No purchase linked", rows[self.b_unlinked.pk])

    def test_rows_do_not_query_per_row(self) -> None:
        params = {"collector": str(self.alice.pk), "status": "", "sort": "amount_desc"}

        def queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                self.get(**params)
            return len(captured)

        before = queries()
        edition = make_edition()
        for day in range(1, 4):
            purchase = make_collection(collector=self.alice, edition=edition, trade_date=datetime.date(2022, 1, day))
            make_collection(
                collector=self.alice,
                edition=edition,
                trade_type=SELLING,
                trade_date=datetime.date(2022, 2, day),
                previous_trade=purchase,
            )
            make_collection(
                collector=self.alice, edition=edition, trade_type=SELLING, trade_date=datetime.date(2022, 3, day)
            )
        self.assertEqual(queries(), before)
