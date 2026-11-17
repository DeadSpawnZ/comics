"""The "Recently added" block of the edition lists: the Comics module shows the latest RECENT_COUNT
editions, flagged like the A-Z rows with what the user owns and hunts; Gestión shows the latest
RECENT_COUNT_LONG. Both only on the unfiltered first page."""

from unittest import mock

from django.urls import reverse

from comics.roles import ADMINISTRATORS
from comics.views.manage_editions import RECENT_COUNT, RECENT_COUNT_LONG

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_user, make_wanted_edition


class RecentEditionsTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.editions = [make_edition() for _ in range(RECENT_COUNT_LONG + 2)]
        self.newest_first = list(reversed(self.editions))

    def recent_pks(self, url_name: str, **params: str) -> list[int]:
        return [edition.pk for edition in self.client.get(reverse(url_name), params).context["recent"]]

    def test_comics_module_lists_the_latest_five(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(RECENT_COUNT, 5)
        self.assertEqual(self.recent_pks("comics_editions"), [e.pk for e in self.newest_first[:RECENT_COUNT]])

    def test_gestion_keeps_the_latest_ten(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.assertEqual(self.recent_pks("manage_editions"), [e.pk for e in self.newest_first[:RECENT_COUNT_LONG]])

    def test_recent_rows_are_flagged_with_what_the_user_owns_and_hunts(self) -> None:
        owned, hunted = self.newest_first[0], self.newest_first[1]
        make_collection(collector=self.user, edition=owned)
        make_wanted_edition(collector=self.user, edition=hunted)
        self.client.force_login(self.user)

        response = self.client.get(reverse("comics_editions"))

        flags = {edition.pk: (edition.owned_count, edition.wanted) for edition in response.context["recent"]}
        self.assertEqual(flags[owned.pk], (1, False))
        self.assertEqual(flags[hunted.pk], (0, True))
        self.assertContains(response, "manage-rows--recent")
        self.assertContains(response, f'action="{reverse("comics_hunt_mark", args=[hunted.pk])}"')

    def test_owned_is_a_trophy_and_every_row_keeps_its_icon_columns(self) -> None:
        once, twice = self.newest_first[0], self.newest_first[1]
        make_collection(collector=self.user, edition=once)
        make_collection(collector=self.user, edition=twice)
        make_collection(collector=self.user, edition=twice)
        self.client.force_login(self.user)

        content = self.client.get(reverse("comics_editions"), {"tipo": "individual"}).content.decode()

        rows = content.count('class="manage-row edition-row"')
        self.assertEqual(rows, len(self.editions))
        # Every row has its owned, hunt and view cells, empty or not, so the icons line up.
        for cell in ("edition-row__owned", "edition-row__hunt", "edition-row__view"):
            self.assertEqual(content.count(f'class="{cell}"'), rows)
        self.assertEqual(content.count(">emoji_events</span>"), 2)
        self.assertIn("×2", content)
        self.assertIn('<span class="visually-hidden">You own 2</span>', content)
        self.assertIn('<span class="visually-hidden">You own it</span>', content)

    def test_only_on_the_unfiltered_first_page(self) -> None:
        self.client.force_login(self.user)
        with mock.patch("comics.views.manage_editions.PAGE_SIZE", 2):
            for params in ({"q": "x"}, {"tipo": "individual"}, {"format": "single_issue"}, {"page": "2"}):
                with self.subTest(params=params):
                    response = self.client.get(reverse("comics_editions"), params)
                    self.assertEqual(response.context["recent"], [])
                    self.assertNotContains(response, "manage-rows--recent")
