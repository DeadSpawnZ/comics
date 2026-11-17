"""The Random category: novato (registered), aplicado (whole profile), fotogenico (profile
picture), madrugador and ave_nocturna (sign-in time windows on the generic login_in_window rule)
and verano (purchases in July and August of the same year, on the generic
purchases_each_month_in_window rule): rules, triggers, window validation, and the migrations
that seed them and drop the icon names."""

import datetime
import zoneinfo
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from comics.achievements import catalogue, engine
from comics.achievements.registry import Event, get_rule, rule_keys_for
from comics.achievements.windows import CalendarWindow, yearly_months
from comics.forms import AchievementCreateForm
from comics.models import Achievement, AchievementCategory, Collection, Profile

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import image_upload, make_achievement, make_collection, make_edition, make_user
from .test_achievements import achievement, held

LOCAL = zoneinfo.ZoneInfo("America/Monterrey")
RANDOM = ["novato", "aplicado", "fotogenico", "madrugador", "ave_nocturna", "verano"]  # seeded by 0103
# The category now: 0111 added reportero (tested with the change requests).
RANDOM_NOW = [*RANDOM, "reportero"]
BUYING, SELLING = Collection.TradeChoices.BUYING, Collection.TradeChoices.SELLING


def progress(code: str, user: object) -> int:
    row = achievement(code)
    return get_rule(row.rule).progress_for(user, row)


class SeedTests(ComiTestCase):
    def test_the_random_category_and_its_achievements(self) -> None:
        random = AchievementCategory.objects.get(code="random")
        self.assertEqual((random.name_en, random.name_es), ("Random", "Random"))
        self.assertEqual(list(AchievementCategory.objects.values_list("code", flat=True))[-1], "random")
        rows = {row.code: row for row in Achievement.objects.filter(category=random)}
        self.assertEqual(set(rows), set(RANDOM_NOW))
        self.assertFalse(any(row.is_secret for row in rows.values()))
        self.assertEqual(rows["novato"].name_es, "Novato")
        self.assertEqual(
            {code: rows[code].rule for code in ["madrugador", "ave_nocturna", "verano"]},
            {
                "madrugador": "login_in_window",
                "ave_nocturna": "login_in_window",
                "verano": "purchases_each_month_in_window",
            },
        )
        self.assertEqual(
            (rows["madrugador"].window_start_time, rows["madrugador"].window_end_time),
            (datetime.time(7), datetime.time(8)),
        )
        self.assertEqual(rows["madrugador"].calendar_window, CalendarWindow.NONE)
        verano = rows["verano"]
        self.assertEqual(
            (verano.calendar_window, verano.window_start_day, verano.window_start_month),
            ("yearly", 1, 7),
        )
        self.assertEqual((verano.window_end_day, verano.window_end_month), (31, 8))

    def test_ensure_catalogue_recreates_them(self) -> None:
        Achievement.objects.filter(code__in=RANDOM_NOW).delete()
        AchievementCategory.objects.filter(code="random").delete()
        self.assertEqual(sorted(catalogue.ensure_catalogue()), sorted(RANDOM_NOW))
        self.assertEqual(achievement("ave_nocturna").window_start_time, datetime.time(1))


class NovatoTests(ComiTestCase):
    def test_every_registered_user(self) -> None:
        self.assertEqual(progress("novato", make_user()), 1)
        self.assertEqual(rule_keys_for(Event.ACCOUNT), {"novato"})

    def test_registering_awards_it_after_commit(self) -> None:
        data = {
            "username": "newreader",
            "email": "new@example.com",
            "password1": "a-Long-unusual-pass-42",
            "password2": "a-Long-unusual-pass-42",
        }
        with self.captureOnCommitCallbacks() as callbacks:
            self.client.post(reverse("register"), data)
            user = Profile.objects.get(user__username="newreader").user
            self.assertEqual(held(user, "novato"), 0)  # nothing until the commit
        for callback in callbacks:
            callback()
        self.assertEqual(held(user, "novato"), 1)

    def test_any_new_account(self) -> None:
        with self.committed():
            user = make_user()
        self.assertEqual(held(user, "novato"), 1)


