"""Achievement categories managed in Gestión: seeded rows, list with counts, create and edit (code
from the name, medallion upload), delete protection, the grouped achievement list, the history,
and what a disabled category does to its achievements."""

import importlib
import re
from unittest import mock

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.urls import reverse
from PIL import Image

from comics.achievements import catalogue, engine
from comics.achievements.display import showcase
from comics.forms import AchievementCategoryForm
from comics.models import Achievement, AchievementCategory, UserAchievement
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import image_upload, make_achievement, make_category, make_collection, make_edition, make_user
from .test_achievements import SVG, achievement, held, png_bytes

category_migration = importlib.import_module("comics.migrations.0101_seed_achievement_categories")
random_migration = importlib.import_module("comics.migrations.0103_seed_random_achievements")


def category(code: str) -> AchievementCategory:
    return AchievementCategory.objects.get(code=code)


class SeededCategoryTests(ComiTestCase):
    def test_the_former_choices_are_rows_with_their_labels(self) -> None:
        rows = list(AchievementCategory.objects.exclude(code="random").values_list("code", "name_en", "name_es"))
        self.assertEqual(
            rows,
            [
                ("collection", "Collection", "Colección"),
                ("treasures", "Treasures", "Tesoros"),
                ("sagas", "Sagas", "Sagas"),
                ("community", "Community", "Comunidad"),
                ("seniority", "Seniority", "Antigüedad"),
            ],
        )
        self.assertEqual(achievement("veteran").category.code, "seniority")
        self.assertEqual(achievement("halloween").category.code, "community")

    def test_frozen_categories_match_the_catalogue(self) -> None:
        self.assertEqual(category_migration.CATEGORIES + random_migration.CATEGORIES, catalogue.CATEGORIES)

    def test_ensure_catalogue_recreates_missing_categories(self) -> None:
        Achievement.objects.filter(category__code="seniority").delete()
        category("seniority").delete()
        self.assertEqual(catalogue.ensure_catalogue(), ["veteran"])
        self.assertEqual(achievement("veteran").category.name_es, "Antigüedad")


class CategoryGestionTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def data(self, **overrides: str) -> dict[str, str]:
        values = {
            "name_en": "Special events",
            "name_es": "Eventos especiales",
            "display_order": "60",
            "is_enabled": "on",
        }
        values.update(overrides)
        return values

    def test_pages_need_the_gestion_permission(self) -> None:
        target = category("sagas")
        urls = [
            reverse("manage_achievement_categories"),
            reverse("manage_achievement_category_new"),
            reverse("manage_achievement_category_edit", args=[target.pk]),
        ]
        self.client.force_login(make_user())
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("manage_achievement_category_new"), self.data()).status_code, 403)
        delete_url = reverse("manage_achievement_category_delete", args=[make_category().pk])
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        self.assertEqual(AchievementCategory.objects.count(), len(catalogue.CATEGORIES) + 1)

    def test_list_shows_the_achievement_counts(self) -> None:
        make_category(name_en="Empty one", display_order=99)
        response = self.client.get(reverse("manage_achievement_categories"))
        listed = [item.code for item in response.context["categories"]]
        self.assertEqual(listed[:5], ["collection", "treasures", "sagas", "community", "seniority"])
        counts = {item.code: item.achievement_count for item in response.context["categories"]}
        self.assertEqual(counts["collection"], 5)
        self.assertEqual(counts["seniority"], 1)
        self.assertContains(response, "5 achievements")
        self.assertContains(response, "Empty one")
        self.assertContains(response, "/static/achievements/_category.svg")

    def test_create_generates_the_code_and_logs_it(self) -> None:
        response = self.client.post(reverse("manage_achievement_category_new"), self.data())
        self.assertRedirects(response, reverse("manage_achievement_categories"), fetch_redirect_response=False)
        created = category("g_special_events")
        self.assertEqual((created.name_es, created.display_order, created.is_enabled), ("Eventos especiales", 60, True))
        entry = LogEntry.objects.get(object_id=str(created.pk), content_type__model="achievementcategory")
        self.assertEqual(entry.action_flag, ADDITION)
        # Same name again: another code. Accents and symbols are dropped from codes.
        self.client.post(reverse("manage_achievement_category_new"), self.data())
        self.client.post(reverse("manage_achievement_category_new"), self.data(name_en="Época ¡dorada!"))
        self.assertTrue(AchievementCategory.objects.filter(code="g_special_events_2").exists())
        self.assertTrue(AchievementCategory.objects.filter(code="g_epoca_dorada").exists())

    def test_a_code_taken_meanwhile_is_generated_again(self) -> None:
        stolen: list[str] = []
        original = AchievementCategoryForm.assign_code

        def assign_then_steal(form: AchievementCategoryForm) -> None:
            original(form)
            if not stolen:  # only the first time: the retry gets a free code
                stolen.append(form.instance.code)
                make_category(code=form.instance.code)

        with mock.patch.object(AchievementCategoryForm, "assign_code", autospec=True, side_effect=assign_then_steal):
            response = self.client.post(reverse("manage_achievement_category_new"), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(category("g_special_events_2").name_es, "Eventos especiales")

    def test_a_deletion_race_is_refused(self) -> None:
        empty = make_category()
        with mock.patch.object(AchievementCategory, "delete", side_effect=IntegrityError("fk")):
            response = self.client.post(reverse("manage_achievement_category_delete", args=[empty.pk]), follow=True)
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(AchievementCategory.objects.filter(pk=empty.pk).exists())

    def test_names_are_required(self) -> None:
        response = self.client.post(reverse("manage_achievement_category_new"), self.data(name_en=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("name_en", response.context["form"].errors)
        self.assertEqual(AchievementCategory.objects.count(), len(catalogue.CATEGORIES))

    def test_edit_keeps_the_code_and_normalizes_the_medallion(self) -> None:
        target = category("sagas")
        url = reverse("manage_achievement_category_edit", args=[target.pk])
        upload = SimpleUploadedFile("medal.png", png_bytes(), content_type="image/png")
        response = self.client.post(url, {**self.data(name_en="Big sagas"), "code": "hacked", "image": upload})
        self.assertEqual(response.status_code, 302)
        saved = category("sagas")
        self.assertEqual(saved.name_en, "Big sagas")
        self.assertRegex(saved.image.name, r"^achievement_categories/category_sagas_[0-9a-f]{16}\.png$")
        with default_storage.open(saved.image.name) as fh, Image.open(fh) as img:
            self.assertEqual((img.format, img.size), ("PNG", (256, 256)))
        self.assertEqual(LogEntry.objects.get(object_id=str(saved.pk)).action_flag, CHANGE)
        self.assertContains(self.client.get(reverse("manage_achievement_categories")), saved.image.url)

    def test_svg_medallions_are_rejected(self) -> None:
        url = reverse("manage_achievement_category_edit", args=[category("sagas").pk])
        upload = SimpleUploadedFile("medal.png", SVG, content_type="image/png")
        response = self.client.post(url, {**self.data(), "image": upload})
        self.assertEqual(response.status_code, 200)
        self.assertIn("image", response.context["form"].errors)

    def test_only_empty_categories_can_be_deleted(self) -> None:
        empty = make_category(image=image_upload("medal.png"))
        stored = AchievementCategory.objects.get(pk=empty.pk).image.name
        page = self.client.get(reverse("manage_achievement_category_edit", args=[empty.pk]))
        self.assertContains(page, reverse("manage_achievement_category_delete", args=[empty.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("manage_achievement_category_delete", args=[empty.pk]))
        self.assertRedirects(response, reverse("manage_achievement_categories"), fetch_redirect_response=False)
        self.assertFalse(AchievementCategory.objects.filter(pk=empty.pk).exists())
        self.assertFalse(default_storage.exists(stored))
        self.assertTrue(LogEntry.objects.filter(object_id=str(empty.pk), action_flag=DELETION).exists())

        used = category("sagas")
        page = self.client.get(reverse("manage_achievement_category_edit", args=[used.pk]))
        self.assertNotContains(page, reverse("manage_achievement_category_delete", args=[used.pk]))
        self.assertContains(page, "It has achievements")
        response = self.client.post(reverse("manage_achievement_category_delete", args=[used.pk]), follow=True)
        self.assertContains(response, "cannot be deleted")
        self.assertTrue(AchievementCategory.objects.filter(pk=used.pk).exists())
        self.assertFalse(LogEntry.objects.filter(object_id=str(used.pk), action_flag=DELETION).exists())

    def test_achievement_list_is_grouped_by_category_in_order(self) -> None:
        first = make_category(name_en="Zeta group", display_order=1)  # first by its order, not its name
        make_achievement(code="custom", name_en="Custom one", category=first)
        html = self.client.get(reverse("manage_achievements")).content.decode()
        headings = re.findall(r'id="achievement-category-\d+">([^<]+)</h2>', html)
        self.assertEqual(
            headings, ["Zeta group", "Collection", "Treasures", "Sagas", "Community", "Seniority", "Random"]
        )
        self.assertLess(html.index("Custom one"), html.index('">Collection</h2>'))

    def test_achievement_edit_moves_it_to_another_category(self) -> None:
        target = achievement("origin")
        url = reverse("manage_achievement_edit", args=[target.pk])
        page = self.client.get(url)
        self.assertContains(page, 'name="category"')
        data = {
            "name_en": target.name_en,
            "name_es": target.name_es,
            "category": str(category("treasures").pk),
            "display_order": "1",
            "is_enabled": "on",
            "tiers-TOTAL_FORMS": "0",
            "tiers-INITIAL_FORMS": "0",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(achievement("origin").category.code, "treasures")

    def test_history_links_category_changes(self) -> None:
        self.client.post(reverse("manage_achievement_category_new"), self.data())
        created = category("g_special_events")
        response = self.client.get(reverse("manage_home"), {"tipo": "achievementcategory"})
        self.assertContains(response, reverse("manage_achievement_category_edit", args=[created.pk]))


class DisabledCategoryTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="hero")
        make_collection(collector=self.user, edition=make_edition())

    def test_a_disabled_category_hides_and_stops_its_achievements(self) -> None:
        AchievementCategory.objects.filter(code="collection").update(is_enabled=False)
        engine.evaluate(self.user)
        self.assertEqual(held(self.user, "origin"), 0)
        shown = showcase(self.user, include_locked=True)
        codes = {card.achievement.code for card in [*shown.earned, *shown.locked]}
        self.assertNotIn("origin", codes)
        self.assertIn("veteran", codes)
        self.assertEqual(shown.total, Achievement.objects.exclude(category__code="collection").count())

    def test_announcements_skip_disabled_categories(self) -> None:
        UserAchievement.objects.award(self.user, achievement("origin"), 1)
        AchievementCategory.objects.filter(code="collection").update(is_enabled=False)
        self.assertEqual(engine.take_notices(self.user), [])

    def test_enabling_it_again_awards_whoever_qualifies(self) -> None:
        collection = category("collection")
        AchievementCategory.objects.filter(pk=collection.pk).update(is_enabled=False)
        self.client.force_login(make_user(role=ADMINISTRATORS))
        data = {"name_en": "Collection", "name_es": "Colección", "display_order": "10", "is_enabled": "on"}
        with self.committed():
            response = self.client.post(reverse("manage_achievement_category_edit", args=[collection.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(held(self.user, "origin"), 1)
