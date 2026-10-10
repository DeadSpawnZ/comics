"""Roles and permissions: who may open Gestión, the Django admin and Mis comics, the role groups
created by the data migration, the Collectors default role and the menu links per role."""

import datetime
import importlib
from io import StringIO
from types import SimpleNamespace
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.core import serializers
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from comics.models import Collection, Edition
from comics.roles import (
    ACCESS_MANAGEMENT,
    ADMINISTRATORS,
    COLLECTORS,
    REVIEWERS,
    ROLE_PERMISSIONS,
    assign_default_role,
    can_access_management,
    ensure_roles,
)

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_superuser, make_user

roles_migration = importlib.import_module("comics.migrations.0093_roles")
change_request_roles_migration = importlib.import_module("comics.migrations.0110_change_request_permissions")

ROLES = ["anonymous", "collector", "reviewer", "administrator", "superuser"]


class RoleUsersMixin:
    """One user per role (anonymous = no login)."""

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.users = {
            "anonymous": None,
            "collector": make_user(),
            "reviewer": make_user(role=REVIEWERS),
            "administrator": make_user(role=ADMINISTRATORS),
            "superuser": make_superuser(),
        }

    def login_as(self, role: str) -> None:
        self.client.logout()
        if self.users[role] is not None:
            self.client.force_login(self.users[role])


