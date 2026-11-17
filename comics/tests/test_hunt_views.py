"""Hunt (Cacería) pages in the Comics module: the list of hunted editions, marking and unmarking an
edition from the edition list and detail, and editing the note of a mark. Marks completed by a
purchase are not shown or edited as hunted anywhere."""

import datetime
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.db import connection
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from comics.models import Collection, Edition, WantedEdition

from .base import ComiTestCase
from .factories import (
    make_collection,
    make_edition,
    make_editorial,
    make_publishing,
    make_user,
    make_wanted_edition,
)

NOTE_LIMIT = 500


def note_field(mark: WantedEdition) -> str:
    """Name of the note field of `mark`'s form on the Hunt page (each row has its own prefix)."""
    return f"note-{mark.pk}-notes"


def completed_mark(*, collector: User, edition: Edition, notes: str = "") -> WantedEdition:
    """A mark of `collector` that a purchase of `edition` completed."""
    mark = make_wanted_edition(collector=collector, edition=edition, notes=notes)
    make_collection(collector=collector, edition=edition)
    mark.refresh_from_db()
    assert mark.completed_at is not None
    return mark


class HuntAccessTests(ComiTestCase):
    def test_anonymous_users_are_sent_to_login(self) -> None:
        edition = make_edition()
        mark = make_wanted_edition(collector=make_user(), edition=edition)
        requests = [
            ("get", reverse("comics_hunt"), {}),
            ("post", reverse("comics_hunt_mark", args=[edition.pk]), {"wanted": "0"}),
            ("post", reverse("comics_hunt_note", args=[mark.pk]), {note_field(mark): "x"}),
        ]
        for method, url, data in requests:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                login = f"{reverse('login')}?{urlencode({'next': url})}"
                self.assertRedirects(response, login, fetch_redirect_response=False)
        self.assertEqual(WantedEdition.objects.get().notes, "")
        self.assertEqual(WantedEdition.objects.count(), 1)

    def test_post_endpoints_reject_get(self) -> None:
        user = make_user()
        edition = make_edition()
        mark = make_wanted_edition(collector=user, edition=edition)
        self.client.force_login(user)

        for url in (reverse("comics_hunt_mark", args=[edition.pk]), reverse("comics_hunt_note", args=[mark.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, {"wanted": "0"}).status_code, 405)
        self.assertTrue(WantedEdition.objects.filter(pk=mark.pk).exists())


class HuntListTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)

    def test_lists_only_the_users_marks_newest_first(self) -> None:
        older = make_wanted_edition(collector=self.user, edition=make_edition())
        newer = make_wanted_edition(collector=self.user, edition=make_edition())
        foreign = make_wanted_edition(collector=make_user(), edition=make_edition())
        # Ordered by creation time, not by id: the first mark becomes the newest.
        now = timezone.now()
        WantedEdition.objects.filter(pk=older.pk).update(created_at=now)
        WantedEdition.objects.filter(pk=newer.pk).update(created_at=now - datetime.timedelta(days=1))

        response = self.client.get(reverse("comics_hunt"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([mark.pk for mark in response.context["page_obj"]], [older.pk, newer.pk])
        self.assertNotContains(response, foreign.edition.short_name)
        self.assertContains(response, reverse("comics_edition_detail", args=[older.edition_id]) + "?next=")

    def test_completed_marks_are_not_listed(self) -> None:
        active = make_wanted_edition(collector=self.user, edition=make_edition())
        completed = completed_mark(collector=self.user, edition=make_edition(), notes="Hidden note")

        response = self.client.get(reverse("comics_hunt"))

        self.assertEqual([mark.pk for mark in response.context["page_obj"]], [active.pk])
        self.assertNotContains(response, completed.edition.short_name)
        self.assertNotContains(response, "Hidden note")

    def test_paginates_like_the_edition_list(self) -> None:
        for _ in range(26):
            make_wanted_edition(collector=self.user, edition=make_edition())

        first = self.client.get(reverse("comics_hunt"))
        second = self.client.get(reverse("comics_hunt"), {"page": 2})

        self.assertEqual(len(first.context["page_obj"]), 25)
        self.assertEqual(len(second.context["page_obj"]), 1)
        self.assertEqual(first.context["page_obj"].paginator.count, 26)

    def test_empty_state_links_to_the_editions(self) -> None:
        make_wanted_edition(collector=make_user(), edition=make_edition())

        response = self.client.get(reverse("comics_hunt"))

        self.assertContains(response, "You're not looking for any edition yet.")
        self.assertContains(response, f'href="{reverse("comics_editions")}"')

    def test_shows_the_owned_chip_and_the_note(self) -> None:
        owned = make_edition()
        make_collection(collector=self.user, edition=owned)
        twice = make_edition()
        make_collection(collector=self.user, edition=twice)
        make_collection(collector=self.user, edition=twice)
        not_owned = make_edition()
        make_collection(collector=make_user(), edition=not_owned)  # someone else's copy
        make_wanted_edition(collector=self.user, edition=owned, notes="A 9.8 to replace mine")
        make_wanted_edition(collector=self.user, edition=twice)
        make_wanted_edition(collector=self.user, edition=not_owned)

        response = self.client.get(reverse("comics_hunt"))

        self.assertContains(response, "You own it", count=1)
        self.assertContains(response, "You own 2", count=1)
        self.assertContains(response, "A 9.8 to replace mine")
        owned_counts = {mark.edition_id: mark.owned_count for mark in response.context["page_obj"]}
        self.assertEqual(owned_counts, {owned.pk: 1, twice.pk: 2, not_owned.pk: 0})

    def test_a_sold_copy_does_not_count_as_owned(self) -> None:
        edition = make_edition()
        purchase = make_collection(collector=self.user, edition=edition, trade_date=datetime.date(2020, 1, 1))
        make_collection(
            collector=self.user,
            edition=edition,
            trade_type=Collection.TradeChoices.SELLING,
            trade_date=datetime.date(2021, 1, 1),
            previous_trade=purchase,
        )
        make_wanted_edition(collector=self.user, edition=edition)

        response = self.client.get(reverse("comics_hunt"))

        self.assertNotContains(response, "You own it")

    def test_the_sidebar_highlights_hunt_only(self) -> None:
        response = self.client.get(reverse("comics_hunt"))

        content = response.content.decode()
        start = content.index('class="md-side-nav"')
        side_nav = content[start : content.index("</nav>", start)]
        self.assertEqual(side_nav.count('aria-current="page"'), 1)
        active = side_nav.index('aria-current="page"')
        # The attribute sits in the active link's tag, just before its href.
        self.assertEqual(side_nav.index(f'href="{reverse("comics_hunt")}"', active), side_nav.index("href=", active))

    def test_query_count_does_not_grow_with_the_rows(self) -> None:
        editorial = make_editorial()

        def add_mark() -> None:
            edition = make_edition(publishing=make_publishing(editorials=[editorial]))
            make_collection(collector=self.user, edition=edition)
            make_wanted_edition(collector=self.user, edition=edition, notes="note")

        def queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(reverse("comics_hunt")).status_code, 200)
            return len(captured)

        add_mark()
        one_row = queries()
        for _ in range(5):
            add_mark()
        self.assertEqual(queries(), one_row)


class HuntMarkTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.edition = make_edition()
        self.client.force_login(self.user)
        self.url = reverse("comics_hunt_mark", args=[self.edition.pk])

    def marks(self) -> int:
        return WantedEdition.objects.filter(collector=self.user, edition=self.edition).count()

    def test_marking_twice_leaves_one_mark(self) -> None:
        for _ in range(2):
            response = self.client.post(self.url, {"wanted": "1"})
            self.assertRedirects(
                response, reverse("comics_edition_detail", args=[self.edition.pk]), fetch_redirect_response=False
            )
        self.assertEqual(self.marks(), 1)

    def test_marking_again_keeps_the_note(self) -> None:
        make_wanted_edition(collector=self.user, edition=self.edition, notes="Signed copy")

        self.client.post(self.url, {"wanted": "1"})

        self.assertEqual(WantedEdition.objects.get(collector=self.user, edition=self.edition).notes, "Signed copy")

    def test_unmarking_removes_the_mark_and_is_idempotent(self) -> None:
        make_wanted_edition(collector=self.user, edition=self.edition)
        other = make_wanted_edition(collector=make_user(), edition=self.edition)

        for _ in range(2):
            response = self.client.post(self.url, {"wanted": "0"})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.marks(), 0)
        self.assertTrue(WantedEdition.objects.filter(pk=other.pk).exists())

    def test_messages_confirm_the_new_state(self) -> None:
        response = self.client.post(self.url, {"wanted": "1"}, follow=True)
        self.assertContains(response, f"{self.edition.short_name} is now in your hunt.")

        response = self.client.post(self.url, {"wanted": "0"}, follow=True)
        self.assertContains(response, f"{self.edition.short_name} is no longer in your hunt.")

    def test_an_owned_edition_can_be_marked(self) -> None:
        make_collection(collector=self.user, edition=self.edition)

        self.client.post(self.url, {"wanted": "1"})

        self.assertEqual(self.marks(), 1)

    def test_a_missing_or_invalid_state_is_rejected(self) -> None:
        make_wanted_edition(collector=self.user, edition=self.edition)
        for data in ({}, {"wanted": "yes"}, {"wanted": ""}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(self.marks(), 1)

    def test_an_unknown_edition_is_a_404(self) -> None:
        url = reverse("comics_hunt_mark", args=[self.edition.pk + 1000])

        self.assertEqual(self.client.post(url, {"wanted": "1"}).status_code, 404)
        self.assertFalse(WantedEdition.objects.exists())

    def test_goes_back_to_a_safe_next(self) -> None:
        back = f"{reverse('comics_editions')}?page=2"

        response = self.client.post(self.url, {"wanted": "1", "next": back})

        self.assertRedirects(response, back, fetch_redirect_response=False)

    def test_an_unsafe_next_is_not_followed(self) -> None:
        response = self.client.post(self.url, {"wanted": "1", "next": "https://evil.example.com/"})

        self.assertRedirects(
            response, reverse("comics_edition_detail", args=[self.edition.pk]), fetch_redirect_response=False
        )
        self.assertEqual(self.marks(), 1)

    def test_a_purchase_after_marking_completes_the_hunt(self) -> None:
        self.client.post(self.url, {"wanted": "1"})

        make_collection(collector=self.user, edition=self.edition, trade_type=Collection.TradeChoices.BUYING)

        self.assertEqual(self.marks(), 1)  # kept, as completed
        self.assertFalse(WantedEdition.objects.active().filter(collector=self.user, edition=self.edition).exists())

    def test_marking_a_completed_hunt_again_brings_it_back_with_its_note(self) -> None:
        mark = completed_mark(collector=self.user, edition=self.edition, notes="Signed copy")

        self.client.post(self.url, {"wanted": "1"})

        mark.refresh_from_db()
        self.assertIsNone(mark.completed_at)
        self.assertEqual(mark.notes, "Signed copy")
        self.assertEqual(self.marks(), 1)

    def test_unmarking_keeps_a_completed_mark(self) -> None:
        mark = completed_mark(collector=self.user, edition=self.edition, notes="Signed copy")

        for _ in range(2):
            self.assertEqual(self.client.post(self.url, {"wanted": "0"}).status_code, 302)

        mark.refresh_from_db()
        self.assertIsNotNone(mark.completed_at)
        self.assertEqual(mark.notes, "Signed copy")


class HuntNoteTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)
        self.mark = make_wanted_edition(collector=self.user, edition=make_edition(), notes="Old note")
        self.url = reverse("comics_hunt_note", args=[self.mark.pk])

    def post_note(self, notes: str, **extra: str) -> HttpResponse:
        return self.client.post(self.url, {note_field(self.mark): notes, **extra})

    def stored_note(self) -> str:
        self.mark.refresh_from_db()
        return self.mark.notes

    def test_saving_and_changing_a_note(self) -> None:
        mark = make_wanted_edition(collector=self.user, edition=make_edition())
        url = reverse("comics_hunt_note", args=[mark.pk])

        response = self.client.post(url, {note_field(mark): "  A 9.8 copy  "})
        self.assertRedirects(response, reverse("comics_hunt"), fetch_redirect_response=False)
        mark.refresh_from_db()
        self.assertEqual(mark.notes, "A 9.8 copy")

        self.client.post(url, {note_field(mark): "Any copy will do"})
        mark.refresh_from_db()
        self.assertEqual(mark.notes, "Any copy will do")

    def test_emptying_the_note(self) -> None:
        response = self.post_note("", next=f"{reverse('comics_hunt')}?page=1")

        self.assertRedirects(response, f"{reverse('comics_hunt')}?page=1", fetch_redirect_response=False)
        self.assertEqual(self.stored_note(), "")

    def test_success_messages(self) -> None:
        response = self.client.post(self.url, {note_field(self.mark): "New"}, follow=True)
        self.assertContains(response, "Note saved.")

        response = self.client.post(self.url, {note_field(self.mark): ""}, follow=True)
        self.assertContains(response, "Note removed.")

    def test_a_note_over_the_limit_is_rejected(self) -> None:
        response = self.client.post(self.url, {note_field(self.mark): "x" * (NOTE_LIMIT + 1)}, follow=True)

        self.assertContains(response, "The note was not saved:")
        self.assertEqual(self.stored_note(), "Old note")

    def test_a_note_at_the_limit_is_saved(self) -> None:
        self.post_note("x" * NOTE_LIMIT)

        self.assertEqual(self.stored_note(), "x" * NOTE_LIMIT)

    def test_line_breaks_count_as_one_character(self) -> None:
        # Browsers count a line break as one character for maxlength but submit it as CRLF.
        lines = "x" * 249 + "\r\n" + "y" * 250

        self.post_note(lines)

        self.assertEqual(self.stored_note(), "x" * 249 + "\n" + "y" * 250)

    def test_an_unsafe_next_is_not_followed(self) -> None:
        response = self.post_note("New", next="//evil.example.com/")

        self.assertRedirects(response, reverse("comics_hunt"), fetch_redirect_response=False)
        self.assertEqual(self.stored_note(), "New")

    def test_another_users_mark_is_a_404(self) -> None:
        intruder = make_user()
        self.client.force_login(intruder)

        response = self.post_note("Hijacked")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored_note(), "Old note")

        # Nor can they remove it through the mark endpoint: that only touches their own marks.
        self.client.post(reverse("comics_hunt_mark", args=[self.mark.edition_id]), {"wanted": "0"})
        self.assertTrue(WantedEdition.objects.filter(pk=self.mark.pk).exists())

    def test_the_note_of_a_completed_mark_cannot_be_edited(self) -> None:
        mark = completed_mark(collector=self.user, edition=make_edition(), notes="Signed copy")

        response = self.client.post(reverse("comics_hunt_note", args=[mark.pk]), {note_field(mark): "Changed"})

        self.assertEqual(response.status_code, 404)
        mark.refresh_from_db()
        self.assertEqual(mark.notes, "Signed copy")

    def test_a_mark_completed_while_saving_its_note_stays_completed(self) -> None:
        save_note = WantedEdition.save_note

        def complete_first(mark: WantedEdition) -> bool:
            # A purchase completes the stored mark after the view read it and before the note is written.
            make_collection(collector=self.user, edition=mark.edition)
            return save_note(mark)

        with mock.patch.object(WantedEdition, "save_note", autospec=True, side_effect=complete_first):
            response = self.post_note("New")

        self.assertEqual(response.status_code, 404)
        self.mark.refresh_from_db()
        self.assertIsNotNone(self.mark.completed_at)
        self.assertEqual(self.mark.notes, "Old note")

    def test_the_hunt_page_shows_the_note_form_filled_in(self) -> None:
        response = self.client.get(reverse("comics_hunt"))

        self.assertContains(response, f'name="{note_field(self.mark)}"')
        self.assertContains(response, f'maxlength="{NOTE_LIMIT}"')
        self.assertContains(response, "Old note</textarea>")


class EditionPagesHuntStateTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)
        self.wanted = make_edition()
        self.not_wanted = make_edition()
        make_wanted_edition(collector=self.user, edition=self.wanted)
        # Another collector's mark does not show as the user's.
        make_wanted_edition(collector=make_user(), edition=self.not_wanted)
        # Nor does a mark of the user completed by a purchase.
        self.completed = make_edition()
        completed_mark(collector=self.user, edition=self.completed)

    def test_edition_list_flags_wanted_rows_and_offers_the_right_action(self) -> None:
        response = self.client.get(reverse("comics_editions"))

        flags = {edition.pk: edition.wanted for edition in response.context["page_obj"]}
        self.assertEqual(flags, {self.wanted.pk: True, self.not_wanted.pk: False, self.completed.pk: False})
        self.assertContains(response, ">bookmark</span>", count=1)  # the chip (the buttons use other icons)
        content = response.content.decode()
        self.assertEqual(content.count('name="wanted" value="0"'), 1)
        self.assertEqual(content.count('name="wanted" value="1"'), 2)
        self.assertIn(f'action="{reverse("comics_hunt_mark", args=[self.wanted.pk])}"', content)
        self.assertIn(f'aria-label="Stop looking for it: {self.wanted.short_name}"', content)
        self.assertIn(f'aria-label="Looking for it: {self.not_wanted.short_name}"', content)

    def test_edition_list_hunt_check_is_one_query_per_page(self) -> None:
        def queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                self.client.get(reverse("comics_editions"))
            return len(captured)

        before = queries()
        for _ in range(5):
            make_wanted_edition(collector=self.user, edition=make_edition())
        self.assertEqual(queries(), before)

    def test_edition_detail_of_a_wanted_edition(self) -> None:
        response = self.client.get(reverse("comics_edition_detail", args=[self.wanted.pk]))

        self.assertTrue(response.context["is_wanted"])
        self.assertContains(response, 'name="wanted" value="0"')
        self.assertContains(response, "Stop looking for it")

    def test_edition_detail_of_an_edition_not_wanted(self) -> None:
        for edition in (self.not_wanted, self.completed):
            with self.subTest(edition=edition.pk):
                response = self.client.get(reverse("comics_edition_detail", args=[edition.pk]))

                self.assertFalse(response.context["is_wanted"])
                self.assertContains(response, 'name="wanted" value="1"')
                self.assertNotContains(response, "Stop looking for it")
