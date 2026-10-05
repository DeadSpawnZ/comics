"""Achievement engine: rules, tiers, triggers after commit, retroactive awarding, notices, profile
display (earned, locked, secret) and the Gestión pages."""

import datetime
import importlib
import io
from io import StringIO
from unittest import mock

from django.contrib.admin.models import CHANGE, LogEntry
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from PIL import Image

from comics.achievements import catalogue, engine
from comics.achievements.display import showcase
from comics.achievements.registry import Event, all_rules, codes_for
from comics.models import Achievement, AchievementTier, Collection, Profile, UserAchievement
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_user

SEEDED = {
    "origin",
    "great_power",
    "veteran",
    "secret_identity",
    "infinity_gauntlet",
    "batcave_vault",
    "multiverse",
    "speed_force",
    "lazarus_pit",
    "assemble",
    "chronicler",
    "halloween",
}
COLLECTION_RULES = {
    "origin",
    "great_power",
    "infinity_gauntlet",
    "batcave_vault",
    "multiverse",
    "speed_force",
    "lazarus_pit",
    "assemble",
    "chronicler",
}
# What a browser sends when it loads a page (notices are only announced on page loads).
PAGE = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
seed_migration = importlib.import_module("comics.migrations.0097_seed_achievements")
more_seed_migration = importlib.import_module("comics.migrations.0099_seed_more_achievements")


def achievement(code: str) -> Achievement:
    return Achievement.objects.get(code=code)


def held(user: object, code: str) -> int:
    """Level `user` holds of `code` (0 = not earned)."""
    award = UserAchievement.objects.filter(user=user, achievement__code=code).first()
    return award.level if award else 0


def set_thresholds(code: str, *thresholds: int) -> None:
    target = achievement(code)
    target.tiers.all().delete()
    for level, threshold in enumerate(thresholds, start=1):
        AchievementTier.objects.create(achievement=target, level=level, threshold=threshold)


def buy(user: object, edition: object = None, **fields: object) -> Collection:
    return make_collection(collector=user, edition=edition or make_edition(), **fields)


class SeedTests(ComiTestCase):
    def test_migration_seeds_one_row_per_rule(self) -> None:
        self.assertEqual(set(Achievement.objects.values_list("code", flat=True)), SEEDED)
        self.assertEqual(set(all_rules()), SEEDED)
        self.assertEqual(list(achievement("great_power").tiers.values_list("threshold", flat=True)), [100, 500, 1000])
        self.assertEqual(list(achievement("veteran").tiers.values_list("threshold", flat=True)), [1, 3, 5])
        self.assertTrue(achievement("secret_identity").is_secret)
        self.assertFalse(achievement("origin").tiers.exists())

    def test_frozen_seed_matches_the_catalogue(self) -> None:
        self.assertEqual(seed_migration.ACHIEVEMENTS + more_seed_migration.ACHIEVEMENTS, catalogue.ACHIEVEMENTS)

    def test_ensure_catalogue_only_creates_what_is_missing(self) -> None:
        Achievement.objects.filter(code="origin").delete()
        Achievement.objects.filter(code="great_power").update(name_es="Editado")
        self.assertEqual(catalogue.ensure_catalogue(), ["origin"])
        self.assertEqual(catalogue.ensure_catalogue(), [])
        self.assertEqual(achievement("great_power").name_es, "Editado")

    def test_texts_follow_the_language_with_fallback(self) -> None:
        great_power = achievement("great_power")
        with translation.override("es"):
            self.assertEqual(great_power.name, "Un gran poder…")
            great_power.name_es = ""
            self.assertEqual(great_power.name, "With great power…")
        with translation.override("en"):
            self.assertEqual(achievement("origin").name, "Origin")


class EngineTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        set_thresholds("great_power", 1, 2, 3)

    def test_awards_are_idempotent(self) -> None:
        buy(self.user)
        self.assertEqual(sorted(engine.evaluate(self.user)), ["great_power", "origin"])
        first = UserAchievement.objects.get(user=self.user, achievement__code="origin")
        self.assertEqual(engine.evaluate(self.user), [])
        self.assertEqual(UserAchievement.objects.filter(user=self.user).count(), 2)
        self.assertEqual(UserAchievement.objects.get(pk=first.pk).awarded_at, first.awarded_at)

    def test_tiers_progress_and_keep_the_first_date(self) -> None:
        buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        award = UserAchievement.objects.get(user=self.user, achievement__code="great_power")
        self.assertEqual(award.level, 1)
        buy(self.user)
        buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        raised = UserAchievement.objects.get(pk=award.pk)
        self.assertEqual(raised.level, 3)
        self.assertEqual(raised.awarded_at, award.awarded_at)
        self.assertGreaterEqual(raised.level_reached_at, award.level_reached_at)

    def test_levels_skip_straight_to_the_reached_one(self) -> None:
        for _piece in range(3):
            buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        self.assertEqual(held(self.user, "great_power"), 3)

    def test_earned_achievements_are_never_revoked(self) -> None:
        pieces = [buy(self.user) for _piece in range(2)]
        Profile.objects.filter(user=self.user).update(is_public=False)
        engine.evaluate(self.user)
        Collection.objects.filter(pk__in=[piece.pk for piece in pieces]).delete()
        Profile.objects.filter(user=self.user).update(is_public=True)
        engine.evaluate(self.user)
        self.assertEqual(held(self.user, "great_power"), 2)
        self.assertEqual(held(self.user, "secret_identity"), 1)
        self.assertEqual(held(self.user, "origin"), 1)

    def test_sold_pieces_do_not_count_as_collection(self) -> None:
        bought = buy(self.user)
        buy(self.user, trade_type=Collection.TradeChoices.SELLING, previous_trade=bought, edition=bought.edition)
        engine.evaluate(self.user, ["great_power"])
        self.assertEqual(held(self.user, "great_power"), 0)

    def test_disabled_achievements_are_not_awarded(self) -> None:
        Achievement.objects.filter(code="origin").update(is_enabled=False)
        buy(self.user)
        engine.evaluate(self.user)
        self.assertEqual(held(self.user, "origin"), 0)
        self.assertEqual(held(self.user, "great_power"), 1)

    def test_count_rule_without_tiers_is_never_awarded(self) -> None:
        achievement("great_power").tiers.all().delete()
        buy(self.user)
        engine.evaluate(self.user, ["great_power"])
        self.assertEqual(held(self.user, "great_power"), 0)

    def test_rows_without_rule_and_rules_without_row(self) -> None:
        Achievement.objects.create(code="ghost", name_en="Ghost", name_es="Fantasma")
        Achievement.objects.filter(code="origin").delete()
        buy(self.user)
        with self.assertLogs("comics.achievements.engine", "WARNING") as logs:
            engine.evaluate(self.user)
        self.assertIn("'origin' has no Achievement row", logs.output[0])
        self.assertFalse(UserAchievement.objects.filter(achievement__code__in=["ghost", "origin"]).exists())

    def test_veteran_counts_full_years(self) -> None:
        cases = [(364, 0), (366, 1), (365 * 3 + 2, 2), (365 * 6, 3)]
        for days, level in cases:
            with self.subTest(days=days):
                user = make_user(date_joined=timezone.now() - datetime.timedelta(days=days))
                engine.evaluate(user, ["veteran"])
                self.assertEqual(held(user, "veteran"), level)

    def test_award_only_moves_up(self) -> None:
        great_power = achievement("great_power")
        self.assertTrue(UserAchievement.objects.award(self.user, great_power, 2))
        self.assertFalse(UserAchievement.objects.award(self.user, great_power, 2))
        self.assertFalse(UserAchievement.objects.award(self.user, great_power, 1))
        self.assertEqual(held(self.user, "great_power"), 2)
        self.assertTrue(UserAchievement.objects.award(self.user, great_power, 3))
        self.assertEqual(UserAchievement.objects.filter(user=self.user).count(), 1)