class GestionAccessTests(RoleUsersMixin, ComiTestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.edition = make_edition()
        cls.piece = make_collection(collector=cls.users["collector"], edition=cls.edition)

    def gestion_pages(self) -> list[str]:
        publishing = self.edition.publishing_id
        return [
            reverse("manage_home"),
            reverse("manage_editions"),
            reverse("manage_edition_new"),
            reverse("manage_edition_edit", args=[self.edition.pk]),
            reverse("manage_collections"),
            reverse("manage_collection_new"),
            reverse("manage_collection_edit", args=[self.piece.pk]),
            reverse("manage_publishings"),
            reverse("manage_publishing_new"),
            reverse("manage_publishing_edit", args=[publishing]),
            reverse("manage_arcs"),
            reverse("manage_arc_new"),
            reverse("manage_connectings"),
            reverse("manage_connecting_new"),
            # Gestión-only JSON APIs.
            f"{reverse('manage_publishing_issues')}?publishing={publishing}",
            f"{reverse('manage_publishing_comics')}?publishing={publishing}",
        ]

    def test_gestion_pages_per_role(self) -> None:
        expected = {"collector": 403, "reviewer": 403, "administrator": 200, "superuser": 200}
        for role in ROLES:
            self.login_as(role)
            for url in self.gestion_pages():
                with self.subTest(role=role, url=url):
                    response = self.client.get(url)
                    if role == "anonymous":
                        login = f"{reverse('login')}?{urlencode({'next': url})}"
                        self.assertRedirects(response, login, fetch_redirect_response=False)
                    else:
                        self.assertEqual(response.status_code, expected[role])

    def test_gestion_writes_are_refused_without_the_permission(self) -> None:
        for role in ["anonymous", "collector", "reviewer"]:
            self.login_as(role)
            with self.subTest(role=role):
                response = self.client.post(reverse("manage_collection_delete", args=[self.piece.pk]))
                self.assertEqual(response.status_code, 302 if role == "anonymous" else 403)
                response = self.client.post(reverse("manage_edition_delete", args=[self.edition.pk]))
                self.assertEqual(response.status_code, 302 if role == "anonymous" else 403)
        self.assertTrue(Collection.objects.filter(pk=self.piece.pk).exists())
        self.assertTrue(Edition.objects.filter(pk=self.edition.pk).exists())

    def test_administrator_can_write(self) -> None:
        self.login_as("administrator")
        response = self.client.post(reverse("manage_collection_delete", args=[self.piece.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Collection.objects.filter(pk=self.piece.pk).exists())

    def test_permission_check_per_role(self) -> None:
        for role, user in self.users.items():
            with self.subTest(role=role):
                user = user or AnonymousUser()
                self.assertEqual(can_access_management(user), role in {"administrator", "superuser"})

    def test_inactive_administrator_has_no_access(self) -> None:
        user = make_user(role=ADMINISTRATORS, is_active=False)
        self.assertFalse(can_access_management(user))


class DjangoAdminAccessTests(RoleUsersMixin, ComiTestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        # is_staff no longer opens the admin on its own.
        cls.users["staff"] = make_user(is_staff=True, role=ADMINISTRATORS)
        cls.edition = make_edition()

    def assert_sent_to_admin_login(self, url: str) -> None:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")), response["Location"])

    def test_admin_pages_are_for_superusers_only(self) -> None:
        urls = [
            reverse("admin:index"),
            reverse("admin:comics_edition_changelist"),
            reverse("admin:auth_user_changelist"),
        ]
        for role in [*ROLES, "staff"]:
            self.login_as(role)
            for url in urls:
                with self.subTest(role=role, url=url):
                    if role == "superuser":
                        self.assertEqual(self.client.get(url).status_code, 200)
                    else:
                        self.assert_sent_to_admin_login(url)

    def test_admin_ajax_endpoints_follow_the_admin_rule(self) -> None:
        publishing = self.edition.publishing_id
        urls = [
            reverse("get_publishing_date", args=[publishing]),
            reverse("get_comics_by_publishing", args=[publishing]),
            reverse("get_previous_trades", args=[self.edition.pk]),
        ]
        for role in [*ROLES, "staff"]:
            self.login_as(role)
            for url in urls:
                with self.subTest(role=role, url=url):
                    if role == "superuser":
                        self.assertEqual(self.client.get(url).status_code, 200)
                    else:
                        self.assert_sent_to_admin_login(url)

    def test_admin_login_is_the_app_login(self) -> None:
        """The admin has no login form of its own: anonymous visitors go to the app login (and come
        back), signed-in users without access get 403 (no redirect loop), superusers go through."""
        changelist = reverse("admin:comics_edition_changelist")
        admin_login = f"{reverse('admin:login')}?{urlencode({'next': changelist})}"

        response = self.client.get(changelist)
        self.assertRedirects(response, admin_login, fetch_redirect_response=False)
        response = self.client.get(admin_login)
        app_login = f"{reverse('login')}?{urlencode({'next': changelist})}"
        self.assertRedirects(response, app_login, fetch_redirect_response=False)
        # POSTing credentials to the admin login does not sign anyone in either.
        make_superuser(username="root", password="pass-1234")
        self.client.post(admin_login, {"username": "root", "password": "pass-1234"})
        self.assertNotIn("_auth_user_id", self.client.session)

        for role in ["collector", "administrator", "staff"]:
            self.login_as(role)
            with self.subTest(role=role):
                self.assertEqual(self.client.get(admin_login).status_code, 403)

        self.login_as("superuser")
        self.assertRedirects(self.client.get(admin_login), changelist, fetch_redirect_response=False)
        response = self.client.get(reverse("admin:login"), {"next": "https://evil.example.com/"})
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)

    def test_admin_login_as_next_does_not_loop(self) -> None:
        admin_login = reverse("admin:login")
        looping = f"{admin_login}?{urlencode({'next': admin_login})}"
        self.login_as("superuser")
        self.assertRedirects(self.client.get(looping), reverse("admin:index"), fetch_redirect_response=False)
        self.login_as("anonymous")
        app_login = f"{reverse('login')}?{urlencode({'next': reverse('admin:index')})}"
        self.assertRedirects(self.client.get(looping), app_login, fetch_redirect_response=False)

    def test_app_login_brings_a_superuser_back_to_the_admin(self) -> None:
        make_superuser(username="root", password="pass-1234")
        changelist = reverse("admin:comics_edition_changelist")
        response = self.client.post(reverse("login"), {"username": "root", "password": "pass-1234", "next": changelist})
        self.assertRedirects(response, changelist, fetch_redirect_response=False)
        self.assertEqual(self.client.get(changelist).status_code, 200)


class MyComicsAccessTests(RoleUsersMixin, ComiTestCase):
    def test_my_comics_pages_are_open_to_every_signed_in_user(self) -> None:
        edition = make_edition()
        urls = [
            reverse("comics"),
            reverse("comics_piece_new"),
            reverse("comics_editions"),
            reverse("comics_edition_detail", args=[edition.pk]),
            reverse("comics_arcs"),
            reverse("comics_connectings"),
            f"{reverse('comics_api_editions')}?publishing={edition.publishing_id}",
            f"{reverse('comics_api_purchases')}?edition={edition.pk}",
            reverse("collectables"),
        ]
        for role in ROLES:
            self.login_as(role)
            for url in urls:
                with self.subTest(role=role, url=url):
                    response = self.client.get(url)
                    if role == "anonymous":
                        login = f"{reverse('login')}?{urlencode({'next': url})}"
                        self.assertRedirects(response, login, fetch_redirect_response=False)
                    else:
                        self.assertEqual(response.status_code, 200)

    def test_only_gestion_users_may_list_another_collectors_purchases(self) -> None:
        edition = make_edition()
        owner = make_user()
        make_collection(collector=owner, edition=edition, trade_date=datetime.date(2020, 1, 1))
        params = {"edition": edition.pk, "collector": owner.pk}
        expected = {"collector": 0, "reviewer": 0, "administrator": 1, "superuser": 1}
        for role, count in expected.items():
            self.login_as(role)
            with self.subTest(role=role):
                response = self.client.get(reverse("comics_api_purchases"), params)
                self.assertEqual(len(response.json()["results"]), count)


class MenuLinksTests(RoleUsersMixin, ComiTestCase):
    def test_gestion_and_admin_links_per_role(self) -> None:
        gestion = f'href="{reverse("manage_home")}"'
        admin = f'href="{reverse("admin:index")}"'
        expected = {
            "anonymous": (False, False),
            "collector": (False, False),
            "reviewer": (False, False),
            "administrator": (True, False),
            "superuser": (True, True),
        }
        for role, (has_gestion, has_admin) in expected.items():
            self.login_as(role)
            # Anonymous users can only see the login page; it uses the same base template.
            url = reverse("login") if role == "anonymous" else reverse("comics")
            with self.subTest(role=role):
                html = self.client.get(url).content.decode()
                self.assertEqual(gestion in html, has_gestion)
                self.assertEqual(admin in html, has_admin)

    def test_edit_in_gestion_link_on_edition_detail(self) -> None:
        edition = make_edition()
        edit_url = reverse("manage_edition_edit", args=[edition.pk])
        for role in ["collector", "reviewer", "administrator", "superuser"]:
            self.login_as(role)
            with self.subTest(role=role):
                html = self.client.get(reverse("comics_edition_detail", args=[edition.pk])).content.decode()
                self.assertEqual(edit_url in html, role in {"administrator", "superuser"})


class HistoryUserFilterTests(ComiTestCase):
    def test_user_filter_lists_the_authors_of_history_entries(self) -> None:
        author = make_user(username="author")
        make_user(username="silent")
        edition = make_edition()
        LogEntry.objects.log_actions(
            user_id=author.pk, queryset=[edition], action_flag=ADDITION, change_message="", single_object=True
        )
        self.client.force_login(make_user(role=ADMINISTRATORS, username="manager"))
        response = self.client.get(reverse("manage_home"))
        self.assertEqual([user.username for user in response.context["users"]], ["author"])


class DefaultRoleTests(ComiTestCase):
    def test_every_new_user_is_a_collector(self) -> None:
        users = [
            User.objects.create_user(username="reader"),
            User.objects.create_superuser(username="root", password="pass-1234"),
            User.objects.create(username="plain"),
        ]
        for user in users:
            with self.subTest(user=user.username):
                self.assertEqual(list(user.groups.values_list("name", flat=True)), [COLLECTORS])

    def test_saving_an_existing_user_does_not_change_its_groups(self) -> None:
        user = make_user()
        user.groups.clear()
        user.first_name = "Al"
        user.save()
        self.assertFalse(user.groups.exists())

    def test_raw_saves_from_loaddata_do_not_add_groups(self) -> None:
        fixture = '[{"model": "auth.user", "fields": {"username": "restored", "password": "", "groups": []}}]'
        for restored in serializers.deserialize("json", fixture):
            restored.save()
        self.assertFalse(User.objects.get(username="restored").groups.exists())

    def test_assign_default_role_is_idempotent_and_creates_the_group_if_missing(self) -> None:
        Group.objects.filter(name=COLLECTORS).delete()
        user = make_user()  # the signal recreates the group
        assign_default_role(user)
        self.assertEqual(Group.objects.filter(name=COLLECTORS).count(), 1)
        self.assertEqual(user.groups.count(), 1)


class EnsureRolesTests(ComiTestCase):
    def test_command_rebuilds_missing_groups_and_is_idempotent(self) -> None:
        superuser = make_superuser()
        reader = make_user()
        extra = make_user(role=REVIEWERS)
        Group.objects.filter(name__in=[COLLECTORS, ADMINISTRATORS]).delete()

        call_command("ensure_roles", stdout=StringIO())
        call_command("ensure_roles", stdout=StringIO())

        self.assertEqual(Group.objects.filter(name__in=ROLE_PERMISSIONS).count(), 3)
        self.assertEqual(set(superuser.groups.values_list("name", flat=True)), {COLLECTORS, ADMINISTRATORS})
        self.assertEqual(set(reader.groups.values_list("name", flat=True)), {COLLECTORS})
        # Existing memberships are kept.
        self.assertEqual(set(extra.groups.values_list("name", flat=True)), {COLLECTORS, REVIEWERS})
        self.assertTrue(User.objects.get(pk=superuser.pk).has_perm(ACCESS_MANAGEMENT))
        self.assertEqual(
            {p.codename for p in Group.objects.get(name=ADMINISTRATORS).permissions.all()},
            {"access_management", "validate_changerequest", "approve_changerequest"},
        )

    def test_ensure_roles_matches_the_migration(self) -> None:
        """Same result as the forward steps of 0093 and 0110 on the same users."""
        make_superuser()
        make_user()
        Group.objects.all().delete()
        ensure_roles()
        by_command = {g.name: (set(g.user_set.all()), set(g.permissions.all())) for g in Group.objects.all()}
        Group.objects.all().delete()
        roles_migration.create_roles(apps, SimpleNamespace(connection=connection))
        change_request_roles_migration.grant(apps, SimpleNamespace(connection=connection))
        by_migration = {g.name: (set(g.user_set.all()), set(g.permissions.all())) for g in Group.objects.all()}
        self.assertEqual(by_command, by_migration)


class RolesMigrationTests(ComiTestCase):
    """The test database was built by running the migrations, so the groups below come from 0093."""

    def test_groups_exist_with_the_permissions_of_their_role(self) -> None:
        for name, permissions in ROLE_PERMISSIONS.items():
            with self.subTest(group=name):
                group = Group.objects.get(name=name)
                granted = {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()}
                self.assertEqual(granted, set(permissions))

    def test_access_management_permission_exists(self) -> None:
        app_label, codename = ACCESS_MANAGEMENT.split(".")
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        self.assertEqual(permission.content_type.model, "areaaccess")

    def test_frozen_role_table_matches_the_runtime_one(self) -> None:
        frozen = {
            name: {f"{app_label}.{codename}" for app_label, _model, codename, _label in permissions}
            for name, permissions in roles_migration.ROLE_PERMISSIONS.items()
        }
        for name, permissions in change_request_roles_migration.ROLE_PERMISSIONS.items():
            frozen[name] |= {f"{app_label}.{codename}" for app_label, _model, codename, _label in permissions}
        self.assertEqual(frozen, {name: set(perms) for name, perms in ROLE_PERMISSIONS.items()})

    def run_migration(self, function_name: str) -> None:
        getattr(roles_migration, function_name)(apps, SimpleNamespace(connection=connection))

    def test_forward_places_existing_users_and_is_idempotent(self) -> None:
        collector = make_user()
        superuser = make_superuser()
        # Users that predate the roles: no groups at all.
        Group.objects.all().delete()

        self.run_migration("create_roles")
        self.run_migration("create_roles")  # running again changes nothing

        self.assertEqual(Group.objects.filter(name__in=ROLE_PERMISSIONS).count(), 3)
        self.assertEqual(set(collector.groups.values_list("name", flat=True)), {COLLECTORS})
        self.assertEqual(set(superuser.groups.values_list("name", flat=True)), {COLLECTORS, ADMINISTRATORS})
        self.assertFalse(User.objects.get(pk=collector.pk).has_perm(ACCESS_MANAGEMENT))
        self.assertEqual(Permission.objects.filter(codename="access_management").count(), 1)

    def test_reverse_deletes_the_groups_only(self) -> None:
        user = make_user(role=ADMINISTRATORS)
        Group.objects.create(name="Other")

        self.run_migration("delete_roles")

        self.assertEqual(list(Group.objects.values_list("name", flat=True)), ["Other"])
        self.assertFalse(user.groups.exists())
        self.assertTrue(Permission.objects.filter(codename="access_management").exists())
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
