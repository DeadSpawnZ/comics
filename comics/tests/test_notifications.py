"""Notifications: the navbar bell (badge, lazy menu), the notifications page, marking read and
opening (own notifications only, POST + CSRF, same-site targets), notifications created by the
achievement engine (live, level-ups, bulk summaries, the data migration for existing awards) and
announcements from Gestión (audience fan-out, edits, deletion, permissions)."""

import importlib
from io import StringIO

from django.apps import apps
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from comics.achievements import engine
from comics.models import Achievement, AchievementTier, Announcement, Notification, UserAchievement
from comics.roles import ADMINISTRATORS, COLLECTORS, REVIEWERS

from .base import ComiTestCase
from .factories import make_achievement, make_category, make_collection, make_edition, make_user

retro_migration = importlib.import_module("comics.migrations.0106_notify_existing_achievements")

PAGE = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def announcement(**fields: object) -> Announcement:
    values = {"title_en": "News", "title_es": "Noticias", "body_en": "Body", "body_es": "Cuerpo"}
    values.update(fields)
    return Announcement.objects.create(**values)


def notify(user: object, **fields: object) -> Notification:
    """An unread announcement notification for `user`."""
    of = fields.pop("of", None) or announcement()
    return Notification.objects.create(recipient=user, kind=Notification.Kind.ANNOUNCEMENT, announcement=of, **fields)


def set_thresholds(code: str, *thresholds: int) -> None:
    target = Achievement.objects.get(code=code)
    target.tiers.all().delete()
    for level, threshold in enumerate(thresholds, start=1):
        AchievementTier.objects.create(achievement=target, level=level, threshold=threshold)


def buy(user: object) -> None:
    make_collection(collector=user, edition=make_edition())


class BellTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)

    def bell(self) -> str:
        return self.client.get(reverse("collectables"), headers=PAGE).content.decode()

    def test_badge_shows_the_unread_count(self) -> None:
        news = announcement()
        for _ in range(3):
            notify(self.user, of=news)
        notify(self.user, of=news, read_at=timezone.now())
        notify(make_user(), of=news)  # someone else's
        html = self.bell()
        self.assertIn('<span class="notif-bell__badge" aria-hidden="true">3</span>', html)
        self.assertIn('aria-label="Notifications (3 unread)"', html)

    def test_badge_is_capped(self) -> None:
        news = announcement()
        for _ in range(12):
            notify(self.user, of=news)
        html = self.bell()
        self.assertIn('<span class="notif-bell__badge" aria-hidden="true">9+</span>', html)
        self.assertIn('aria-label="Notifications (9+ unread)"', html)

    def test_no_badge_without_unread(self) -> None:
        notify(self.user, read_at=timezone.now())
        html = self.bell()
        self.assertIn("data-notifications", html)
        self.assertNotIn("notif-bell__badge", html)
        self.assertIn('aria-label="Notifications"', html)

    def test_anonymous_visitors_have_no_bell(self) -> None:
        self.client.logout()
        self.assertNotIn("data-notifications", self.client.get(reverse("login")).content.decode())

    def test_a_page_costs_one_notification_query(self) -> None:
        notify(self.user)
        page = reverse("collectables")
        self.client.get(page, headers=PAGE)  # warm up (session, caches)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(page, headers=PAGE)
        self.assertContains(response, "notif-bell__badge")
        touching = [query["sql"] for query in queries if "comics_notification" in query["sql"]]
        self.assertEqual(len(touching), 1, touching)
        self.assertIn("LIMIT", touching[0].upper())  # counts at most CAP + 1 rows

    def test_pages_that_do_not_show_the_bell_do_not_query_it(self) -> None:
        with CaptureQueriesContext(connection) as queries:
            self.client.get(f"{reverse('comics_api_editions')}?publishing={make_edition().publishing_id}")
        self.assertFalse([query for query in queries if "comics_notification" in query["sql"]])


class MenuAndListTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)

    def test_menu_lists_the_latest_own_notifications(self) -> None:
        for number in range(8):
            notify(self.user, of=announcement(title_en=f"Mine {number}"))
        notify(make_user(), of=announcement(title_en="Not mine"))
        response = self.client.get(reverse("notification_menu"), {"next": "/comics/"})
        self.assertEqual(response.status_code, 200)
        shown = response.context["notifications"]
        self.assertEqual(len(shown), 6)
        self.assertTrue(all(item.recipient_id == self.user.pk for item in shown))
        self.assertContains(response, "Mine 7")
        self.assertNotContains(response, "Mine 0")  # older than the latest six
        self.assertNotContains(response, "Not mine")
        self.assertContains(response, "Mark all as read")
        self.assertContains(response, 'name="next" value="/comics/"')
        self.assertContains(response, reverse("notification_open", args=[shown[0].pk]))

    def test_menu_rejects_an_external_next(self) -> None:
        notify(self.user)
        response = self.client.get(reverse("notification_menu"), {"next": "https://evil.example/"})
        self.assertContains(response, f'name="next" value="{reverse("notifications")}"')

    def test_menu_when_empty(self) -> None:
        response = self.client.get(reverse("notification_menu"))
        self.assertContains(response, "You have no notifications.")
        self.assertNotContains(response, "Mark all as read")

    def test_menu_and_list_need_a_signed_in_user(self) -> None:
        self.client.logout()
        for url in [reverse("notification_menu"), reverse("notifications")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_list_is_paginated(self) -> None:
        news = announcement()
        for _ in range(25):
            notify(self.user, of=news)
        notify(make_user(), of=news)
        first = self.client.get(reverse("notifications"))
        self.assertEqual(len(first.context["page_obj"]), 20)
        self.assertEqual(first.context["page_obj"].paginator.count, 25)
        second = self.client.get(reverse("notifications"), {"page": 2})
        self.assertEqual(len(second.context["page_obj"]), 5)

    def test_list_shows_announcement_bodies_and_unread_marks(self) -> None:
        notify(self.user, of=announcement(title_en="Big news", body_en="Line one\nLine two"))
        notify(self.user, of=announcement(title_en="Old news"), read_at=timezone.now())
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "Big news")
        self.assertContains(response, "Line one<br>Line two")
        self.assertContains(response, "notif-item is-unread", count=1)

    def test_list_renders_each_notification_with_few_queries(self) -> None:
        news = announcement()
        great_power = Achievement.objects.get(code="great_power")
        for level in range(1, 4):
            Notification.objects.achievement_unlocked(self.user, great_power, level)
            notify(self.user, of=news)
        self.client.get(reverse("notifications"))  # warm up
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("notifications"))
        touching = [query for query in queries if "comics_notification" in query["sql"]]
        # Badge, page count and the page itself (with achievements, categories and announcements).
        self.assertEqual(len(touching), 3)

    def test_texts_are_escaped(self) -> None:
        news = announcement(title_en="<script>alert(1)</script>", body_en="<img src=x onerror=alert(2)>")
        notify(self.user, of=news)
        for url in [reverse("notification_menu"), reverse("notifications")]:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn("<script>alert(1)", html)
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
                self.assertNotIn("<img src=x", html)


class MarkReadTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.client.force_login(self.user)

    def test_opening_marks_read_and_goes_to_the_target(self) -> None:
        mine = notify(self.user, of=announcement(link="/comics/"))
        response = self.client.post(reverse("notification_open", args=[mine.pk]))
        self.assertRedirects(response, "/comics/", fetch_redirect_response=False)
        mine.refresh_from_db()
        self.assertIsNotNone(mine.read_at)

    def test_opening_twice_keeps_the_first_read_time(self) -> None:
        mine = notify(self.user)
        self.client.post(reverse("notification_open", args=[mine.pk]))
        mine.refresh_from_db()
        first = mine.read_at
        self.client.post(reverse("notification_open", args=[mine.pk]))
        mine.refresh_from_db()
        self.assertEqual(mine.read_at, first)

    def test_achievement_notifications_open_the_profile_achievements(self) -> None:
        mine = Notification.objects.achievement_unlocked(self.user, Achievement.objects.get(code="origin"), 1)
        response = self.client.post(reverse("notification_open", args=[mine.pk]))
        self.assertRedirects(response, f"{reverse('profile')}#achievements", fetch_redirect_response=False)

    def test_without_a_target_it_opens_the_notifications_page(self) -> None:
        mine = notify(self.user)
        response = self.client.post(reverse("notification_open", args=[mine.pk]))
        self.assertRedirects(response, reverse("notifications"), fetch_redirect_response=False)

    def test_external_targets_are_never_followed(self) -> None:
        for link in ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)"]:
            with self.subTest(link=link):
                news = announcement()
                Announcement.objects.filter(pk=news.pk).update(link=link)  # bypassing validation
                mine = notify(self.user, of=news)
                response = self.client.post(reverse("notification_open", args=[mine.pk]))
                self.assertRedirects(response, reverse("notifications"), fetch_redirect_response=False)

    def test_others_notifications_are_not_found(self) -> None:
        theirs = notify(make_user())
        response = self.client.post(reverse("notification_open", args=[theirs.pk]))
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_marking_read_needs_post(self) -> None:
        mine = notify(self.user)
        self.assertEqual(self.client.get(reverse("notification_open", args=[mine.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("notifications_mark_all_read")).status_code, 405)
        mine.refresh_from_db()
        self.assertIsNone(mine.read_at)

    def test_marking_read_needs_the_csrf_token(self) -> None:
        mine = notify(self.user)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("notification_open", args=[mine.pk])).status_code, 403)
        self.assertEqual(client.post(reverse("notifications_mark_all_read")).status_code, 403)
        mine.refresh_from_db()
        self.assertIsNone(mine.read_at)

    def test_mark_all_read_only_touches_own_notifications(self) -> None:
        news = announcement()
        mine = [notify(self.user, of=news) for _ in range(3)]
        theirs = notify(make_user(), of=news)
        response = self.client.post(reverse("notifications_mark_all_read"), {"next": "/comics/"})
        self.assertRedirects(response, "/comics/", fetch_redirect_response=False)
        self.assertFalse(Notification.objects.filter(pk__in=[item.pk for item in mine], read_at=None).exists())
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_mark_all_read_ignores_an_external_next(self) -> None:
        response = self.client.post(reverse("notifications_mark_all_read"), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("notifications"), fetch_redirect_response=False)


class AchievementNotificationTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="hero")

    def notifications(self, **filters: object) -> list[Notification]:
        return list(Notification.objects.filter(recipient=self.user, **filters).order_by("pk"))

    def test_a_live_award_creates_one_notification(self) -> None:
        # Fixtures inside committed() (see ComiTestCase): the evaluations run as after a commit.
        with self.committed():
            self.user = make_user()
        with self.committed():
            buy(self.user)
        origin = self.notifications(achievement__code="origin")
        self.assertEqual(len(origin), 1)
        self.assertEqual((origin[0].kind, origin[0].level), (Notification.Kind.ACHIEVEMENT, 1))
        self.assertEqual(origin[0].text, "Achievement unlocked: Origin")
        # The snackbar notice stays as well.
        self.assertIn("Achievement unlocked: Origin", engine.take_notices(self.user))

    def test_evaluating_again_does_not_notify_again(self) -> None:
        buy(self.user)
        engine.evaluate(self.user, ["origin"])
        engine.evaluate(self.user, ["origin"])
        self.assertEqual(len(self.notifications(achievement__code="origin")), 1)

    def test_level_ups_create_a_notification_each(self) -> None:
        set_thresholds("great_power", 1, 2)
        buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        levels = [item.level for item in self.notifications(achievement__code="great_power")]
        self.assertEqual(levels, [1, 2])
        self.assertEqual(
            self.notifications(achievement__code="great_power")[-1].text,
            "Achievement unlocked: With great power… (level 2)",
        )

    def test_a_bulk_run_creates_one_summary_per_user(self) -> None:
        set_thresholds("great_power", 1, 2)
        buy(self.user)
        buy(self.user)
        other = make_user()
        call_command("award_achievements", stdout=StringIO(), stderr=StringIO())
        summaries = self.notifications()
        self.assertEqual(len(summaries), 1)  # origin, great_power and novato in one
        self.assertEqual((summaries[0].kind, summaries[0].count), (Notification.Kind.ACHIEVEMENT_SUMMARY, 3))
        self.assertEqual(summaries[0].text, "You unlocked 3 achievements: see them on your profile.")
        self.assertEqual(list(Notification.objects.filter(recipient=other).values_list("count", flat=True)), [1])
        # A second run awards nothing, so it notifies nothing.
        call_command("award_achievements", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(Notification.objects.filter(recipient=self.user).count(), 1)

    def test_a_gestion_reevaluation_sends_one_summary(self) -> None:
        set_thresholds("great_power", 1, 2)
        buy(self.user)
        buy(self.user)
        Notification.objects.filter(recipient=self.user).delete()
        UserAchievement.objects.filter(user=self.user, achievement__code__in=["origin", "great_power"]).delete()
        with self.captureOnCommitCallbacks(execute=True):
            engine.schedule_for_everyone("origin", "great_power")
        summaries = self.notifications()
        self.assertEqual(len(summaries), 1)  # origin and great_power together
        self.assertEqual((summaries[0].kind, summaries[0].count), (Notification.Kind.ACHIEVEMENT_SUMMARY, 2))

    def test_renames_show_up_and_the_viewer_language_is_used(self) -> None:
        origin = Achievement.objects.get(code="origin")
        notification = Notification.objects.achievement_unlocked(self.user, origin, 1)
        Achievement.objects.filter(pk=origin.pk).update(name_en="Genesis", name_es="Génesis")
        fresh = Notification.objects.for_display().get(pk=notification.pk)
        self.assertEqual(fresh.text, "Achievement unlocked: Genesis")
        with translation.override("es"):
            self.assertIn("Génesis", fresh.text)

    def test_disabled_achievements_are_not_named(self) -> None:
        hidden = make_achievement(name_en="Hidden thing", category=make_category(is_enabled=False))
        notification = Notification.objects.achievement_unlocked(self.user, hidden, 1)
        self.assertEqual(notification.text, "An achievement you unlocked is no longer available.")

    def test_retro_migration_sums_up_existing_awards_once(self) -> None:
        other = make_user()
        make_user()  # nothing earned: nothing to tell
        disabled = make_achievement(is_enabled=False)
        for code in ["origin", "great_power"]:
            UserAchievement.objects.award(self.user, Achievement.objects.get(code=code), 1)
        UserAchievement.objects.award(self.user, disabled, 1)  # hidden on profiles: not counted
        UserAchievement.objects.award(other, Achievement.objects.get(code="origin"), 1)
        kept = Notification.objects.achievement_unlocked(self.user, Achievement.objects.get(code="origin"), 1)

        retro_migration.notify_existing_achievements(apps, None)
        retro_migration.notify_existing_achievements(apps, None)  # re-running adds nothing

        held = Notification.objects.filter(kind=Notification.Kind.ACHIEVEMENTS_HELD)
        self.assertEqual(
            sorted(held.values_list("recipient__username", "count")), sorted([("hero", 2), (other.username, 1)])
        )
        self.assertEqual(held.get(recipient=self.user).text, "You have 2 achievements: see them on your profile.")
        self.assertTrue(all(item.is_unread for item in held))

        retro_migration.remove_existing_achievements_notices(apps, None)
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.ACHIEVEMENTS_HELD).exists())
        self.assertTrue(Notification.objects.filter(pk=kept.pk).exists())


class AnnouncementGestionTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(username="manager", role=ADMINISTRATORS)
        self.client.force_login(self.manager)

    def create(self, **overrides: object) -> object:
        data = {
            "title_en": "Maintenance",
            "title_es": "Mantenimiento",
            "body_en": "Tonight.",
            "body_es": "Esta noche.",
            "link": "",
            "audience": "everyone",
        }
        data.update(overrides)
        return self.client.post(reverse("manage_announcement_new"), data)

    def roles(self, *names: str) -> list[str]:
        return [str(pk) for pk in Group.objects.filter(name__in=names).values_list("pk", flat=True)]

    def test_pages_need_the_gestion_permission(self) -> None:
        news = announcement()
        urls = [
            reverse("manage_announcements"),
            reverse("manage_announcement_new"),
            reverse("manage_announcement_edit", args=[news.pk]),
        ]
        collector = make_user()
        for url in urls:
            with self.subTest(url=url):
                self.client.force_login(collector)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.client.force_login(self.manager)
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(collector)
        self.assertEqual(self.client.post(reverse("manage_announcement_delete", args=[news.pk])).status_code, 403)
        self.assertEqual(self.create().status_code, 403)
        self.assertTrue(Announcement.objects.filter(pk=news.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.get(reverse("manage_announcements")).status_code, 302)

    def test_sending_to_everyone_notifies_each_active_user_once(self) -> None:
        reader = make_user()
        make_user(is_active=False)
        response = self.create()
        self.assertRedirects(response, reverse("manage_announcements"), fetch_redirect_response=False)
        news = Announcement.objects.get()
        self.assertEqual((news.audience, news.created_by), (Announcement.Audience.EVERYONE, self.manager))
        self.assertEqual(
            sorted(news.notifications.values_list("recipient__username", flat=True)),
            sorted(["manager", reader.username]),
        )
        self.assertTrue(LogEntry.objects.filter(object_id=str(news.pk), action_flag=ADDITION).exists())
        follow = self.client.get(reverse("manage_announcements"))
        self.assertContains(follow, "Announcement sent to 2 users.")

    def test_sending_to_roles_notifies_their_active_members_once(self) -> None:
        reviewer = make_user(role=REVIEWERS)
        both = make_user(role=REVIEWERS)
        both.groups.add(Group.objects.get(name=ADMINISTRATORS))
        make_user()  # only a Collector
        make_user(role=REVIEWERS, is_active=False)
        self.create(audience="roles", roles=self.roles(REVIEWERS, ADMINISTRATORS))
        news = Announcement.objects.get()
        self.assertEqual(set(news.roles.values_list("name", flat=True)), {REVIEWERS, ADMINISTRATORS})
        self.assertEqual(
            sorted(news.notifications.values_list("recipient__username", flat=True)),
            sorted(["manager", reviewer.username, both.username]),
        )

    def test_roles_are_needed_when_sending_to_roles(self) -> None:
        response = self.create(audience="roles")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "roles", "Choose at least one role.")
        self.assertFalse(Announcement.objects.exists())

    def test_roles_are_ignored_when_sending_to_everyone(self) -> None:
        self.create(audience="everyone", roles=self.roles(REVIEWERS))
        news = Announcement.objects.get()
        self.assertFalse(news.roles.exists())
        self.assertEqual(news.notifications.count(), 1)  # the manager, the only user

    def test_only_links_on_this_site(self) -> None:
        for link in ["https://evil.example/", "//evil.example/", "comics/", "javascript:alert(1)", "/\\evil.example"]:
            with self.subTest(link=link):
                response = self.create(link=link)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors["link"])
        self.assertFalse(Announcement.objects.exists())
        self.create(link="/comics/?page=2")
        self.assertEqual(Announcement.objects.get().link, "/comics/?page=2")

    def test_edits_show_up_in_sent_notifications(self) -> None:
        reader = make_user()
        self.create()
        news = Announcement.objects.get()
        data = {
            "title_en": "Maintenance moved",
            "title_es": "Mantenimiento movido",
            "body_en": "Tomorrow.",
            "body_es": "",
            "link": "/comics/",
            # Ignored: the audience is fixed once sent.
            "audience": "roles",
            "roles": self.roles(REVIEWERS),
        }
        response = self.client.post(reverse("manage_announcement_edit", args=[news.pk]), data)
        self.assertRedirects(response, reverse("manage_announcements"), fetch_redirect_response=False)
        news.refresh_from_db()
        self.assertEqual(news.audience, Announcement.Audience.EVERYONE)
        self.assertEqual(news.notifications.count(), 2)
        notification = Notification.objects.for_display().get(recipient=reader)
        self.assertEqual((notification.text, notification.detail), ("Maintenance moved", "Tomorrow."))
        self.assertEqual(notification.target_url, "/comics/")
        with translation.override("es"):
            # Spanish title; the Spanish body is empty, so it falls back to the English one.
            self.assertEqual((notification.text, notification.detail), ("Mantenimiento movido", "Tomorrow."))
        self.assertTrue(LogEntry.objects.filter(object_id=str(news.pk), action_flag=CHANGE).exists())

    def test_deleting_removes_its_notifications(self) -> None:
        make_user()
        self.create()
        news = Announcement.objects.get()
        other = announcement()
        kept = notify(self.manager, of=other)
        response = self.client.post(reverse("manage_announcement_delete", args=[news.pk]))
        self.assertRedirects(response, reverse("manage_announcements"), fetch_redirect_response=False)
        self.assertFalse(Announcement.objects.filter(pk=news.pk).exists())
        self.assertFalse(Notification.objects.filter(announcement_id=news.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=kept.pk).exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(news.pk), action_flag=DELETION).exists())
        self.assertEqual(self.client.get(reverse("manage_announcement_delete", args=[other.pk])).status_code, 405)

    def test_list_shows_audience_recipients_and_reads(self) -> None:
        make_user()
        make_user()
        self.create(title_en="<b>Bold</b> news")
        news = Announcement.objects.get()
        news.notifications.filter(recipient=self.manager).update(read_at=timezone.now())
        self.create(title_en="For reviewers", audience="roles", roles=self.roles(REVIEWERS))
        response = self.client.get(reverse("manage_announcements"))
        self.assertContains(response, "Read by 1 of 3 recipients")
        self.assertContains(response, "Read by 0 of 0 recipients")
        self.assertContains(response, "Everyone")
        self.assertContains(response, "Reviewers")
        self.assertContains(response, "&lt;b&gt;Bold&lt;/b&gt; news")
        self.assertNotContains(response, "<b>Bold</b>")

    def test_history_links_announcements(self) -> None:
        self.create()
        response = self.client.get(reverse("manage_home"), {"tipo": "announcement"})
        news = Announcement.objects.get()
        self.assertContains(response, reverse("manage_announcement_edit", args=[news.pk]))
