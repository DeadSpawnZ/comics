"""Generic rules and achievements created in Gestión: the code/rule split, `login_in_window`
(inside, outside, without a window, several rows sharing the rule, halloween), creating and
deleting achievements in Gestión, the rule flags of the list, and the data migrations that
introduced categories and rules (forward and backward)."""

import dataclasses
import datetime
import zoneinfo
from io import StringIO
from unittest import mock

from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from comics.achievements import engine, medals, registry
from comics.achievements.catalogue import BUILTIN_CODES, CATEGORIES
from comics.achievements.registry import Kind, builtin_rules, generic_rules, get_rule
from comics.achievements.windows import ALL_WINDOW_KINDS, CalendarWindow, WindowKind
from comics.forms import AchievementCreateForm
from comics.models import (
    GENERATED_CODE_PREFIX,
    Achievement,
    AchievementCategory,
    AchievementTier,
    UserAchievement,
)
from comics.roles import ADMINISTRATORS

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import image_upload, make_achievement, make_category, make_user
from .test_achievements import achievement, held

LOCAL = zoneinfo.ZoneInfo("America/Monterrey")


def windowed(code: str, **fields: object) -> Achievement:
    """An achievement with the generic login_in_window rule (no window unless given)."""
    return make_achievement(code=code, rule="login_in_window", **fields)


def christmas(code: str = "christmas", **fields: object) -> Achievement:
    return windowed(
        code,
        calendar_window=CalendarWindow.YEARLY,
        window_start_day=24,
        window_start_month=12,
        window_end_day=6,
        window_end_month=1,
        **fields,
    )


class RegistryTests(ComiTestCase):
    def test_generic_and_builtin_rules(self) -> None:
        self.assertEqual(set(generic_rules()), {"login_in_window", "purchases_each_month_in_window"})
        self.assertNotIn("login_in_window", builtin_rules())
        rule = get_rule("login_in_window")
        self.assertEqual((rule.kind, rule.needs_window, rule.counted), (Kind.BOOLEAN, True, False))
        self.assertEqual(rule.window_kinds, ALL_WINDOW_KINDS)
        summer = get_rule("purchases_each_month_in_window")
        self.assertEqual(summer.window_kinds, {WindowKind.YEARLY})

    def test_halloween_is_a_window_on_the_generic_rule(self) -> None:
        halloween = achievement("halloween")
        self.assertEqual(halloween.rule, "login_in_window")
        self.assertEqual(
            (halloween.calendar_window, halloween.window_start_day, halloween.window_start_month),
            ("yearly", 31, 10),
        )
        self.assertEqual((halloween.window_end_day, halloween.window_end_month), (31, 10))
        generic_rows = {"halloween", "madrugador", "ave_nocturna", "verano"}
        others = Achievement.objects.exclude(code__in=generic_rows)
        self.assertEqual(set(others.values_list("code", flat=True)), set(others.values_list("rule", flat=True)))