class ProfileAchievementTests(ComiTestCase):
    def setUp(self) -> None:
        with self.committed():
            self.user = make_user(username="hero", password="pass-1234")
        self.profile = Profile.objects.get(user=self.user)

    def complete(self) -> None:
        self.user.first_name, self.user.last_name, self.user.email = "Ana", "López", "ana@example.com"
        self.user.save()
        self.profile.phone = "811 234 5678"
        self.profile.avatar = image_upload("me.png")
        self.profile.save()

    def test_aplicado_needs_every_field(self) -> None:
        self.complete()
        self.assertEqual(progress("aplicado", self.user), 1)
        for field in ["first_name", "last_name", "email", "phone", "avatar"]:
            with self.subTest(missing=field):
                user = type(self.user).objects.get(pk=self.user.pk)
                if field in ("phone", "avatar"):
                    Profile.objects.filter(pk=self.profile.pk).update(**{field: ""})
                else:
                    setattr(user, field, "  ")  # blank counts as empty
                self.assertEqual(progress("aplicado", user), 0)
                self.complete()

    def test_fotogenico_needs_a_picture(self) -> None:
        self.assertEqual(progress("fotogenico", self.user), 0)
        self.profile.avatar = image_upload("me.png")
        self.profile.save()
        self.assertEqual(progress("fotogenico", self.user), 1)

    def test_the_profile_page_awards_them(self) -> None:
        with self.committed():
            self.client.force_login(self.user)
        info = {"first_name": "Ana", "last_name": "López", "email": "ana@example.com", "phone": "811 234 5678"}
        with self.committed():
            self.client.post(reverse("profile"), {"section": "info", **info})
        self.assertEqual((held(self.user, "aplicado"), held(self.user, "fotogenico")), (0, 0))
        with self.committed():
            self.client.post(reverse("profile"), {"section": "avatar", "avatar": image_upload("me.png")})
        self.assertEqual((held(self.user, "aplicado"), held(self.user, "fotogenico")), (1, 1))

    def test_personal_info_saved_elsewhere_reevaluates_too(self) -> None:
        # E.g. the admin: the names live on the user, not on the profile.
        Profile.objects.filter(pk=self.profile.pk).update(phone="811 234 5678", avatar="avatars/me.jpg")
        self.user.email = "ana@example.com"
        self.user.first_name, self.user.last_name = "Ana", "López"
        with self.committed():
            self.user.save()
        self.assertEqual(held(self.user, "aplicado"), 1)

    def test_signing_in_is_not_a_profile_change(self) -> None:
        with mock.patch.object(engine, "evaluate") as evaluate, self.committed():
            self.client.login(username="hero", password="pass-1234")
        self.assertEqual(set(evaluate.call_args.args[1]), rule_keys_for(Event.LOGIN))

    def test_clearing_the_data_keeps_them(self) -> None:
        self.complete()
        engine.evaluate(self.user)
        Profile.objects.filter(pk=self.profile.pk).update(avatar="", phone="")
        engine.evaluate(self.user)
        self.assertEqual((held(self.user, "aplicado"), held(self.user, "fotogenico")), (1, 1))


