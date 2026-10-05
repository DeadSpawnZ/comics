"""Achievement medals: picture resolution (upload, bundled artwork, icon), level rings and marks,
the profile rows with their detail cards (what the owner and other users get, escaping) and the
medal previews in Gestión."""

import json
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import connection
from django.template.loader import render_to_string
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.achievements import catalogue, engine, medals
from comics.achievements.display import showcase
from comics.achievements.medals import (
    IconSource,
    Rim,
    icon_source,
    icon_url,
    medal_for,
    preview_medals,
    rim_for,
    top_medal,
)
from comics.models import Achievement, AchievementTier, Profile
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import image_upload, make_achievement, make_collection, make_edition, make_user
from .test_achievements import achievement, set_thresholds

ARTWORK_DIR = Path(settings.BASE_DIR) / "comics" / "static" / "achievements"
EXTERNAL_URL = re.compile(r"url\((?!\s*['\"]?#)")
SECRET_CODES = ["secret_identity", "lazarus_pit", "halloween"]


def upload_icon(target: Achievement) -> Achievement:
    target.icon_image = image_upload("icon.png")
    target.save()
    return target


class IconResolutionTests(ComiTestCase):
    def setUp(self) -> None:
        medals.bundled_icon.cache_clear()
        self.addCleanup(medals.bundled_icon.cache_clear)

    def test_bundled_artwork_is_used_without_an_upload(self) -> None:
        origin = achievement("origin")
        self.assertEqual(icon_url(origin), "/static/achievements/origin.svg")
        self.assertEqual(icon_source(origin), IconSource.BUNDLED)

    def test_uploaded_image_wins_over_the_bundled_artwork(self) -> None:
        origin = upload_icon(achievement("origin"))
        self.assertEqual(icon_url(origin), origin.icon_image.url)
        self.assertEqual(medal_for(origin, 1).image_url, origin.icon_image.url)
        self.assertEqual(icon_source(origin), IconSource.UPLOAD)

    def test_icon_is_the_last_resort(self) -> None:
        ghost = Achievement(code="ghost", icon="bolt", name_en="Ghost", name_es="Fantasma")
        self.assertIsNone(icon_url(ghost))
        medal = medal_for(ghost, 1)
        self.assertEqual((medal.image_url, medal.symbol), (None, "bolt"))
        self.assertEqual(icon_source(ghost), IconSource.SYMBOL)
        html = render_to_string("includes/achievement_medal.html", {"medal": medal})
        self.assertIn('<span class="medal__symbol material-symbols-outlined">bolt</span>', html)
        self.assertNotIn("<img", html)

    def test_secret_placeholder_never_reveals_the_real_picture(self) -> None:
        for code in SECRET_CODES:
            with self.subTest(code=code):
                secret = achievement(code)
                medal = medal_for(secret, 0, concealed=True)
                self.assertEqual(medal.image_url, "/static/achievements/_secret.svg")
                self.assertEqual((medal.symbol, medal.rim, medal.pill, medal.progress), ("help", Rim.SECRET, None, ""))
                # Not even an uploaded picture.
                upload_icon(secret)
                self.assertEqual(medal_for(secret, 0, concealed=True).image_url, "/static/achievements/_secret.svg")

    def test_odd_codes_are_never_looked_up(self) -> None:
        with mock.patch.object(medals.finders, "find") as find:
            for code in ["../settings", "a/b", "_secret", "Origin", ""]:
                self.assertIsNone(medals.bundled_icon(code))
        find.assert_not_called()

    def test_the_existence_check_is_cached(self) -> None:
        with mock.patch.object(medals.finders, "find", wraps=finders.find) as find:
            for _ in range(3):
                self.assertEqual(medals.bundled_icon("origin"), "achievements/origin.svg")
            self.assertIsNone(medals.bundled_icon("ghost"))
            self.assertIsNone(medals.bundled_icon("ghost"))
        self.assertEqual(find.call_count, 2)

    def test_manifest_storage_gives_the_hashed_name(self) -> None:
        # Production uses the manifest storage: the URL must be the hashed file collectstatic made.
        hashed = "achievements/origin.0123abcd.svg"
        manifest = {"version": "1.1", "hash": "x", "paths": {"achievements/origin.svg": hashed}}
        storages = {
            "default": settings.STORAGES["default"],
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory(prefix="comi-tests-static-") as static_root:
            Path(static_root, "staticfiles.json").write_text(json.dumps(manifest), encoding="utf-8")
            with override_settings(STORAGES=storages, STATIC_ROOT=static_root, DEBUG=False):
                self.assertEqual(icon_url(achievement("origin")), f"/static/{hashed}")


class BundledArtworkTests(ComiTestCase):
    def test_every_catalogue_code_has_its_artwork(self) -> None:
        # Fails when an achievement is added without its medal: add comics/static/achievements/<code>.svg.
        codes = {item["code"] for item in catalogue.ACHIEVEMENTS}
        for code in sorted(codes) + ["_secret", "_category"]:
            with self.subTest(code=code):
                self.assertIsNotNone(finders.find(f"achievements/{code}.svg"), f"missing achievements/{code}.svg")

    def test_artwork_is_plain_svg(self) -> None:
        files = sorted(ARTWORK_DIR.glob("*.svg"))
        self.assertGreaterEqual(len(files), 14)
        for path in files:
            with self.subTest(file=path.name):
                root = ET.parse(path).getroot()
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                for element in root.iter():
                    self.assertNotIn(element.tag.rsplit("}", 1)[-1], {"script", "foreignObject", "image", "use"})
                    for name, value in element.attrib.items():
                        self.assertFalse(name.lower().startswith("on"), name)
                        self.assertNotIn("http", value if "href" in name else "")
                        # Only references to the file's own ids: url(#disc), never an external url(...).
                        self.assertIsNone(EXTERNAL_URL.search(value), f"{name}={value}")
                    if element.tag.endswith("}style"):
                        self.assertNotIn("@import", element.text or "")
                        self.assertIsNone(EXTERNAL_URL.search(element.text or ""))


class MedalLookTests(ComiTestCase):
    def render(self, medal: medals.Medal) -> str:
        return render_to_string("includes/achievement_medal.html", {"medal": medal})

    def test_rings_per_level(self) -> None:
        self.assertEqual([rim_for(level) for level in range(5)], ["base", "base", "silver", "gold", "gold"])

    def test_ring_and_level_mark_classes(self) -> None:
        great_power = achievement("great_power")
        cases = {
            1: ("medal--base", None),
            2: ("medal--silver", "x2"),
            3: ("medal--gold", "x3"),
            4: ("medal--gold", "x4"),
        }
        for level, (ring, pill) in cases.items():
            with self.subTest(level=level):
                html = self.render(medal_for(great_power, level))
                self.assertIn(f"medal {ring}", html)
                self.assertNotIn("is-locked", html)
                if pill:
                    self.assertIn(f'<span class="medal__pill">{pill}</span>', html)
                else:
                    self.assertNotIn("medal__pill", html)
                self.assertIn('<img src="/static/achievements/great_power.svg"', html)

    def test_achievements_without_levels_use_the_base_ring(self) -> None:
        html = self.render(medal_for(achievement("origin"), 1))
        self.assertIn("medal medal--base", html)
        self.assertNotIn("medal__pill", html)

    def test_locked_medal_is_greyed_out_with_its_progress(self) -> None:
        medal = medal_for(achievement("infinity_gauntlet"), 0, have=2, need=6)
        html = self.render(medal)
        self.assertIn("medal medal--base is-locked", html)
        self.assertIn('<span class="medal__progress">2/6</span>', html)
        self.assertNotIn("medal__pill", html)
        self.assertEqual(self.render(medal_for(achievement("origin"), 0)).count("medal__progress"), 0)

    def test_previews_cover_every_level(self) -> None:
        self.assertEqual([m.rim for m in preview_medals(achievement("great_power"))], ["base", "silver", "gold"])
        self.assertEqual([m.level for m in preview_medals(achievement("origin"))], [1])
        self.assertEqual((top_medal(achievement("great_power")).level, top_medal(achievement("origin")).level), (3, 1))
        self.assertEqual([str(rim.label) for rim in Rim], ["Base", "Silver", "Gold", "Secret"])


class ProfileMedalTests(ComiTestCase):
    def setUp(self) -> None:
        self.owner = make_user(username="owner")
        self.viewer = make_user(username="viewer")
        set_thresholds("great_power", 1, 2, 5)
        for _ in range(2):
            make_collection(collector=self.owner, edition=make_edition())
        engine.evaluate(self.owner)  # origin, great_power x2

    def own_page(self) -> str:
        self.client.force_login(self.owner)
        return self.client.get(reverse("profile")).content.decode()

    def public_page(self) -> str:
        self.client.force_login(self.viewer)
        return self.client.get(reverse("user_profile", args=["owner"])).content.decode()

    def test_owner_sees_both_rows_with_rings_marks_and_progress(self) -> None:
        html = self.own_page()
        self.assertIn("Earned", html)
        self.assertIn("2 of 12", html)
        self.assertIn("Not yet earned", html)
        self.assertIn("medal medal--silver", html)
        self.assertIn('<span class="medal__pill">x2</span>', html)
        self.assertIn('<span class="medal__progress">0/6</span>', html)  # infinity_gauntlet
        self.assertIn('aria-label="Infinity Gauntlet, not earned yet. Progress: 0 of 6"', html)
        self.assertIn('aria-label="Origin. Earned on', html)
        self.assertIn('aria-label="With great power…, level 2 of 3. Earned on', html)
        self.assertIn("/static/achievements/_secret.svg", html)
        self.assertIn('aria-label="Secret achievement. Hint: Some heroes prefer to stay in the shadows."', html)
        self.assertIn("achievement_medals.js", html)

    def test_detail_card_content(self) -> None:
        html = self.own_page()
        self.assertIn("Level 2 of 3 · Silver (comics in the collection: 2)", html)
        self.assertIn("Next level at 5", html)
        self.assertIn("Progress: 2 / 5", html)  # earned, towards the next level
        self.assertIn("Progress: 0 / 6", html)
        self.assertIn('<div class="medal-popover__name">???</div>', html)
        self.assertIn("<span>Collection</span>", html)  # the category, now inside the card

    def test_owner_never_gets_the_real_secret_art_or_texts(self) -> None:
        for code in SECRET_CODES:
            upload_icon(achievement(code))
        html = self.own_page()
        for code in SECRET_CODES:
            target = achievement(code)
            with self.subTest(code=code):
                self.assertNotIn(f"achievements/{code}.svg", html)
                self.assertNotIn(target.icon_image.url, html)
                self.assertNotIn(target.name_en, html)
                self.assertNotIn(target.description_en, html)

    def test_other_users_get_only_earned_medals(self) -> None:
        html = self.public_page()
        self.assertIn("/static/achievements/origin.svg", html)
        self.assertIn("/static/achievements/great_power.svg", html)
        self.assertIn('<span class="medal__pill">x2</span>', html)
        self.assertIn("Level 2 of 3 · Silver", html)
        for leak in [
            "Not yet earned",
            "medal__progress",
            "Progress",
            "is-locked",
            "???",
            "_secret.svg",
            "veteran.svg",
            "infinity_gauntlet.svg",
            "Some heroes",
            "Secret achievement",
            "Lois Lane",
        ]:
            with self.subTest(leak=leak):
                self.assertNotIn(leak, html)

    def test_private_profiles_show_no_medals_to_others(self) -> None:
        Profile.objects.filter(user=self.owner).update(is_public=False)
        html = self.public_page()
        self.assertNotIn("medal-btn", html)
        self.assertNotIn("origin.svg", html)

    def test_disabled_achievements_show_no_medal(self) -> None:
        Achievement.objects.filter(code="origin").update(is_enabled=False)
        self.assertNotIn("origin.svg", self.own_page())
        self.assertNotIn("origin.svg", self.public_page())

    def test_gestion_texts_are_escaped_everywhere(self) -> None:
        Achievement.objects.filter(code="origin").update(
            name_en='<img src=x onerror="alert(1)">', description_en="<script>alert(2)</script>"
        )
        Achievement.objects.filter(code="secret_identity").update(hint_en='<b onmouseover="alert(3)">hint</b>')
        AchievementTier.objects.filter(achievement__code="great_power", level=2).update(label_en="<i>x</i>")
        for html in [self.own_page(), self.public_page()]:
            for raw in ['<img src=x onerror="alert(1)">', "<script>alert(2)</script>", "<b onmouseover", "<i>x</i>"]:
                self.assertNotIn(raw, html)
            self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html)
            self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", html)
            self.assertIn("&lt;i&gt;x&lt;/i&gt;", html)
        self.assertIn("&lt;b onmouseover=&quot;alert(3)&quot;&gt;hint&lt;/b&gt;", self.own_page())

    def test_showcase_queries_do_not_depend_on_the_medals(self) -> None:
        # Pictures are resolved without queries: achievements, their tiers and the user's awards.
        with self.assertNumQueries(3):
            showcase(self.owner, include_locked=False)
        upload_icon(achievement("origin"))
        with self.assertNumQueries(3):
            showcase(self.owner, include_locked=False)


class GestionMedalTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def edit_page(self, target: Achievement) -> str:
        return self.client.get(reverse("manage_achievement_edit", args=[target.pk])).content.decode()

    def test_list_shows_each_medal_at_its_top_level(self) -> None:
        html = self.client.get(reverse("manage_achievements")).content.decode()
        self.assertIn("medal medal--gold medal--sm", html)  # great_power, three levels
        self.assertIn('<span class="medal__pill">x3</span>', html)
        self.assertIn("/static/achievements/great_power.svg", html)
        # Gestión sees the real art of secret achievements.
        self.assertIn("/static/achievements/secret_identity.svg", html)
        self.assertNotIn("_secret.svg", html)

    def test_list_queries_do_not_grow_with_the_medals(self) -> None:
        url = reverse("manage_achievements")

        def queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                self.client.get(url)
            return len(captured)

        before = queries()
        upload_icon(achievement("origin"))
        make_achievement(code="ghost", name_en="Ghost", name_es="Fantasma")
        self.assertEqual(queries(), before)

    def test_edit_page_previews_every_level_and_says_where_the_picture_comes_from(self) -> None:
        html = self.edit_page(achievement("great_power"))
        for ring in ["base", "silver", "gold"]:
            self.assertIn(f"medal medal--{ring} medal--lg", html)
        self.assertIn('data-icon-source="bundled"', html)

    def test_edit_page_shows_the_uploaded_image(self) -> None:
        origin = upload_icon(achievement("origin"))
        html = self.edit_page(origin)
        self.assertIn(f'<img src="{origin.icon_image.url}"', html)
        self.assertNotIn("/static/achievements/origin.svg", html)
        self.assertIn('data-icon-source="upload"', html)

    def test_edit_page_falls_back_to_the_icon(self) -> None:
        ghost = make_achievement(code="ghost", icon="bolt", name_en="Ghost", name_es="Fantasma")
        html = self.edit_page(ghost)
        self.assertIn('<span class="medal__symbol material-symbols-outlined">bolt</span>', html)
        self.assertIn('data-icon-source="symbol"', html)