class LoginInWindowTests(ComiTestCase):
    def setUp(self) -> None:
        with self.committed():
            self.user = make_user(username="hero", password="pass-1234")

    def sign_in_at(self, moment: datetime.datetime) -> None:
        with mock.patch("django.utils.timezone.now", return_value=moment), self.committed():
            self.assertTrue(self.client.login(username="hero", password="pass-1234"))

    def test_signing_in_inside_the_window(self) -> None:
        christmas()
        self.sign_in_at(datetime.datetime(2025, 12, 31, 23, 0, tzinfo=LOCAL))
        self.assertEqual(held(self.user, "christmas"), 1)
        self.assertEqual(held(self.user, "halloween"), 0)

    def test_signing_in_outside_the_window(self) -> None:
        christmas()
        self.sign_in_at(datetime.datetime(2026, 1, 7, 0, 30, tzinfo=LOCAL))
        self.assertEqual(held(self.user, "christmas"), 0)

    def test_an_achievement_without_window_is_never_awarded(self) -> None:
        windowed("anytime")
        self.sign_in_at(datetime.datetime(2025, 12, 31, 23, 0, tzinfo=LOCAL))
        self.assertEqual(held(self.user, "anytime"), 0)
        self.user.refresh_from_db()
        self.assertEqual(get_rule("login_in_window").progress_for(self.user, achievement("anytime")), 0)

    def test_rows_sharing_the_rule_are_judged_by_their_own_window(self) -> None:
        christmas()
        windowed(
            "night_owl",
            calendar_window=CalendarWindow.NONE,
            window_start_time=datetime.time(2),
            window_end_time=datetime.time(5),
        )
        self.sign_in_at(datetime.datetime(2025, 12, 25, 3, 0, tzinfo=LOCAL))
        self.assertEqual((held(self.user, "christmas"), held(self.user, "night_owl")), (1, 1))
        other = make_user()
        other.last_login = datetime.datetime(2025, 7, 1, 3, 0, tzinfo=LOCAL)
        engine.evaluate(other)
        self.assertEqual((held(other, "christmas"), held(other, "night_owl")), (0, 1))

    def test_a_builtin_rule_shared_by_rows_is_measured_once(self) -> None:
        copy = make_achievement(code="great_power_copy", rule="great_power")
        AchievementTier.objects.create(achievement=copy, level=1, threshold=1)
        AchievementTier.objects.filter(achievement__code="great_power", level=1).update(threshold=1)
        rule = get_rule("great_power")
        counter = mock.Mock(return_value=1)
        with mock.patch.dict(registry._RULES, {"great_power": dataclasses.replace(rule, progress=counter)}):
            raised = engine.evaluate(self.user, ["great_power"])
        counter.assert_called_once_with(self.user)
        self.assertEqual(sorted(raised), ["great_power", "great_power_copy"])

    def test_disabled_windowed_achievements_are_not_awarded(self) -> None:
        christmas(is_enabled=False)
        self.sign_in_at(datetime.datetime(2025, 12, 25, 12, 0, tzinfo=LOCAL))
        self.assertEqual(held(self.user, "christmas"), 0)

    def test_halloween_parity(self) -> None:
        # Same answers as the former halloween rule (see test_catalogue for the time zone cases).
        self.sign_in_at(datetime.datetime(2025, 10, 31, 23, 30, tzinfo=LOCAL))
        self.assertEqual(held(self.user, "halloween"), 1)
        late = make_user(username="late", password="pass-1234")
        next_day = datetime.datetime(2025, 11, 1, 0, 0, tzinfo=LOCAL)
        with mock.patch("django.utils.timezone.now", return_value=next_day), self.committed():
            self.assertTrue(self.client.login(username="late", password="pass-1234"))
        self.assertEqual(held(late, "halloween"), 0)

    def test_retroactive_runs_only_see_the_last_sign_in(self) -> None:
        christmas()
        user = make_user(last_login=datetime.datetime(2025, 12, 24, 20, 0, tzinfo=LOCAL))
        moved_on = make_user(last_login=datetime.datetime(2026, 2, 1, 20, 0, tzinfo=LOCAL))
        call_command("award_achievements", "--code", "christmas", stdout=StringIO(), stderr=StringIO())
        self.assertEqual((held(user, "christmas"), held(moved_on, "christmas")), (1, 0))


class CreateGenericAchievementTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.url = reverse("manage_achievement_new")

    def data(self, **overrides: str) -> dict[str, str]:
        values = {
            "rule": "login_in_window",
            "name_en": "Christmas Eve",
            "name_es": "Nochebuena",
            "description_en": "Sign in on Christmas Eve.",
            "category": str(AchievementCategory.objects.get(code="community").pk),
            "display_order": "30",
            "is_enabled": "on",
            "calendar_window": "yearly",
            "window_start_day": "24",
            "window_start_month": "12",
            "window_end_day": "24",
            "window_end_month": "12",
            "tiers-TOTAL_FORMS": "0",
            "tiers-INITIAL_FORMS": "0",
        }
        values.update(overrides)
        return values

    def test_pages_need_the_gestion_permission(self) -> None:
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
        self.assertFalse(Achievement.objects.exclude(code__in=BUILTIN_CODES).exists())

    def test_the_form_offers_only_generic_rules_and_their_windows(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [key for key, _label in response.context["form"].fields["rule"].choices],
            ["login_in_window", "purchases_each_month_in_window"],
        )
        self.assertContains(response, 'name="calendar_window"')
        self.assertContains(response, "This achievement is earned or not: it has a single level.")
        self.assertContains(self.client.get(reverse("manage_achievements")), self.url)

    def test_create(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.data())
        self.assertRedirects(response, reverse("manage_achievements"), fetch_redirect_response=False)
        created = achievement("g_christmas_eve")
        self.assertEqual(
            (created.rule, created.category.code, created.name_es),
            ("login_in_window", "community", "Nochebuena"),
        )
        self.assertEqual((created.window_start_day, created.window_start_month), (24, 12))
        self.assertFalse(created.tiers.exists())
        entry = LogEntry.objects.get(object_id=str(created.pk), content_type__model="achievement")
        self.assertEqual(entry.action_flag, ADDITION)

    def test_creating_awards_whoever_already_qualifies(self) -> None:
        early = make_user(last_login=datetime.datetime(2025, 12, 24, 9, 0, tzinfo=LOCAL))
        with self.committed():
            self.client.post(self.url, self.data())
        self.assertEqual(held(early, "g_christmas_eve"), 1)

    def test_generated_codes_are_namespaced_and_unique(self) -> None:
        self.client.post(self.url, self.data())
        self.client.post(self.url, self.data())
        self.client.post(self.url, self.data(name_en="Origin"))  # never the built-in "origin"
        codes = set(Achievement.objects.exclude(code__in=BUILTIN_CODES).values_list("code", flat=True))
        self.assertEqual(codes, {"g_christmas_eve", "g_christmas_eve_2", "g_origin"})
        self.assertEqual(medals.icon_source(achievement("g_origin")), medals.IconSource.GENERIC)

    def test_builtin_codes_never_use_the_generated_prefix(self) -> None:
        self.assertFalse([code for code in BUILTIN_CODES if code.startswith(GENERATED_CODE_PREFIX)])
        self.assertFalse([item["code"] for item in CATEGORIES if item["code"].startswith(GENERATED_CODE_PREFIX)])

    def test_a_code_taken_meanwhile_is_generated_again(self) -> None:
        # Another request creates the same name between validation and saving.
        stolen: list[str] = []
        original = AchievementCreateForm.assign_code

        def assign_then_steal(form: AchievementCreateForm) -> None:
            original(form)
            if not stolen:  # only the first time: the retry gets a free code
                stolen.append(form.instance.code)
                make_achievement(code=form.instance.code, rule="login_in_window", name_es="Otro")

        with mock.patch.object(AchievementCreateForm, "assign_code", autospec=True, side_effect=assign_then_steal):
            response = self.client.post(self.url, self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(achievement("g_christmas_eve_2").name_es, "Nochebuena")
        self.assertEqual(achievement("g_christmas_eve").name_es, "Otro")

    def test_invalid_posts_create_nothing(self) -> None:
        cases = {
            "no window": self.data(calendar_window="none"),
            "impossible day": self.data(window_start_day="30", window_start_month="2"),
            "built-in rule": self.data(rule="origin"),
            "no name": self.data(name_en=""),
            "levels on a yes/no rule": self.data(
                **{"tiers-TOTAL_FORMS": "1", "tiers-0-level": "1", "tiers-0-threshold": "5"}
            ),
        }
        for label, data in cases.items():
            with self.subTest(label):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors or response.context["tiers"].non_form_errors())
        self.assertEqual(Achievement.objects.count(), len(BUILTIN_CODES))

    def test_icon_image_on_create(self) -> None:
        response = self.client.post(self.url, {**self.data(), "icon_image": image_upload("icon.png")})
        self.assertEqual(response.status_code, 302)
        self.assertRegex(achievement("g_christmas_eve").icon_image.name, r"^achievements/achievement_g_christmas_eve_")


class EditAndDeleteGenericAchievementTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.created = christmas()

    def edit_data(self, target: Achievement, **overrides: str) -> dict[str, str]:
        values = {
            "name_en": target.name_en,
            "name_es": target.name_es,
            "category": str(target.category_id),
            "display_order": str(target.display_order),
            "is_enabled": "on",
            "calendar_window": target.calendar_window,
            "window_start_day": str(target.window_start_day or ""),
            "window_start_month": str(target.window_start_month or ""),
            "window_end_day": str(target.window_end_day or ""),
            "window_end_month": str(target.window_end_month or ""),
            "tiers-TOTAL_FORMS": "0",
            "tiers-INITIAL_FORMS": "0",
        }
        values.update(overrides)
        return values

    def test_windows_are_edited_only_for_windowed_rules(self) -> None:
        url = reverse("manage_achievement_edit", args=[achievement("halloween").pk])
        self.assertContains(self.client.get(url), 'name="window_start_day"')
        response = self.client.post(
            url, self.edit_data(achievement("halloween"), window_end_day="1", window_end_month="11")
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual((achievement("halloween").window_end_day, achievement("halloween").window_end_month), (1, 11))
        great_power = reverse("manage_achievement_edit", args=[achievement("great_power").pk])
        self.assertNotContains(self.client.get(great_power), 'name="window_start_day"')

    def test_changing_a_window_reevaluates_everyone(self) -> None:
        user = make_user(last_login=datetime.datetime(2025, 11, 30, 12, 0, tzinfo=LOCAL))
        url = reverse("manage_achievement_edit", args=[self.created.pk])
        with self.committed():
            self.client.post(url, self.edit_data(self.created, window_start_day="30", window_start_month="11"))
        self.assertEqual(held(user, "christmas"), 1)

    def test_delete_an_unearned_achievement_created_in_gestion(self) -> None:
        AchievementTier.objects.create(achievement=self.created, level=1, threshold=1)  # goes with it
        url = reverse("manage_achievement_edit", args=[self.created.pk])
        self.assertContains(self.client.get(url), reverse("manage_achievement_delete", args=[self.created.pk]))
        response = self.client.post(reverse("manage_achievement_delete", args=[self.created.pk]))
        self.assertRedirects(response, reverse("manage_achievements"), fetch_redirect_response=False)
        self.assertFalse(Achievement.objects.filter(code="christmas").exists())
        self.assertFalse(AchievementTier.objects.filter(achievement_id=self.created.pk).exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.created.pk), action_flag=DELETION).exists())

    def test_earned_achievements_can_only_be_disabled(self) -> None:
        UserAchievement.objects.award(make_user(), self.created, 1)
        url = reverse("manage_achievement_edit", args=[self.created.pk])
        page = self.client.get(url)
        self.assertNotContains(page, reverse("manage_achievement_delete", args=[self.created.pk]))
        self.assertContains(page, "Someone has already earned it")
        response = self.client.post(reverse("manage_achievement_delete", args=[self.created.pk]), follow=True)
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(Achievement.objects.filter(code="christmas").exists())
        self.assertFalse(LogEntry.objects.filter(object_id=str(self.created.pk), action_flag=DELETION).exists())

    def test_builtin_achievements_cannot_be_deleted(self) -> None:
        for code in ["halloween", "origin"]:
            with self.subTest(code=code):
                target = achievement(code)
                page = self.client.get(reverse("manage_achievement_edit", args=[target.pk]))
                self.assertNotContains(page, reverse("manage_achievement_delete", args=[target.pk]))
                self.client.post(reverse("manage_achievement_delete", args=[target.pk]))
                self.assertTrue(Achievement.objects.filter(code=code).exists())

    def test_moving_it_out_of_a_disabled_category_awards_whoever_qualifies(self) -> None:
        # login_in_window only sees the last sign-in: waiting for the next one would lose it.
        hidden = make_category(is_enabled=False)
        Achievement.objects.filter(pk=self.created.pk).update(category=hidden)
        user = make_user(last_login=datetime.datetime(2025, 12, 25, 12, 0, tzinfo=LOCAL))
        engine.evaluate(user)
        self.assertEqual(held(user, "christmas"), 0)
        target = achievement("christmas")
        community = AchievementCategory.objects.get(code="community")
        url = reverse("manage_achievement_edit", args=[target.pk])
        with self.committed():
            response = self.client.post(url, self.edit_data(target, category=str(community.pk)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(held(user, "christmas"), 1)

    def test_saving_without_becoming_active_evaluates_nobody(self) -> None:
        target = achievement("christmas")
        url = reverse("manage_achievement_edit", args=[target.pk])
        with mock.patch("comics.views.manage_achievements.schedule_for_everyone") as schedule:
            self.client.post(url, self.edit_data(target, name_en="Christmas!"))
        schedule.assert_not_called()

    def test_a_deletion_race_is_refused(self) -> None:
        # Earned between the check and the delete: the database's foreign key refuses it.
        with mock.patch.object(Achievement, "delete", side_effect=IntegrityError("fk")):
            response = self.client.post(reverse("manage_achievement_delete", args=[self.created.pk]), follow=True)
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(Achievement.objects.filter(code="christmas").exists())

    def test_delete_needs_the_gestion_permission_and_post(self) -> None:
        url = reverse("manage_achievement_delete", args=[self.created.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(make_user())
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(Achievement.objects.filter(code="christmas").exists())

    def test_list_flags(self) -> None:
        windowed("broken")  # a windowed rule without a window (only possible outside Gestión)
        make_achievement(code="orphan", rule="no_such_rule")
        Achievement.objects.filter(code="origin").delete()
        response = self.client.get(reverse("manage_achievements"))
        rows = {item.code: item for item in response.context["achievements"]}
        self.assertTrue(rows["broken"].missing_window)
        self.assertFalse(rows["christmas"].missing_window)
        self.assertFalse(rows["christmas"].is_builtin)
        self.assertIsNone(rows["orphan"].rule_info)
        self.assertEqual(response.context["rules_without_row"], ["origin"])  # generic rules need no row
        self.assertContains(response, "No time window")
        self.assertContains(response, "Created in Gestión")


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class CategoryAndRuleMigrationTests(TransactionTestCase):
    """0100-0102 forward and backward on the test database. Migrating runs DDL, so this runs
    outside a transaction; the database is always left at the latest migration."""

    serialized_rollback = True
    before = [("comics", "0099_seed_more_achievements")]
    after = [("comics", "0102_achievement_category_foreign_key")]

    def migrate(self, targets: list[tuple[str, str]]):  # noqa: ANN201  (historical apps registry)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_and_backward(self) -> None:
        old = self.migrate(self.before)
        old_achievement = old.get_model("comics", "Achievement")
        old_achievement.objects.filter(code="halloween").update(name_es="Editada")
        old_achievement.objects.create(code="custom", name_en="Custom", name_es="Propio", category="odd_value")

        new = self.migrate(self.after)
        new_achievement = new.get_model("comics", "Achievement")
        rows = {row.code: row for row in new_achievement.objects.select_related("category")}
        self.assertEqual(rows["veteran"].category.code, "seniority")
        self.assertEqual(rows["origin"].category.name_es, "Colección")
        self.assertEqual(rows["custom"].category.code, "odd_value")  # unknown values get their own row
        self.assertEqual({row.code for row in rows.values() if row.rule == row.code}, set(rows) - {"halloween"})
        halloween = rows["halloween"]
        self.assertEqual((halloween.rule, halloween.name_es), ("login_in_window", "Editada"))
        self.assertEqual(
            (halloween.calendar_window, halloween.window_start_day, halloween.window_end_month), ("yearly", 31, 10)
        )

        old = self.migrate(self.before)
        old_achievement = old.get_model("comics", "Achievement")
        values = dict(old_achievement.objects.values_list("code", "category"))
        self.assertEqual(
            (values["veteran"], values["halloween"], values["custom"]), ("seniority", "community", "odd_value")
        )
        self.assertEqual(old_achievement.objects.get(code="halloween").name_es, "Editada")
        field_names = {field.name for field in old_achievement._meta.get_fields()}
        self.assertNotIn("rule", field_names)

        self.migrate(self.after)
        self.assertEqual(new.get_model("comics", "Achievement").objects.get(code="halloween").rule, "login_in_window")
