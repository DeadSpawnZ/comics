"""Hunting (Cacería) the missing pieces of a connecting from its page in the Comics module: every
missing piece gets the hunt toggle on the grid and in the "Missing pieces" list, owned pieces get none,
and the hunt state costs a fixed number of queries (none at all on the connecting list)."""

import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from comics.models import Connecting, Edition, Publishing, WantedEdition

from .base import ComiTestCase
from .factories import (
    make_collection,
    make_edition,
    make_editorial,
    make_publishing,
    make_user,
    make_wanted_edition,
)

# The parts of a hunt toggle's form (includes/hunt_button.html): its action, the state it sets and where
# it returns.
ACTION = re.compile(r'action="([^"]+)"')
WANTED = re.compile(r'name="wanted" value="([01])"')
NEXT = re.compile(r'name="next" value="([^"]*)"')


def hunt_forms(content: str) -> dict[str, list[tuple[str, str]]]:
    """{action URL: [(wanted value, next), ...]} for every hunt toggle on the page (the forms with a
    `wanted` field; others, like the logout one, are skipped)."""
    forms: dict[str, list[tuple[str, str]]] = {}
    for chunk in content.split("<form")[1:]:
        form = chunk.split("</form>", 1)[0]
        wanted = WANTED.search(form)
        if wanted:
            forms.setdefault(ACTION.search(form)[1], []).append((wanted[1], NEXT.search(form)[1]))
    return forms


def mark_url(edition: Edition) -> str:
    return reverse("comics_hunt_mark", args=[edition.pk])


def make_piece_edition() -> Edition:
    """An edition in its own publishing, with an editorial (Edition.__str__ reads it)."""
    return make_edition(publishing=make_publishing(editorials=[make_editorial()]))


def make_connecting(editions: list[Edition], name: str = "Wall") -> Connecting:
    """A one-row connecting with `editions` from left to right."""
    wall = Connecting.objects.create(name=name, rows=1, columns=max(len(editions), 1))
    wall.set_pieces([(1, column, edition.pk) for column, edition in enumerate(editions, start=1)])
    return wall


class ConnectingHuntToggleTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)
        self.owned, self.hunted, self.not_hunted, self.owned_hunted = (make_piece_edition() for _ in range(4))
        self.wall = make_connecting([self.owned, self.hunted, self.not_hunted, self.owned_hunted])
        self.url = reverse("comics_connecting_detail", args=[self.wall.pk])
        make_collection(collector=self.user, edition=self.owned)
        make_wanted_edition(collector=self.user, edition=self.hunted)
        # Hunting an edition already owned (a better copy) still leaves no toggle: the piece is not missing.
        make_collection(collector=self.user, edition=self.owned_hunted)
        make_wanted_edition(collector=self.user, edition=self.owned_hunted)
        # Another collector's mark is not the user's hunt.
        make_wanted_edition(collector=make_user(), edition=self.not_hunted)

    def test_missing_pieces_get_the_toggle_on_the_grid_and_in_the_list(self) -> None:
        response = self.client.get(self.url)

        forms = hunt_forms(response.content.decode())
        # Each missing piece shows twice (its grid cell and its row in the list), always returning here.
        self.assertEqual(forms[mark_url(self.hunted)], [("0", self.url)] * 2)
        self.assertEqual(forms[mark_url(self.not_hunted)], [("1", self.url)] * 2)
        self.assertNotIn(mark_url(self.owned), forms)
        self.assertNotIn(mark_url(self.owned_hunted), forms)
        self.assertContains(response, f'aria-label="Stop looking for it: {self.hunted.short_name}"', count=2)
        self.assertContains(response, f'aria-label="Looking for it: {self.not_hunted.short_name}"', count=2)
        self.assertContains(response, 'class="connecting-view__hunt"', count=2)

    def test_pieces_are_flagged_and_the_hunted_missing_ones_counted(self) -> None:
        response = self.client.get(self.url)

        flags = {piece.edition_id: piece.wanted for piece in response.context["pieces"]}
        self.assertEqual(
            flags,
            {self.owned.pk: False, self.hunted.pk: True, self.not_hunted.pk: False, self.owned_hunted.pk: True},
        )
        self.assertEqual(response.context["hunted"], 1)  # the owned piece hunted is not missing
        self.assertContains(response, "You are looking for 1 of 2")

    def test_a_mark_completed_by_a_purchase_is_not_a_hunt(self) -> None:
        WantedEdition.objects.filter(collector=self.user, edition=self.hunted).update(completed_at=timezone.now())

        response = self.client.get(self.url)

        self.assertEqual(hunt_forms(response.content.decode())[mark_url(self.hunted)], [("1", self.url)] * 2)
        self.assertContains(response, "You are looking for 0 of 2")

    def test_marking_from_the_page_comes_back_to_it_hunted(self) -> None:
        response = self.client.post(mark_url(self.not_hunted), {"wanted": "1", "next": self.url}, follow=True)

        self.assertRedirects(response, self.url)
        self.assertEqual(hunt_forms(response.content.decode())[mark_url(self.not_hunted)], [("0", self.url)] * 2)
        self.assertContains(response, "You are looking for 2 of 2")

        response = self.client.post(mark_url(self.hunted), {"wanted": "0", "next": self.url}, follow=True)

        self.assertEqual(hunt_forms(response.content.decode())[mark_url(self.hunted)], [("1", self.url)] * 2)
        self.assertContains(response, "You are looking for 1 of 2")

    def test_a_complete_connecting_has_no_toggle_nor_count(self) -> None:
        for edition in (self.hunted, self.not_hunted):
            make_collection(collector=self.user, edition=edition)

        response = self.client.get(self.url)

        self.assertEqual(hunt_forms(response.content.decode()), {})
        self.assertNotContains(response, "You are looking for")
        self.assertContains(response, "You own every piece of this connecting.")

    def test_a_connecting_without_pieces(self) -> None:
        empty = Connecting.objects.create(name="Empty", rows=1, columns=2)

        response = self.client.get(reverse("comics_connecting_detail", args=[empty.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["hunted"], 0)
        self.assertContains(response, "This connecting has no pieces yet.")


class ConnectingHuntQueryTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)

    def add_pieces(self, wall: Connecting, count: int) -> None:
        """Widen `wall` by `count` pieces: one owned, the rest missing with every other one hunted."""
        start = wall.columns if wall.pieces.exists() else 0
        wall.columns = start + count
        wall.save()
        for offset in range(1, count + 1):
            edition = make_piece_edition()
            wall.pieces.create(row=1, column=start + offset, edition=edition)
            if offset == 1:
                make_collection(collector=self.user, edition=edition)
            elif offset % 2:
                make_wanted_edition(collector=self.user, edition=edition)

    def queries(self, url: str) -> list[str]:
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return [query["sql"] for query in captured]

    def test_the_detail_query_count_does_not_grow_with_the_pieces(self) -> None:
        wall = Connecting.objects.create(name="Wall", rows=1, columns=1)
        url = reverse("comics_connecting_detail", args=[wall.pk])
        self.add_pieces(wall, 3)
        before = len(self.queries(url))

        self.add_pieces(wall, 5)

        self.assertEqual(len(self.queries(url)), before)

    def test_the_list_never_reads_hunt_marks_nor_editorials(self) -> None:
        make_wanted_edition(collector=self.user, edition=make_piece_edition())
        first = Connecting.objects.create(name="First", rows=1, columns=1)
        self.add_pieces(first, 3)
        one_connecting = self.queries(reverse("comics_connectings"))

        second = Connecting.objects.create(name="Second", rows=1, columns=1)
        self.add_pieces(second, 3)
        two_connectings = self.queries(reverse("comics_connectings"))

        # Each connecting still costs its grid and its ownership check, and nothing else.
        self.assertEqual(len(two_connectings), len(one_connecting) + 2)
        tables = (WantedEdition._meta.db_table, Publishing.editorials.through._meta.db_table)
        for sql in two_connectings:
            for table in tables:
                self.assertNotIn(table, sql)