class TriggerTests(ComiTestCase):
    """Fixtures are created inside self.committed() (see ComiTestCase)."""

    def setUp(self) -> None:
        with self.committed():
            self.user = make_user(username="hero", password="pass-1234")
        set_thresholds("great_power", 1, 2)

    def test_evaluation_runs_after_commit(self) -> None:
        with self.captureOnCommitCallbacks() as callbacks:
            buy(self.user)
            self.assertEqual(held(self.user, "origin"), 0)  # nothing until the commit
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual((held(self.user, "origin"), held(self.user, "great_power")), (1, 1))

    def test_rolled_back_changes_award_nothing(self) -> None:
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    buy(self.user)
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.assertEqual(held(self.user, "origin"), 0)

    def test_deleting_a_sale_can_raise_progress(self) -> None:
        with self.committed():
            bought = buy(self.user)
            sale = buy(
                self.user, trade_type=Collection.TradeChoices.SELLING, previous_trade=bought, edition=bought.edition
            )
        self.assertEqual(held(self.user, "great_power"), 0)
        with self.captureOnCommitCallbacks(execute=True):
            sale.delete()
        self.assertEqual(held(self.user, "great_power"), 1)

    def test_profile_privacy_toggle(self) -> None:
        with self.committed():
            self.client.force_login(self.user)  # signing in schedules its own (login) evaluation
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("profile"), {"section": "privacy"})
        self.assertEqual(held(self.user, "secret_identity"), 1)

    def test_login_evaluates_date_based_rules(self) -> None:
        self.user.date_joined = timezone.now() - datetime.timedelta(days=400)
        self.user.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(self.client.login(username="hero", password="pass-1234"))
        self.assertEqual(held(self.user, "veteran"), 1)

    def test_events_only_evaluate_their_rules(self) -> None:
        self.assertEqual(codes_for(Event.COLLECTION), COLLECTION_RULES)
        self.assertEqual(codes_for(Event.SIGNATURE), {"batcave_vault"})
        with mock.patch.object(engine, "evaluate") as evaluate, self.captureOnCommitCallbacks(execute=True):
            buy(self.user)
        evaluate.assert_called_once()
        self.assertEqual(set(evaluate.call_args.args[1]), COLLECTION_RULES)

    def test_one_evaluation_per_user_and_transaction(self) -> None:
        with self.committed():
            other = make_user()
        with (
            mock.patch.object(engine, "evaluate") as evaluate,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            for _piece in range(3):
                buy(self.user)
            buy(other)
            Profile.objects.get(user=self.user).save()  # another event of the same user joins
        self.assertEqual(len(callbacks), 2)
        calls = {call.args[0].pk: set(call.args[1]) for call in evaluate.call_args_list}
        self.assertEqual(calls, {self.user.pk: COLLECTION_RULES | {"secret_identity"}, other.pk: COLLECTION_RULES})

    def test_unexpected_pending_callback_shapes_fall_back_to_a_new_evaluation(self) -> None:
        unexpected = ("not", "the", "expected", "shape", "at", "all")
        with self.committed():
            connection.run_on_commit.append(unexpected)
            connection.run_on_commit.append(("short",))
            piece = buy(self.user)  # the save still works
            for entry in [unexpected, ("short",)]:
                connection.run_on_commit.remove(entry)  # captureOnCommitCallbacks unpacks every entry
        self.assertTrue(Collection.objects.filter(pk=piece.pk).exists())
        self.assertEqual(held(self.user, "origin"), 1)

    def test_events_outside_a_transaction_are_not_merged(self) -> None:
        with (
            mock.patch.object(engine.transaction, "get_connection") as get_connection,
            mock.patch.object(engine.transaction, "on_commit") as on_commit,
        ):
            get_connection.return_value.in_atomic_block = False
            engine.schedule(Event.COLLECTION, self.user.pk)
            engine.schedule(Event.COLLECTION, self.user.pk)
        self.assertEqual(on_commit.call_count, 2)

    def test_callbacks_are_robust(self) -> None:
        with mock.patch("comics.achievements.engine.transaction.on_commit") as on_commit:
            engine.schedule(Event.COLLECTION, self.user.pk)
        self.assertTrue(on_commit.call_args.kwargs["robust"])

    def test_loaddata_saves_schedule_nothing(self) -> None:
        bought = buy(self.user)
        with self.captureOnCommitCallbacks() as callbacks:
            Collection.save_base(bought, raw=True)
        self.assertEqual(callbacks, [])


class RetroactiveAndNoticeTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="hero")
        set_thresholds("great_power", 1, 2)
        buy(self.user)
        buy(self.user)

    def run_command(self, *args: str) -> str:
        out = StringIO()
        call_command("award_achievements", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_command_awards_existing_activity_once(self) -> None:
        other = make_user()
        self.assertIn("awarded or raised: 2.", self.run_command())
        self.assertEqual((held(self.user, "origin"), held(self.user, "great_power")), (1, 2))
        self.assertTrue(UserAchievement.objects.filter(user=self.user).first().bulk_awarded)
        self.assertEqual(held(other, "origin"), 0)
        self.assertIn("awarded or raised: 0.", self.run_command())

    def test_command_filters(self) -> None:
        self.run_command("--user", "hero", "--code", "origin")
        self.assertEqual((held(self.user, "origin"), held(self.user, "great_power")), (1, 0))
        with self.assertRaises(CommandError):
            self.run_command("--code", "nope")
        with self.assertRaises(CommandError):
            self.run_command("--user", "nobody")

    def test_retroactive_awards_are_announced_in_one_message(self) -> None:
        self.run_command()
        self.assertEqual(engine.take_notices(self.user), ["You unlocked 2 achievements: see them on your profile."])
        self.assertEqual(engine.take_notices(self.user), [])

    def test_live_awards_are_announced_one_by_one(self) -> None:
        engine.evaluate(self.user)
        notices = engine.take_notices(self.user)
        self.assertCountEqual(
            notices, ["Achievement unlocked: Origin", "Achievement unlocked: With great power… (level 2)"]
        )

    def test_disabled_achievements_are_not_announced(self) -> None:
        engine.evaluate(self.user)
        Achievement.objects.filter(code="origin").update(is_enabled=False)
        self.assertEqual(engine.take_notices(self.user), ["Achievement unlocked: With great power… (level 2)"])
        self.assertFalse(UserAchievement.objects.filter(user=self.user, notified=False).exists())

    def test_nothing_pending_costs_one_query(self) -> None:
        with self.assertNumQueries(1):
            self.assertEqual(engine.take_notices(self.user), [])

    def test_next_page_shows_the_notice_once(self) -> None:
        engine.evaluate(self.user, ["origin"])
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("comics"), headers=PAGE), "Achievement unlocked: Origin")
        self.assertNotContains(self.client.get(reverse("comics"), headers=PAGE), "Achievement unlocked")

    def test_notices_wait_for_a_page_load(self) -> None:
        engine.evaluate(self.user, ["origin"])
        self.client.get(reverse("login"), headers=PAGE)  # anonymous
        self.client.force_login(self.user)
        edition = make_edition()
        requests = [
            ("post", reverse("profile"), PAGE),  # not a GET
            ("get", "/media/avatars/none.jpg", PAGE),  # media files
            ("get", reverse("javascript-catalog"), PAGE),  # translation catalog, even asking for HTML
            ("get", f"{reverse('comics_api_editions')}?publishing={edition.publishing_id}", PAGE),  # */api/*
            ("get", reverse("get_publishing_date", args=[edition.publishing_id]), PAGE),  # /ajax/
            ("get", reverse("comics"), {"accept": "*/*"}),  # fetch() and scripts
            ("get", reverse("comics"), {}),
        ]
        for method, url, headers in requests:
            with self.subTest(method=method, url=url, headers=headers):
                getattr(self.client, method)(url, headers=headers)
                self.assertTrue(UserAchievement.objects.filter(user=self.user, notified=False).exists())

    def test_a_page_costs_one_query_for_notices(self) -> None:
        self.client.force_login(self.user)
        page = reverse("collectables")
        self.client.get(page, headers=PAGE)  # warm up (session, caches)
        with CaptureQueriesContext(connection) as with_notices:
            self.client.get(page, headers=PAGE)
        with CaptureQueriesContext(connection) as without_notices:
            self.client.get(page)
        self.assertEqual(len(with_notices) - len(without_notices), 1)

    def test_an_award_claimed_by_another_request_is_not_announced_again(self) -> None:
        engine.evaluate(self.user)
        ids = engine._pending_ids(self.user)
        # Another request claims them between this one's read and its claim.
        UserAchievement.objects.filter(pk__in=ids).update(notified=True)
        with mock.patch.object(engine, "_pending_ids", return_value=ids):
            self.assertEqual(engine.take_notices(self.user), [])

    def test_two_takes_announce_each_award_once(self) -> None:
        engine.evaluate(self.user)
        first = engine.take_notices(self.user)
        second = engine.take_notices(self.user)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])


