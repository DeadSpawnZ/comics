"""Backups (dump.py / dumpdata) still work with the AreaAccess model, and a restore keeps the roles:
group permissions travel by natural key, so they survive permission ids that differ between
databases. `ensure_roles` rebuilds the groups when a dump does not bring them. Profiles travel by
their user's username too, achievements by their code and achievement categories by theirs.
Notifications point to their user and achievement by natural key and to announcements by id
(announcements are content, like pieces: nothing seeds them). The versions of issues and editions
(comics.history) travel with them, their authors by username, so the history survives a restore."""

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.utils import timezone
from django.test import TransactionTestCase, override_settings

import dump
from comics.achievements.catalogue import ACHIEVEMENTS, ensure_catalogue
from comics.history import versions_of
from comics.models import (
    Achievement,
    AchievementCategory,
    AchievementTier,
    Announcement,
    Artist,
    Collection,
    Edition,
    Issue,
    Notification,
    Profile,
    UserAchievement,
)
from comics.versions import revert_to_version
from comics.roles import ACCESS_MANAGEMENT, ADMINISTRATORS, COLLECTORS, REVIEWERS, ROLE_PERMISSIONS, ensure_roles

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import make_achievement, make_category, make_collection, make_edition, make_user


def group_permissions(name: str) -> set[str]:
    group = Group.objects.get(name=name)
    return {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.select_related("content_type")}


