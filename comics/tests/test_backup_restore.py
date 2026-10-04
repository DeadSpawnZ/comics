"""Backups (dump.py / dumpdata) still work with the AreaAccess model, and a restore keeps the roles:
group permissions travel by natural key, so they survive permission ids that differ between
databases. `ensure_roles` rebuilds the groups when a dump does not bring them."""

import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings

import dump
from comics.models import Collection
from comics.roles import ACCESS_MANAGEMENT, ADMINISTRATORS, COLLECTORS, ROLE_PERMISSIONS, ensure_roles

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import make_collection, make_edition, make_user


def group_permissions(name: str) -> set[str]:
    group = Group.objects.get(name=name)
    return {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.select_related("content_type")}


class DumpTests(ComiTestCase):
    def test_backup_dump_succeeds(self) -> None:
        make_collection(collector=make_user(role=ADMINISTRATORS), edition=make_edition())
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dump.json"
            call_command("dumpdata", *dump.dumpdata_args(str(output)), stdout=StringIO())
            self.assertIn('"model": "auth.group"', output.read_text(encoding="utf-8"))

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
        call_command("dumpdata", *dump.dumpdata_args(str(output)), stdout=StringIO())
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