class SignInWindowTests(ComiTestCase):
    def check(self, code: str, cases: dict[str, bool]) -> None:
        user = make_user()
        for moment, expected in cases.items():
            with self.subTest(code=code, moment=moment):
                hour = datetime.time.fromisoformat(moment)
                user.last_login = datetime.datetime.combine(datetime.date(2025, 5, 5), hour, tzinfo=LOCAL)
                self.assertEqual(progress(code, user), int(expected))

    def test_madrugador_from_7_to_8(self) -> None:
        self.check("madrugador", {"06:59:59": False, "07:00": True, "07:59:59": True, "08:00": False, "19:30": False})

    def test_ave_nocturna_from_1_to_3(self) -> None:
        self.check("ave_nocturna", {"00:59:59": False, "01:00": True, "02:59:59": True, "03:00": False, "13:30": False})

    def test_windows_are_in_the_project_time_zone(self) -> None:
        user = make_user(last_login=datetime.datetime(2025, 5, 5, 13, 30, tzinfo=datetime.UTC))  # 07:30 locally
        self.assertEqual(progress("madrugador", user), 1)

    def test_signing_in_early_awards_madrugador(self) -> None:
        with self.committed():
            make_user(username="early", password="pass-1234")
        early = datetime.datetime(2025, 5, 5, 7, 15, tzinfo=LOCAL)
        with mock.patch("django.utils.timezone.now", return_value=early), self.committed():
            self.assertTrue(self.client.login(username="early", password="pass-1234"))
        user = type(make_user()).objects.get(username="early")
        self.assertEqual((held(user, "madrugador"), held(user, "ave_nocturna")), (1, 0))


class VeranoTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()

    def buy(self, day: datetime.date, **fields: object) -> Collection:
        fields.setdefault("edition", make_edition())
        return make_collection(collector=self.user, trade_date=day, **fields)

    def test_july_and_august_of_the_same_year(self) -> None:
        self.buy(datetime.date(2024, 7, 1))
        self.assertEqual(progress("verano", self.user), 0)  # July only
        self.buy(datetime.date(2023, 8, 31))
        self.assertEqual(progress("verano", self.user), 0)  # August of another year
        self.buy(datetime.date(2024, 8, 31))
        self.assertEqual(progress("verano", self.user), 1)

    def test_august_only(self) -> None:
        self.buy(datetime.date(2024, 8, 10))
        self.buy(datetime.date(2024, 9, 1))
        self.buy(datetime.date(2024, 6, 30))
        self.assertEqual(progress("verano", self.user), 0)

    def test_sold_purchases_count_and_sales_do_not(self) -> None:
        july = self.buy(datetime.date(2024, 7, 15))
        self.buy(datetime.date(2024, 8, 20), trade_type=SELLING, previous_trade=july, edition=july.edition)
        self.assertEqual(progress("verano", self.user), 0)  # a sale is not a purchase
        august = self.buy(datetime.date(2024, 8, 21))
        self.buy(datetime.date(2024, 9, 1), trade_type=SELLING, previous_trade=august, edition=august.edition)
        self.assertEqual(progress("verano", self.user), 1)  # both purchases count, sold or not

    def test_a_row_without_a_yearly_window_never_counts(self) -> None:
        self.buy(datetime.date(2024, 7, 1))
        self.buy(datetime.date(2024, 8, 1))
        rule = get_rule("purchases_each_month_in_window")
        for fields in [
            {},
            {"calendar_window": CalendarWindow.ABSOLUTE, "window_start_date": datetime.date(2024, 7, 1)},
            {"window_start_time": datetime.time(1), "window_end_time": datetime.time(3)},
        ]:
            with self.subTest(fields=fields):
                row = Achievement(code="x", rule=rule.key, **fields)
                self.assertEqual(rule.progress_for(self.user, row), 0)

    def test_a_window_wrapping_the_year_end(self) -> None:
        row = make_achievement(
            code="g_winter",
            rule="purchases_each_month_in_window",
            calendar_window=CalendarWindow.YEARLY,
            window_start_day=15,
            window_start_month=12,
            window_end_day=15,
            window_end_month=1,
        )
        rule = get_rule(row.rule)
        self.buy(datetime.date(2024, 1, 10))
        self.buy(datetime.date(2024, 12, 20))
        self.assertEqual(rule.progress_for(self.user, row), 0)  # January belongs to the previous winter
        self.buy(datetime.date(2024, 12, 10))  # before the window starts
        self.buy(datetime.date(2025, 1, 20))  # after it ends
        self.assertEqual(rule.progress_for(self.user, row), 0)
        self.buy(datetime.date(2025, 1, 15))
        self.assertEqual(rule.progress_for(self.user, row), 1)

    def test_months_of_a_yearly_window(self) -> None:
        def months(start: tuple[int, int], end: tuple[int, int]) -> list[int]:
            row = Achievement(
                calendar_window=CalendarWindow.YEARLY,
                window_start_day=start[0],
                window_start_month=start[1],
                window_end_day=end[0],
                window_end_month=end[1],
            )
            return yearly_months(row)

        self.assertEqual(months((1, 7), (31, 8)), [7, 8])
        self.assertEqual(months((24, 12), (6, 1)), [12, 1])
        self.assertEqual(months((1, 3), (31, 3)), [3])
        self.assertEqual(months((15, 7), (10, 7)), [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6])

    def test_one_query(self) -> None:
        for month in [7, 8, 7, 8]:
            self.buy(datetime.date(2024, month, 3))
        user = self.user
        row = achievement("verano")
        with self.assertNumQueries(1):
            self.assertEqual(get_rule(row.rule).progress_for(user, row), 1)

    def test_registering_the_purchases_awards_it(self) -> None:
        with self.committed():
            self.user = make_user()
        with self.committed():
            self.buy(datetime.date(2022, 7, 31))
            self.buy(datetime.date(2022, 8, 1))
        self.assertEqual(held(self.user, "verano"), 1)


