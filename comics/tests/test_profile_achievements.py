"""Achievements on profiles, by category (Cuánto Cabrón style): one medallion per category with
"X of Y challenges" and a progress bar, and a disclosure panel with one row per achievement level.
Counts per level, requirement texts, earned dates and progress, secrets, what other users see,
the disclosure markup and the query count. Also the user_saved trigger and window_kinds_of."""

import dataclasses
import datetime
import re
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from comics.achievements import engine, registry
from comics.achievements.display import showcase
from comics.achievements.registry import Event, get_rule
from comics.achievements.windows import CalendarWindow, WindowKind, window_kinds_of
from comics.models import Achievement, AchievementCategory, AchievementTier, Profile, UserAchievement

from .base import ComiTestCase
from .factories import image_upload, make_achievement, make_category, make_collection, make_edition, make_user
from .test_achievements import achievement, set_thresholds


def shelf_of(shown: object, code: str) -> object:
    return next(shelf for shelf in shown.categories if shelf.category.code == code)


class ShowcaseTests(ComiTestCase):
    def setUp(self) -> None:
        self.owner = make_user(username="owner")
        set_thresholds("great_power", 1, 2, 5)
        for _ in range(2):
            make_collection(collector=self.owner, edition=make_edition())
        engine.evaluate(self.owner)  # origin, great_power x2, novato

    def test_levels_are_challenges(self) -> None:
        collection = shelf_of(showcase(self.owner, include_locked=True), "collection")
        rows = [(reto.achievement.code, reto.level, reto.earned) for reto in collection.retos]
        self.assertEqual(
            rows[:4],
            [("origin", 1, True), ("great_power", 1, True), ("great_power", 2, True), ("great_power", 3, False)],
        )
        # origin 1 + great_power 3 + multiverse 1 + speed_force 3 + lazarus_pit 1
        self.assertEqual((collection.earned, collection.total, collection.percent), (3, 9, 33))
        self.assertFalse(collection.complete)

    def test_level_names_and_requirements(self) -> None:
        AchievementTier.objects.filter(achievement__code="great_power", level=3).update(label_en="Hoarder")
        retos = {
            (reto.achievement.code, reto.level): reto
            for reto in shelf_of(showcase(self.owner, include_locked=True), "collection").retos
        }
        great = [retos[("great_power", level)] for level in (1, 2, 3)]
        self.assertEqual([reto.level_name for reto in great], ["Base", "Silver", "Hoarder"])
        self.assertEqual(
            [reto.requirement for reto in great],
            [
                "Have at least 1 comic in your collection",
                "Have at least 2 comics in your collection",
                "Have at least 5 comics in your collection",
            ],
        )
        origin = retos[("origin", 1)]
        self.assertEqual((origin.tiered, origin.level_name), (False, ""))
        self.assertEqual(origin.requirement, achievement("origin").description)  # no levels: its description
        multiverse = retos[("multiverse", 1)]  # a single level still states its threshold
        self.assertEqual(
            (multiverse.tiered, multiverse.requirement), (False, "Own 5 different editions of the same issue")
        )

    def test_requirements_are_plural_aware_and_have_a_fallback(self) -> None:
        for key, one, many in [
            ("batcave_vault", "Own at least 1 signed piece", "Own at least 15 signed pieces"),
            ("veteran", "Be a member for at least 1 full year", "Be a member for at least 5 full years"),
            ("assemble", "Complete 1 connecting", "Complete 3 connectings"),
            ("chronicler", "Complete 1 reading arc", "Complete 5 reading arcs"),
            ("speed_force", "Buy 1 comic on the same day", "Buy 50 comics on the same day"),
        ]:
            with self.subTest(rule=key):
                rule = get_rule(key)
                self.assertEqual(rule.requirement_for(1), one)
                self.assertEqual(rule.requirement_for(int(re.search(r"\d+", many).group())), many)
        bare = dataclasses.replace(get_rule("great_power"), requirement=None)
        self.assertEqual(bare.requirement_for(7), "comics in the collection: 7")

    def test_earned_dates_are_only_the_known_ones(self) -> None:
        set_thresholds("great_power", 1, 2, 3)
        award = UserAchievement.objects.get(user=self.owner, achievement__code="great_power")
        first, later = timezone.now() - datetime.timedelta(days=30), timezone.now()
        UserAchievement.objects.filter(pk=award.pk).update(level=3, awarded_at=first, level_reached_at=later)
        retos = [
            reto
            for reto in shelf_of(showcase(self.owner, include_locked=True), "collection").retos
            if reto.achievement.code == "great_power"
        ]
        # Level 1 came with the first award, level 3 is the current one; when level 2 was reached
        # is not stored.
        self.assertEqual([reto.earned_on for reto in retos], [first, None, later])

    def test_progress_only_on_the_owners_unearned_count_levels(self) -> None:
        retos = shelf_of(showcase(self.owner, include_locked=True), "collection").retos
        gold = next(reto for reto in retos if reto.achievement.code == "great_power" and reto.level == 3)
        self.assertEqual((gold.progress, gold.goal), (2, 5))
        silver = next(reto for reto in retos if reto.achievement.code == "great_power" and reto.level == 2)
        self.assertEqual((silver.progress, silver.goal), (None, None))
        others = showcase(self.owner, include_locked=False)
        self.assertTrue(all(reto.goal is None for shelf in others.categories for reto in shelf.retos))

    def test_categories_shown(self) -> None:
        shown = showcase(self.owner, include_locked=True)
        self.assertEqual(shelf_of(shown, "treasures").earned, 0)
        empty = make_category(code="empty")
        make_achievement(code="ghost_rule", rule="no_such_rule", category=empty)  # never shown
        hidden = make_category(code="hidden", is_enabled=False)
        make_achievement(code="g_hidden", rule="login_in_window", category=hidden, window_start_time=datetime.time(1))
        Achievement.objects.filter(category__code="seniority").update(is_enabled=False)
        codes = [shelf.category.code for shelf in showcase(self.owner, include_locked=True).categories]
        self.assertEqual(codes, ["collection", "treasures", "sagas", "community", "random"])

    def test_a_complete_category(self) -> None:
        trophies = make_category(code="trophies", name_en="Trophies", display_order=99)
        only = make_achievement(
            code="g_only", rule="login_in_window", category=trophies, window_start_time=datetime.time(1)
        )
        UserAchievement.objects.award(self.owner, only, 1)
        shelf = shelf_of(showcase(self.owner, include_locked=False), "trophies")
        self.assertEqual((shelf.earned, shelf.total, shelf.percent, shelf.complete), (1, 1, 100, True))

    def test_query_count_does_not_grow_with_categories_or_levels(self) -> None:
        def queries(include_locked: bool) -> int:
            with CaptureQueriesContext(connection) as captured:
                showcase(self.owner, include_locked=include_locked)
            return len(captured)

        before = (queries(False), queries(True))
        self.assertEqual(before[0], 3)  # achievements with categories, levels, awards
        for number in range(4):
            category = make_category(code=f"extra{number}")
            for index in range(3):
                extra = make_achievement(
                    code=f"g_extra{number}_{index}",
                    rule="login_in_window",
                    category=category,
                    window_start_time=datetime.time(1),
                    window_end_time=datetime.time(2),
                )
                UserAchievement.objects.award(self.owner, extra, 1)
        set_thresholds("batcave_vault", 1, 2, 3, 4, 5, 6)  # more levels, same rule
        self.assertEqual((queries(False), queries(True)), before)


