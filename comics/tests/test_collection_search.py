"""My comics → Collection: the search box, the letter chips above the grid and the paginator above
and below it."""

from django.http import HttpResponse
from django.urls import reverse

from comics.models import Collection, Publishing, Title
from comics.views.collection import PAGE_LIMIT

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_publishing, make_user


class CollectionSearchTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)
        self.batman = self.piece("Batman", "Batman: Year One", "1")
        self.spawn = self.piece("Spawn", "Spawn", "12")
        self.avengers = self.piece("Avengers", "The Avengers", "12")

    def piece(self, title: str, publishing_title: str, number: str) -> Collection:
        publishing = Publishing.objects.filter(publishing_title=publishing_title).first() or make_publishing(
            title=Title.objects.create(name=title), publishing_title=publishing_title
        )
        return make_collection(collector=self.user, edition=make_edition(publishing=publishing, number=number))

    def get(self, **params: str) -> HttpResponse:
        return self.client.get(reverse("comics"), params)

    def listed(self, response: HttpResponse) -> set[int]:
        return {collection.pk for collection in response.context["page_obj"].object_list}

    def test_without_a_search_the_page_opens_on_a(self) -> None:
        response = self.get()
        self.assertEqual(response.context["selected_letter"], "A")
        self.assertEqual(self.listed(response), {self.avengers.pk})

    def test_a_search_looks_through_every_letter(self) -> None:
        response = self.get(q="year one")
        self.assertEqual(response.context["selected_letter"], "")
        self.assertEqual(self.listed(response), {self.batman.pk})
        # The title's name matches too, not only the publishing title.
        self.assertEqual(self.listed(self.get(q="spaw")), {self.spawn.pk})

    def test_a_number_matches_exactly(self) -> None:
        self.assertEqual(self.listed(self.get(q="12")), {self.spawn.pk, self.avengers.pk})
        self.assertEqual(self.listed(self.get(q="1")), {self.batman.pk})

    def test_a_letter_picked_during_a_search_narrows_it(self) -> None:
        response = self.get(q="12", letter="S")
        self.assertEqual(self.listed(response), {self.spawn.pk})

    def test_the_page_renders_the_search_and_two_paginators(self) -> None:
        for number in range(PAGE_LIMIT):
            self.piece("Aquaman", "Aquaman", str(100 + number))
        page = self.get(q="aqua").content.decode()
        self.assertIn('name="q" value="aqua"', page)
        # The country form keeps the search; the search form leaves the letter out.
        self.assertIn('<input type="hidden" name="q" value="aqua">', page)
        self.assertEqual(page.count('<nav class="md-paginator '), 2)
        self.assertIn('<nav class="md-paginator mb-3"', page)
        self.assertIn("col-xl-eighth", page)