class DisplayTests(ComiTestCase):
    def setUp(self) -> None:
        self.owner = make_user(username="owner")
        self.viewer = make_user(username="viewer")
        set_thresholds("great_power", 1, 3)
        buy(self.owner)
        buy(self.owner)
        engine.evaluate(self.owner)

    def own_page(self) -> str:
        self.client.force_login(self.owner)
        return self.client.get(reverse("profile")).content.decode()

    def test_owner_sees_earned_locked_progress_and_concealed_secrets(self) -> None:
        html = self.own_page()
        self.assertIn("Origin", html)
        self.assertIn('class="achievement-level"', html)  # great_power x1 of 2 levels
        self.assertIn("2 / 3", html)  # progress to the next level
        self.assertIn("Veteran", html)  # locked but visible
        self.assertIn("???", html)
        self.assertIn("Some heroes prefer to stay in the shadows.", html)  # the secret's hint
        self.assertNotIn("Secret identity", html)
        self.assertNotIn("Lois Lane", html)

    def test_secret_without_hint_reveals_nothing(self) -> None:
        Achievement.objects.filter(code="secret_identity").update(hint_en="", hint_es="")
        html = self.own_page()
        self.assertIn("Secret achievement.", html)
        self.assertNotIn("shadows", html)

    def test_earned_secret_is_shown(self) -> None:
        Profile.objects.filter(user=self.owner).update(is_public=False)
        engine.evaluate(self.owner)
        self.assertIn("Secret identity", self.own_page())

    def test_groups_count_completion(self) -> None:
        groups = {
            group.label: (group.earned, group.total) for group in showcase(self.owner, include_locked=True).groups
        }
        self.assertEqual(
            groups,
            {"Collection": (2, 5), "Treasures": (0, 2), "Sagas": (0, 2), "Community": (0, 2), "Seniority": (0, 1)},
        )

    def test_other_users_only_see_earned_achievements(self) -> None:
        self.client.force_login(self.viewer)
        html = self.client.get(reverse("user_profile", args=["owner"])).content.decode()
        self.assertIn("Origin", html)
        self.assertNotIn("Veteran", html)
        self.assertNotIn("???", html)
        self.assertNotIn(" / 3", html)
        cards = [card for group in showcase(self.owner, include_locked=False).groups for card in group.cards]
        self.assertTrue(all(card.earned for card in cards))

    def test_disabled_achievements_are_hidden_everywhere(self) -> None:
        Achievement.objects.filter(code__in=["origin", "veteran"]).update(is_enabled=False)
        self.assertNotIn("Origin", self.own_page())
        self.assertNotIn("Veteran", self.own_page())
        self.client.force_login(self.viewer)
        self.assertNotIn("Origin", self.client.get(reverse("user_profile", args=["owner"])).content.decode())

    def test_rows_without_rule_are_not_shown(self) -> None:
        Achievement.objects.create(code="ghost", name_en="Ghostly", name_es="Fantasmal")
        self.assertNotIn("Ghostly", self.own_page())

    def test_icon_image_replaces_the_symbol(self) -> None:
        origin = achievement("origin")
        origin.icon_image = SimpleUploadedFile("icon.png", png_bytes(), content_type="image/png")
        origin.save()
        self.assertIn(origin.icon_image.url, self.own_page())