class ProfilePageTests(ComiTestCase):
    def setUp(self) -> None:
        self.owner = make_user(username="owner")
        self.viewer = make_user(username="viewer")
        set_thresholds("great_power", 1, 2, 5)
        for _ in range(2):
            make_collection(collector=self.owner, edition=make_edition())
        engine.evaluate(self.owner)

    def own_page(self) -> str:
        self.client.force_login(self.owner)
        return self.client.get(reverse("profile")).content.decode()

    def public_page(self) -> str:
        self.client.force_login(self.viewer)
        return self.client.get(reverse("user_profile", args=["owner"])).content.decode()

    def test_owner_sees_medallions_counts_and_every_challenge(self) -> None:
        html = self.own_page()
        self.assertIn("4 of 30 challenges", html)  # the section's total
        self.assertIn("3 of 9 challenges", html)  # collection
        self.assertIn('class="trophy-progress__bar is-partial" style="width: 33%"', html)
        self.assertIn('class="trophy-progress__bar" style="width: 0%"', html)  # treasures: nothing yet
        self.assertIn('<span class="medal medal--base medal--lg is-locked" aria-hidden="true">', html)  # greyed
        self.assertIn("/static/achievements/_category.svg", html)
        self.assertIn('With great power… <span class="reto__level">Silver</span>', html)
        self.assertIn('<li class="reto is-locked">', html)
        self.assertIn("Progress: 2 / 5", html)
        self.assertIn("Earned on", html)
        self.assertIn("Not earned yet", html)

    def test_disclosure_markup(self) -> None:
        html = self.own_page()
        buttons = re.findall(
            r'<button type="button" class="trophy-category" id="([^"]+)"\s+aria-expanded="false" aria-controls="([^"]+)">',
            html,
        )
        self.assertEqual(len(buttons), 6)
        for _button_id, panel_id in buttons:
            with self.subTest(panel=panel_id):
                self.assertRegex(
                    html, rf'<section class="trophy-panel" id="{panel_id}" aria-labelledby="[^"]+" hidden>'
                )
        self.assertIn("achievement_trophies.js", html)
        self.assertNotIn("achievement_medals.js", html)

    def test_secrets_are_concealed_until_earned(self) -> None:
        html = self.own_page()
        self.assertIn('<div class="reto__name">???</div>', html)
        self.assertIn("Some heroes prefer to stay in the shadows.", html)
        for leak in ["Secret identity", "Lois Lane", "secret_identity.svg", "Halloween night", "halloween.svg"]:
            self.assertNotIn(leak, html)
        Profile.objects.filter(user=self.owner).update(is_public=False)
        engine.evaluate(self.owner)
        self.assertIn("Secret identity", self.own_page())

    def test_other_users_see_counts_but_only_earned_challenges(self) -> None:
        html = self.public_page()
        self.assertIn("4 of 30 challenges", html)
        self.assertIn("3 of 9 challenges", html)
        self.assertIn('With great power… <span class="reto__level">Silver</span>', html)
        self.assertIn("Nothing earned in this category yet.", html)  # e.g. treasures
        for leak in [
            '<li class="reto is-locked">',
            "Progress",
            "Not earned yet",
            "???",
            "_secret.svg",
            "Gold",
            "Have at least 5 comics",
            "Veteran",
            "Some heroes",
            "Secret achievement",
            "Lois Lane",
        ]:
            with self.subTest(leak=leak):
                self.assertNotIn(leak, html)

    def test_private_profiles_show_nothing_to_others(self) -> None:
        Profile.objects.filter(user=self.owner).update(is_public=False)
        html = self.public_page()
        self.assertNotIn("challenge", html)
        self.assertNotIn("trophy-category", html)

    def test_disabled_achievements_are_hidden(self) -> None:
        Achievement.objects.filter(code="origin").update(is_enabled=False)
        for html in [self.own_page(), self.public_page()]:
            self.assertNotIn("Origin", html)
            self.assertIn("2 of 8 challenges", html)

    def test_category_texts_and_medallion(self) -> None:
        AchievementCategory.objects.filter(code="collection").update(
            name_en="<b>Coll</b>", description_en="<script>x()</script>"
        )
        category = AchievementCategory.objects.get(code="collection")
        category.image = image_upload("medal.png")
        category.save()
        html = self.own_page()
        self.assertIn(category.image.url, html)
        self.assertIn("&lt;b&gt;Coll&lt;/b&gt;", html)
        self.assertIn("&lt;script&gt;x()&lt;/script&gt;", html)
        self.assertNotIn("<script>x()", html)