class WindowKindValidationTests(ComiTestCase):
    def form(self, **overrides: str) -> AchievementCreateForm:
        data = {
            "rule": "purchases_each_month_in_window",
            "name_en": "Winter",
            "name_es": "Invierno",
            "category": str(AchievementCategory.objects.get(code="random").pk),
            "display_order": "0",
            "is_enabled": "on",
            "calendar_window": "yearly",
            "window_start_day": "21",
            "window_start_month": "12",
            "window_end_day": "20",
            "window_end_month": "3",
        }
        data.update(overrides)
        return AchievementCreateForm(data, rule=get_rule("purchases_each_month_in_window"))

    def test_a_yearly_window_is_valid(self) -> None:
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_purchases_rule_needs_a_yearly_window(self) -> None:
        form = self.form(calendar_window="none")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), ["This rule needs a yearly date window."])
        form = self.form(calendar_window="absolute", window_start_date="2026-07-01", window_end_date="2026-08-31")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), ["This rule does not use a window between two dates."])

    def test_the_purchases_rule_ignores_times_so_they_are_refused(self) -> None:
        form = self.form(window_start_time="10:00", window_end_time="12:00")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), ["This rule does not use a time-of-day window."])

    def test_the_sign_in_rule_takes_any_kind(self) -> None:
        form = self.form(
            rule="login_in_window", calendar_window="none", window_start_time="07:00", window_end_time="08:00"
        )
        form = AchievementCreateForm(form.data, rule=get_rule("login_in_window"))
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class RandomMigrationTests(TransactionTestCase):
    """0103 (seed Random) and 0104 (drop the icon names), forward and backward. Migrating runs
    DDL, so this runs outside a transaction; the database is always left at the latest migration."""

    serialized_rollback = True
    before = [("comics", "0102_achievement_category_foreign_key")]
    after = [("comics", "0104_remove_achievement_icon")]

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
        user = make_user(username="hero")  # committed right away: gets novato
        self.assertEqual(held(user, "novato"), 1)

        old = self.migrate(self.before)
        old_achievement = old.get_model("comics", "Achievement")
        self.assertFalse(old_achievement.objects.filter(code__in=RANDOM).exists())  # awards deleted with them
        self.assertFalse(old.get_model("comics", "AchievementCategory").objects.filter(code="random").exists())
        icons = dict(old_achievement.objects.values_list("code", "icon"))
        self.assertEqual((icons["halloween"], icons["origin"]), ("skull", "auto_stories"))

        new = self.migrate(self.after)
        new_achievement = new.get_model("comics", "Achievement")
        self.assertEqual(
            set(new_achievement.objects.filter(category__code="random").values_list("code", flat=True)), set(RANDOM)
        )
        self.assertNotIn("icon", {field.name for field in new_achievement._meta.get_fields()})