def png_bytes(size: tuple[int, int] = (300, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (10, 120, 200, 100)).save(buffer, format="PNG")
    return buffer.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class GestionTests(ComiTestCase):
    def setUp(self) -> None:
        self.manager = make_user(role=ADMINISTRATORS)
        self.client.force_login(self.manager)
        self.great_power = achievement("great_power")
        self.url = reverse("manage_achievement_edit", args=[self.great_power.pk])

    def form_data(self, target: Achievement, tiers: list[tuple[int, int]] | None = None, **overrides: str) -> dict:
        data = {
            "name_en": target.name_en,
            "name_es": target.name_es,
            "description_en": target.description_en,
            "description_es": target.description_es,
            "hint_en": target.hint_en,
            "hint_es": target.hint_es,
            "category": target.category,
            "icon": target.icon,
            "display_order": str(target.display_order),
        }
        if target.is_enabled:
            data["is_enabled"] = "on"
        if target.is_secret:
            data["is_secret"] = "on"
        # Like the page: every saved level is posted (the ones not in `tiers` marked for deletion),
        # then the new ones.
        existing = list(target.tiers.all())
        rows = tiers if tiers is not None else [(tier.level, tier.threshold) for tier in existing]
        total = max(len(existing), len(rows))
        data.update({"tiers-TOTAL_FORMS": str(total), "tiers-INITIAL_FORMS": str(len(existing))})
        data.update({"tiers-MIN_NUM_FORMS": "0", "tiers-MAX_NUM_FORMS": "1000"})
        for index in range(total):
            prefix = f"tiers-{index}"
            data[f"{prefix}-achievement"] = str(target.pk)
            if index < len(existing):
                data[f"{prefix}-id"] = str(existing[index].pk)
            if index < len(rows):
                data[f"{prefix}-level"], data[f"{prefix}-threshold"] = str(rows[index][0]), str(rows[index][1])
            else:
                tier = existing[index]
                data[f"{prefix}-level"], data[f"{prefix}-threshold"] = str(tier.level), str(tier.threshold)
                data[f"{prefix}-DELETE"] = "on"
        data.update(overrides)
        return data

    def test_pages_need_the_gestion_permission(self) -> None:
        for url in [reverse("manage_achievements"), self.url]:
            with self.subTest(url=url):
                self.client.force_login(make_user())
                self.assertEqual(self.client.get(url).status_code, 403)
                self.client.force_login(self.manager)
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_list_shows_counts_and_flags(self) -> None:
        UserAchievement.objects.award(make_user(), self.great_power, 1)
        Achievement.objects.create(code="ghost", name_en="Ghost", name_es="Fantasma")
        Achievement.objects.filter(code="veteran").delete()
        achievement("origin")  # unchanged
        response = self.client.get(reverse("manage_achievements"))
        rows = {item.code: item for item in response.context["achievements"]}
        self.assertEqual(rows["great_power"].earned_by, 1)
        self.assertIsNone(rows["ghost"].rule)
        self.assertContains(response, "No rule in code")
        self.assertEqual(response.context["rules_without_row"], ["veteran"])

    def test_list_flags_count_rules_without_levels(self) -> None:
        self.great_power.tiers.all().delete()
        self.assertContains(self.client.get(reverse("manage_achievements")), "No levels")

    def test_edit_saves_texts_and_levels_and_logs_it(self) -> None:
        data = self.form_data(self.great_power, tiers=[(1, 10), (2, 50)], name_es="Gran poder", code="hacked")
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse("manage_achievements"), fetch_redirect_response=False)
        saved = Achievement.objects.get(pk=self.great_power.pk)
        self.assertEqual((saved.code, saved.name_es), ("great_power", "Gran poder"))
        self.assertEqual(list(saved.tiers.values_list("level", "threshold")), [(1, 10), (2, 50)])
        entry = LogEntry.objects.get(object_id=str(saved.pk), content_type__model="achievement")
        self.assertEqual(entry.action_flag, CHANGE)

    def test_levels_can_swap_thresholds(self) -> None:
        set_thresholds("great_power", 5, 10)
        target = achievement("great_power")
        tiers = list(target.tiers.all())
        data = self.form_data(target, tiers=[(2, 10), (1, 5)])
        data["tiers-0-id"], data["tiers-1-id"] = str(tiers[0].pk), str(tiers[1].pk)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(list(target.tiers.values_list("level", "threshold")), [(1, 5), (2, 10)])

    def test_invalid_levels(self) -> None:
        cases = {
            "gap": [(1, 10), (3, 50)],
            "repeat": [(1, 10), (1, 50)],
            "not increasing": [(1, 50), (2, 50)],
            "none": [],
        }
        for label, rows in cases.items():
            with self.subTest(label):
                response = self.client.post(self.url, self.form_data(self.great_power, tiers=rows))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["tiers"].non_form_errors())
        self.assertEqual(list(self.great_power.tiers.values_list("threshold", flat=True)), [100, 500, 1000])

    def test_yes_no_achievements_take_no_levels(self) -> None:
        origin = achievement("origin")
        url = reverse("manage_achievement_edit", args=[origin.pk])
        response = self.client.post(url, self.form_data(origin, tiers=[(1, 5)]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(origin.tiers.exists())
        self.assertEqual(self.client.post(url, self.form_data(origin)).status_code, 302)

    def test_rejected_post_keeps_the_saved_name_in_the_header(self) -> None:
        response = self.client.post(self.url, self.form_data(self.great_power, name_en="", icon="Bad Icon!"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("name_en", response.context["form"].errors)
        self.assertIn("icon", response.context["form"].errors)
        self.assertEqual(response.context["achievement"].name_en, "With great power…")

    def test_icon_image_is_normalized_to_a_square_png(self) -> None:
        upload = SimpleUploadedFile("badge.png", png_bytes(), content_type="image/png")
        response = self.client.post(self.url, {**self.form_data(self.great_power), "icon_image": upload})
        self.assertEqual(response.status_code, 302)
        saved = Achievement.objects.get(pk=self.great_power.pk)
        self.assertRegex(saved.icon_image.name, r"^achievements/achievement_great_power_[0-9a-f]{16}\.png$")
        with default_storage.open(saved.icon_image.name) as fh, Image.open(fh) as img:
            self.assertEqual((img.format, img.size, img.mode), ("PNG", (256, 256), "RGBA"))

    def test_svg_and_other_formats_are_rejected(self) -> None:
        gif = io.BytesIO()
        Image.new("RGB", (8, 8)).save(gif, format="GIF")
        uploads = {
            "svg": SimpleUploadedFile("icon.svg", SVG, content_type="image/svg+xml"),
            "svg named png": SimpleUploadedFile("icon.png", SVG, content_type="image/png"),
            "gif": SimpleUploadedFile("icon.gif", gif.getvalue(), content_type="image/gif"),
        }
        for label, upload in uploads.items():
            with self.subTest(label):
                response = self.client.post(self.url, {**self.form_data(self.great_power), "icon_image": upload})
                self.assertEqual(response.status_code, 200)
                self.assertIn("icon_image", response.context["form"].errors)
        self.assertFalse(Achievement.objects.get(pk=self.great_power.pk).icon_image)

    def test_reevaluating_everyone_survives_a_failing_user(self) -> None:
        users = [make_user(), make_user()]
        for user in users:
            buy(user)
        real_evaluate = engine.evaluate
        failing = users[0] if users[0].pk < users[1].pk else users[1]

        def evaluate(user: object, codes: object) -> list[str]:
            if user.pk == failing.pk:
                raise RuntimeError("boom")
            return real_evaluate(user, codes)

        with (
            mock.patch.object(engine, "evaluate", side_effect=evaluate),
            self.assertLogs("comics.achievements.engine", "ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            engine.schedule_for_everyone("origin")
        self.assertIn("failed", logs.output[0])
        survivor = users[1] if failing is users[0] else users[0]
        self.assertEqual((held(failing, "origin"), held(survivor, "origin")), (0, 1))

    def test_enabling_or_changing_levels_reevaluates_everyone(self) -> None:
        user = make_user()
        buy(user)
        Achievement.objects.filter(pk=self.great_power.pk).update(is_enabled=False)
        target = achievement("great_power")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.form_data(target, tiers=[(1, 1)], is_enabled="on"))
        self.assertEqual(held(user, "great_power"), 1)

    @override_settings(LANGUAGE_CODE="es")
    def test_history_lists_achievement_changes(self) -> None:
        self.client.post(self.url, self.form_data(self.great_power))
        response = self.client.get(reverse("manage_home"), {"tipo": "achievement"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["days"]), 1)