class UserSavedTriggerTests(ComiTestCase):
    def setUp(self) -> None:
        with self.committed():
            self.user = make_user()

    def events(self, **save: object) -> list[Event]:
        with mock.patch("comics.signals.schedule") as schedule:
            self.user.save(**save)
        return [call.args[0] for call in schedule.call_args_list]

    def test_personal_info_saves_are_profile_events(self) -> None:
        self.assertEqual(self.events(), [Event.PROFILE])
        self.assertEqual(self.events(update_fields=["first_name"]), [Event.PROFILE])
        self.assertEqual(self.events(update_fields=["email", "password"]), [Event.PROFILE])

    def test_other_partial_saves_are_not(self) -> None:
        self.assertEqual(self.events(update_fields=["last_login"]), [])
        self.assertEqual(self.events(update_fields=["password"]), [])  # password change or hash upgrade

    def test_a_password_change_evaluates_nothing(self) -> None:
        self.user.set_password("another-pass-42")
        with mock.patch.object(engine, "evaluate") as evaluate, self.committed():
            User.objects.get(pk=self.user.pk).set_password("x")  # no save
            self.user.save(update_fields=["password"])
        evaluate.assert_not_called()


class WindowKindsTests(ComiTestCase):
    def test_kinds_of_values(self) -> None:
        self.assertEqual(window_kinds_of(CalendarWindow.NONE, None, None), set())
        self.assertEqual(window_kinds_of(CalendarWindow.YEARLY, None, None), {WindowKind.YEARLY})
        self.assertEqual(
            window_kinds_of(CalendarWindow.ABSOLUTE, datetime.time(1), None),
            {WindowKind.ABSOLUTE, WindowKind.TIME_OF_DAY},  # half a time window still counts as one
        )
        self.assertEqual(registry.get_rule("purchases_each_month_in_window").window_kinds, {WindowKind.YEARLY})