class DumpTests(ComiTestCase):
    def test_backup_dump_succeeds(self) -> None:
        make_collection(collector=make_user(role=ADMINISTRATORS), edition=make_edition())
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dump.json"
            dump.write_dump(str(output))
            content = output.read_text(encoding="utf-8")
            self.assertIn('"model": "auth.group"', content)
            # Non-ASCII texts (seeded achievement names) survive as UTF-8.
            self.assertIn("Un gran poder…", content)

    def test_bare_dumpdata_of_the_app_succeeds(self) -> None:
        out = StringIO()
        call_command("dumpdata", "comics", stdout=out)
        self.assertTrue(out.getvalue().startswith("["))


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class RestoreTests(TransactionTestCase):
    """Flushes the database, so it runs outside a transaction; serialized_rollback puts back the
    migration-created rows (groups, permissions) for the tests that run after it."""

    serialized_rollback = True

    def make_dump(self, tmp: str) -> Path:
        output = Path(tmp) / "dump.json"
        dump.write_dump(str(output))
        return output

    def shift_permission_ids(self) -> None:
        """Give `comics.access_management` an id it did not have when the dump was taken, as on a
        database where the migrations created the permissions in another order."""
        app_label, codename = ACCESS_MANAGEMENT.split(".")
        old = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        content_type, name, old_pk = old.content_type, old.name, old.pk
        old.delete()
        # Another permission first, in case the database reuses the freed id.
        Permission.objects.create(content_type=ContentType.objects.get_for_model(Group), codename="filler", name="x")
        moved = Permission.objects.create(content_type=content_type, codename=codename, name=name)
        self.assertNotEqual(moved.pk, old_pk)

    def test_restore_keeps_the_permissions_of_the_roles(self) -> None:
        manager = make_user(username="manager", role=ADMINISTRATORS)
        make_collection(collector=manager, edition=make_edition())
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)

            call_command("flush", interactive=False, verbosity=0)
            self.shift_permission_ids()
            # A migrated database already has the role groups (0093), here with other ids than in the
            # dump: loading groups by id would collide on their unique name.
            placeholder = Group.objects.create(name="placeholder")
            ensure_roles()
            placeholder.delete()
            call_command("loaddata", str(dump_file), verbosity=0)

        self.assertEqual(Group.objects.filter(name__in=ROLE_PERMISSIONS).count(), 3)
        self.assertEqual(group_permissions(ADMINISTRATORS), {ACCESS_MANAGEMENT})
        manager = User.objects.get(username="manager")
        self.assertTrue(manager.has_perm(ACCESS_MANAGEMENT))
        self.assertEqual(set(manager.groups.values_list("name", flat=True)), {COLLECTORS, ADMINISTRATORS})
        self.assertEqual(Collection.objects.get().collector, manager)

    def test_restore_keeps_achievements_by_code(self) -> None:
        hero = make_user(username="hero")
        great_power = Achievement.objects.get(code="great_power")
        great_power.name_es = "Gran poder"  # edited in Gestión
        great_power.save()
        AchievementTier.objects.filter(achievement=great_power, level=1).update(threshold=10)
        UserAchievement.objects.award(hero, great_power, 2)
        UserAchievement.objects.award(hero, Achievement.objects.get(code="origin"), 1)
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            content = json.loads(dump_file.read_text(encoding="utf-8"))
            awards = [item for item in content if item["model"] == "comics.userachievement"]
            self.assertIn(
                ["hero", "great_power"], [[*item["fields"]["user"], *item["fields"]["achievement"]] for item in awards]
            )
            self.assertTrue(all("pk" not in item for item in content if item["model"].startswith("comics.achievement")))

            call_command("flush", interactive=False, verbosity=0)
            # A migrated target already has the seeded achievements, here under other ids.
            make_achievement(code="placeholder", name_en="x", name_es="x")
            ensure_catalogue()
            Achievement.objects.filter(code="placeholder").delete()
            call_command("loaddata", str(dump_file), verbosity=0)

        self.assertEqual(Achievement.objects.filter(code="great_power").count(), 1)
        great_power = Achievement.objects.get(code="great_power")
        self.assertEqual(great_power.name_es, "Gran poder")
        self.assertEqual(list(great_power.tiers.values_list("level", "threshold")), [(1, 10), (2, 500), (3, 1000)])
        hero = User.objects.get(username="hero")
        levels = dict(UserAchievement.objects.filter(user=hero).values_list("achievement__code", "level"))
        # novato came from the account's creation, committed right away (no TestCase transaction).
        self.assertEqual(levels, {"great_power": 2, "origin": 1, "novato": 1})

    def test_restore_keeps_categories_and_gestion_achievements_by_code(self) -> None:
        hero = make_user(username="hero")
        AchievementCategory.objects.filter(code="sagas").update(name_es="Grandes sagas")  # edited in Gestión
        events = make_category(code="events", name_en="Events", name_es="Eventos", display_order=60)
        christmas = make_achievement(
            code="christmas",
            rule="login_in_window",
            category=events,
            calendar_window="yearly",
            window_start_day=24,
            window_start_month=12,
            window_end_day=6,
            window_end_month=1,
        )
        UserAchievement.objects.award(hero, christmas, 1)
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            content = json.loads(dump_file.read_text(encoding="utf-8"))
            categories = [item for item in content if item["model"] == "comics.achievementcategory"]
            self.assertTrue(categories and all("pk" not in item for item in categories))
            entry = next(
                item
                for item in content
                if item["model"] == "comics.achievement" and item["fields"]["code"] == "christmas"
            )
            self.assertEqual(entry["fields"]["category"], ["events"])
            # Categories come before the achievements that point at them.
            models = [item["model"] for item in content]
            self.assertLess(models.index("comics.achievementcategory"), models.index("comics.achievement"))

            call_command("flush", interactive=False, verbosity=0)
            # A migrated target already has the seeded categories and achievements, under other ids.
            make_category(code="placeholder")
            ensure_catalogue()
            AchievementCategory.objects.filter(code="placeholder").delete()
            call_command("loaddata", str(dump_file), verbosity=0)

        self.assertEqual(AchievementCategory.objects.get(code="sagas").name_es, "Grandes sagas")
        restored = Achievement.objects.get(code="christmas")
        self.assertEqual(
            (restored.category.code, restored.rule, restored.window_end_month), ("events", "login_in_window", 1)
        )
        self.assertTrue(UserAchievement.objects.filter(user__username="hero", achievement=restored).exists())
        self.assertEqual(AchievementCategory.objects.filter(code="collection").count(), 1)

    def test_older_dump_after_flush_needs_ensure_achievements(self) -> None:
        make_collection(collector=make_user(username="hero"), edition=make_edition())
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            # Notifications came after achievements (and point to them): not in such a dump either.
            older = [
                item
                for item in json.loads(dump_file.read_text(encoding="utf-8"))
                if "achievement" not in item["model"] and item["model"] != "comics.notification"
            ]
            dump_file.write_text(json.dumps(older), encoding="utf-8")
            call_command("flush", interactive=False, verbosity=0)
            call_command("loaddata", str(dump_file), verbosity=0)
        self.assertFalse(Achievement.objects.exists())

        call_command("ensure_achievements", stdout=StringIO())
        call_command("ensure_achievements", stdout=StringIO())
        call_command("award_achievements", stdout=StringIO(), stderr=StringIO())

        self.assertEqual(
            set(Achievement.objects.values_list("code", flat=True)), {item["code"] for item in ACHIEVEMENTS}
        )
        self.assertEqual(AchievementTier.objects.filter(achievement__code="great_power").count(), 3)
        self.assertTrue(UserAchievement.objects.filter(user__username="hero", achievement__code="origin").exists())

    def test_restore_keeps_notifications_and_announcements(self) -> None:
        hero = make_user(username="hero")
        reviewer = make_user(username="reviewer", role=REVIEWERS)
        manager = make_user(username="manager", role=ADMINISTRATORS)
        great_power = Achievement.objects.get(code="great_power")
        UserAchievement.objects.award(hero, great_power, 2)
        Notification.objects.achievement_unlocked(hero, great_power, 2)
        news = Announcement.objects.create(
            title_en="News", title_es="Noticias", audience=Announcement.Audience.ROLES, created_by=manager
        )
        news.roles.set([Group.objects.get(name=REVIEWERS)])
        self.assertEqual(news.publish(), 1)
        read_at = timezone.now().replace(microsecond=0)
        Notification.objects.filter(recipient=reviewer).update(read_at=read_at)
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            content = json.loads(dump_file.read_text(encoding="utf-8"))
            entry = next(
                item
                for item in content
                if item["model"] == "comics.notification" and item["fields"]["achievement"] == ["great_power"]
            )
            self.assertEqual(entry["fields"]["recipient"], ["hero"])
            entry = next(item for item in content if item["model"] == "comics.announcement")
            self.assertEqual((entry["fields"]["roles"], entry["fields"]["created_by"]), ([["Reviewers"]], ["manager"]))

            call_command("flush", interactive=False, verbosity=0)
            # A migrated target already has the role groups and the achievements, under other ids.
            placeholder = Group.objects.create(name="placeholder")
            ensure_roles()
            placeholder.delete()
            make_achievement(code="placeholder", name_en="x", name_es="x")
            ensure_catalogue()
            Achievement.objects.filter(code="placeholder").delete()
            call_command("loaddata", str(dump_file), verbosity=0)

        unlocked = Notification.objects.get(recipient__username="hero", achievement__code="great_power")
        self.assertEqual((unlocked.kind, unlocked.level, unlocked.read_at), (Notification.Kind.ACHIEVEMENT, 2, None))
        news = Announcement.objects.get()
        self.assertEqual(list(news.roles.values_list("name", flat=True)), [REVIEWERS])
        self.assertEqual(news.created_by.username, "manager")
        sent = Notification.objects.get(announcement=news)
        self.assertEqual((sent.recipient.username, sent.read_at), ("reviewer", read_at))
        self.assertEqual(sent.text, "News")

    def test_restore_keeps_profiles_by_username(self) -> None:
        manager = make_user(username="manager")
        # The file itself is not part of the dump (media is backed up separately): only its path.
        Profile.objects.filter(user=manager).update(avatar="avatars/user1_0123456789abcdef.jpg", phone="811 234 5678")
        Profile.objects.filter(user=manager).update(is_public=False)
        make_user(username="plain")
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            entries = [
                item for item in json.loads(dump_file.read_text(encoding="utf-8")) if item["model"] == "comics.profile"
            ]
            # Written by username, without the profile's own id.
            self.assertEqual(sorted(item["fields"]["user"] for item in entries), [["manager"], ["plain"]])
            self.assertTrue(all("pk" not in item for item in entries))

            call_command("flush", interactive=False, verbosity=0)
            # The target already has a "manager" (with a fresh profile, under other ids): the dump
            # updates both instead of colliding on them.
            make_user(username="other")
            make_user(username="manager")
            call_command("loaddata", str(dump_file), verbosity=0)

        self.assertEqual(Profile.objects.filter(user__username="manager").count(), 1)
        profile = Profile.objects.get(user__username="manager")
        self.assertEqual(profile.avatar.name, "avatars/user1_0123456789abcdef.jpg")
        self.assertEqual((profile.phone, profile.is_public), ("811 234 5678", False))
        self.assertTrue(Profile.objects.filter(user__username="plain").exists())

    def test_restore_keeps_the_versions(self) -> None:
        manager = make_user(username="manager")
        edition = make_edition(notes="first")
        edition.cover_artists.add(Artist.objects.create(name="Todd McFarlane"))
        edition._history_user = manager
        edition._change_reason = "Typo"
        edition.notes = "second"
        edition.save()
        history = Edition.history.model
        dumped = [
            (version.history_type, version.notes, getattr(version.history_user, "username", None))
            for version in versions_of(Edition, edition.pk)
        ]
        issue_versions = versions_of(Issue, edition.issue_id).count()
        with tempfile.TemporaryDirectory() as tmp:
            dump_file = self.make_dump(tmp)
            content = json.loads(dump_file.read_text(encoding="utf-8"))
            entry = next(
                item
                for item in content
                if item["model"] == "comics.historicaledition" and item["fields"]["history_change_reason"] == "Typo"
            )
            self.assertEqual(entry["fields"]["history_user"], ["manager"])

            call_command("flush", interactive=False, verbosity=0)
            make_user(username="other")  # the restored users get other ids
            call_command("loaddata", str(dump_file), verbosity=0)

        # Loading the rows (and setting their relations) wrote no versions of its own.
        restored = [
            (version.history_type, version.notes, getattr(version.history_user, "username", None))
            for version in versions_of(Edition, edition.pk)
        ]
        self.assertEqual(restored, dumped)
        self.assertEqual(versions_of(Issue, edition.issue_id).count(), issue_versions)
        latest = versions_of(Edition, edition.pk).first()
        self.assertEqual((latest.history_user.username, latest.history_change_reason), ("manager", "Typo"))
        self.assertEqual(list(latest.cover_artists.values_list("artist__name", flat=True)), ["Todd McFarlane"])
        self.assertEqual(history.cover_artists.model.objects.count(), 2)  # one row per version since the artist

        revert_to_version(versions_of(Edition, edition.pk).last(), user=None)
        edition = Edition.objects.get(pk=edition.pk)
        self.assertEqual((edition.notes, edition.cover_artists.count()), ("first", 0))

    def test_ensure_roles_rebuilds_the_groups_after_flush(self) -> None:
        call_command("flush", interactive=False, verbosity=0)
        superuser = User.objects.create_superuser(username="root", password="pass-1234")
        superuser.groups.clear()  # like a user restored from a dump taken before the roles
        reader = User.objects.create_user(username="reader")
        reader.groups.clear()

        call_command("ensure_roles", stdout=StringIO())
        call_command("ensure_roles", stdout=StringIO())

        for name, permissions in ROLE_PERMISSIONS.items():
            with self.subTest(group=name):
                self.assertEqual(group_permissions(name), set(permissions))
        self.assertEqual(Group.objects.count(), 3)
        self.assertEqual(set(superuser.groups.values_list("name", flat=True)), {COLLECTORS, ADMINISTRATORS})
        self.assertEqual(set(reader.groups.values_list("name", flat=True)), {COLLECTORS})
