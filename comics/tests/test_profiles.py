"""User profiles: automatic creation, the own profile page (personal info, picture, privacy,
password), the profile page other users see and the user menu."""

import importlib
import io
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.core import serializers
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.urls import reverse
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile

from comics.forms import AvatarField, PersonalInfoForm
from comics.models import Profile

from .base import ComiTestCase
from .factories import make_user

profiles_migration = importlib.import_module("comics.migrations.0095_create_profiles")

PROFILE_URL = "/perfil/"


def picture(name: str = "me.png", *, size: tuple[int, int] = (300, 200), fmt: str = "PNG") -> SimpleUploadedFile:
    """An in-memory picture upload (RGBA for PNG, so flattening is exercised)."""
    mode = "RGBA" if fmt == "PNG" else "RGB"
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 120) if mode == "RGBA" else (200, 30, 30)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


class ProfileCreationTests(ComiTestCase):
    def test_new_users_get_a_public_profile(self) -> None:
        for user in [make_user(), User.objects.create_superuser(username="root", password="pass-1234")]:
            with self.subTest(user=user.username):
                profile = Profile.objects.get(user=user)
                self.assertTrue(profile.is_public)
                self.assertEqual((profile.phone, profile.avatar.name), ("", ""))

    def test_users_restored_by_loaddata_get_no_profile_until_read(self) -> None:
        fixture = '[{"model": "auth.user", "fields": {"username": "restored", "password": ""}}]'
        for restored in serializers.deserialize("json", fixture):
            restored.save()
        user = User.objects.get(username="restored")
        self.assertFalse(Profile.objects.filter(user=user).exists())

        # Reads never fail: the pages create the missing profile.
        self.client.force_login(user)
        self.assertEqual(self.client.get(PROFILE_URL).status_code, 200)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(Profile.objects.for_user(user), Profile.objects.get(user=user))

    def test_public_page_of_a_user_without_profile(self) -> None:
        owner = make_user(username="ghost")
        Profile.objects.filter(user=owner).delete()
        self.client.force_login(make_user())
        self.assertContains(self.client.get(reverse("user_profile", args=["ghost"])), "ghost")

    def test_menu_renders_for_a_user_without_profile(self) -> None:
        user = make_user()
        Profile.objects.filter(user=user).delete()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("comics")).status_code, 200)

    def test_migration_creates_the_missing_profiles_only(self) -> None:
        first, second = make_user(), make_user()
        Profile.objects.filter(user=first).delete()
        kept = Profile.objects.get(user=second)
        kept.phone = "811 234 5678"
        kept.save()

        for _run in range(2):  # idempotent
            profiles_migration.create_missing_profiles(apps, SimpleNamespace(connection=connection))

        self.assertEqual(Profile.objects.filter(user__in=[first, second]).count(), 2)
        self.assertEqual(Profile.objects.get(user=second).phone, "811 234 5678")
        self.assertTrue(Profile.objects.get(user=first).is_public)


class OwnProfilePageTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="reader", email="reader@example.com", first_name="Ana")
        self.profile = Profile.objects.get(user=self.user)
        self.profile.phone = "+52 81 1234 5678"
        self.profile.save()
        self.client.force_login(self.user)

    def post(self, section: str, **data: object) -> object:
        return self.client.post(PROFILE_URL, {"section": section, **data})

    def info(self, **overrides: str) -> dict[str, str]:
        values = {"first_name": "Ana", "last_name": "López", "email": "reader@example.com", "phone": ""}
        values.update(overrides)
        return values

    def test_url_requires_login(self) -> None:
        self.assertEqual(reverse("profile"), PROFILE_URL)
        self.client.logout()
        response = self.client.get(PROFILE_URL)
        self.assertRedirects(response, f"{reverse('login')}?next={PROFILE_URL}", fetch_redirect_response=False)

    def test_owner_sees_member_since_email_phone_and_achievements_section(self) -> None:
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, "Member since")
        self.assertContains(response, "reader@example.com")
        self.assertContains(response, "+52 81 1234 5678")
        # Nothing earned yet: the owner sees the locked achievements (see test_achievements.py).
        self.assertEqual(response.context["achievements"].earned, 0)
        self.assertContains(response, "Achievements")

    def test_personal_info_is_saved(self) -> None:
        response = self.post("info", **self.info(email=" Reader@EXAMPLE.com ", phone="  +52 (81)   1234-5678 "))
        self.assertRedirects(response, PROFILE_URL, fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.last_name), ("Ana", "López"))
        self.assertEqual(self.user.email, "Reader@example.com")
        self.assertEqual(self.profile.phone, "+52 (81) 1234-5678")
        self.assertContains(self.client.get(PROFILE_URL), "Personal information saved.")

    def test_rejected_info_does_not_change_the_signed_in_user(self) -> None:
        response = self.post("info", **self.info(first_name="Unsaved", email=""))
        self.assertEqual(response.status_code, 200)
        # The header and the menu read the signed-in user: it keeps the saved values...
        self.assertEqual(response.wsgi_request.user.first_name, "Ana")
        self.assertEqual(response.context["user"].first_name, "Ana")
        # ...while the form shows what was typed, to be corrected.
        self.assertEqual(response.context["forms"]["info"]["first_name"].value(), "Unsaved")

    def test_info_form_without_commit_writes_nothing(self) -> None:
        form = PersonalInfoForm(
            data=self.info(first_name="Draft", phone="8112345678"), instance=self.user, profile=self.profile
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save(commit=False)
        self.assertEqual((user.first_name, self.profile.phone), ("Draft", "8112345678"))
        self.assertEqual(User.objects.get(pk=self.user.pk).first_name, "Ana")
        self.assertEqual(Profile.objects.get(pk=self.profile.pk).phone, "+52 81 1234 5678")

    def test_email_is_required_and_unique_ignoring_case_except_for_oneself(self) -> None:
        make_user(email="taken@example.com")
        for email in ["", "not-an-email", "TAKEN@example.com"]:
            with self.subTest(email=email):
                response = self.post("info", **self.info(email=email))
                self.assertEqual(response.status_code, 200)
                self.assertIn("email", response.context["forms"]["info"].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "reader@example.com")
        # Changing only the case of one's own email is fine.
        self.assertEqual(self.post("info", **self.info(email="READER@example.com")).status_code, 302)

    def test_phone_validation(self) -> None:
        for phone in ["abc", "123", "+52 81 1234 5678 9012 34", "81-1234-5678 ext 2", "++5281234567"]:
            with self.subTest(phone=phone):
                response = self.post("info", **self.info(phone=phone))
                self.assertEqual(response.status_code, 200)
                self.assertIn("phone", response.context["forms"]["info"].errors)
        for phone in ["8112345678", "+1 (555) 010-9999", "81.1234.5678", ""]:
            with self.subTest(phone=phone):
                self.assertEqual(self.post("info", **self.info(phone=phone)).status_code, 302)

    def test_a_failed_section_keeps_the_other_sections_unbound(self) -> None:
        response = self.post("info", **self.info(email=""))
        forms = response.context["forms"]
        self.assertTrue(forms["info"].is_bound)
        self.assertFalse(any(forms[name].is_bound for name in ["avatar", "privacy", "password"]))

    def test_unknown_section_changes_nothing(self) -> None:
        response = self.post("bogus", first_name="Changed")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Ana")

    def test_privacy_toggle(self) -> None:
        self.assertRedirects(self.post("privacy"), PROFILE_URL, fetch_redirect_response=False)  # unchecked
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_public)
        self.assertContains(self.client.get(PROFILE_URL), "Privacy settings saved.")
        self.post("privacy", is_public="on")
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_public)

    def test_password_change_keeps_this_session_and_ends_the_others(self) -> None:
        other_session = Client()
        other_session.force_login(self.user)
        response = self.post(
            "password",
            old_password="secret-pass",
            new_password1="a-Brand-new-pass-77",
            new_password2="a-Brand-new-pass-77",
        )
        self.assertRedirects(response, PROFILE_URL, fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-Brand-new-pass-77"))
        response = self.client.get(PROFILE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password changed.")
        self.assertEqual(other_session.get(PROFILE_URL).status_code, 302)

    def test_password_change_errors(self) -> None:
        cases = [
            ({"old_password": "wrong", "new_password1": "a-Brand-new-pass-77"}, "old_password"),
            ({"old_password": "secret-pass", "new_password1": "123"}, "new_password2"),
        ]
        for data, field in cases:
            with self.subTest(field=field):
                response = self.post("password", **data, new_password2=data["new_password1"])
                self.assertIn(field, response.context["forms"]["password"].errors)
        response = self.post(
            "password", old_password="secret-pass", new_password1="a-Brand-new-pass-77", new_password2="other-pass-77"
        )
        self.assertIn("new_password2", response.context["forms"]["password"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("secret-pass"))


class AvatarTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.profile = Profile.objects.get(user=self.user)
        self.client.force_login(self.user)

    def upload(self, upload: SimpleUploadedFile) -> object:
        return self.client.post(PROFILE_URL, {"section": "avatar", "avatar": upload})

    def stored_image(self) -> tuple[str, tuple[int, int], str]:
        self.profile.refresh_from_db()
        with default_storage.open(self.profile.avatar.name) as fh, Image.open(fh) as img:
            return img.format, img.size, img.mode

    def test_upload_is_cropped_square_resized_and_converted_to_jpeg(self) -> None:
        response = self.upload(picture(size=(900, 300)))
        self.assertRedirects(response, PROFILE_URL, fetch_redirect_response=False)
        self.assertEqual(self.stored_image(), ("JPEG", (Profile.AVATAR_SIZE, Profile.AVATAR_SIZE), "RGB"))
        self.assertRegex(self.profile.avatar.name, rf"^avatars/user{self.user.pk}_[0-9a-f]{{16}}\.jpg$")
        self.assertContains(self.client.get(PROFILE_URL), "Profile picture updated.")

    def test_big_picture_is_shrunk_right_after_opening(self) -> None:
        draft = JpegImageFile.draft
        thumbnail = Image.Image.thumbnail
        with (
            mock.patch.object(JpegImageFile, "draft", autospec=True, side_effect=draft) as draft_spy,
            mock.patch.object(Image.Image, "thumbnail", autospec=True, side_effect=thumbnail) as thumbnail_spy,
        ):
            response = self.upload(picture("big.jpg", size=(3000, 2000), fmt="JPEG"))
        self.assertRedirects(response, PROFILE_URL, fetch_redirect_response=False)
        size = Profile.AVATAR_SIZE
        self.assertIn(("RGB", (size * 2, size * 2)), [call.args[1:] for call in draft_spy.call_args_list])
        self.assertIn((size * 4, size * 4), [call.args[1] for call in thumbnail_spy.call_args_list])
        self.assertEqual(self.stored_image(), ("JPEG", (size, size), "RGB"))

    def test_saving_other_fields_leaves_the_stored_picture_alone(self) -> None:
        self.upload(picture())
        stale = Profile.objects.get(pk=self.profile.pk)  # read before the picture changes again
        self.upload(picture("newer.png"))
        self.profile.refresh_from_db()
        current = self.profile.avatar.name

        stale.phone = "8112345678"
        with self.captureOnCommitCallbacks(execute=True), mock.patch("comics.models.square_jpeg") as square:
            stale.save(update_fields=["phone"])

        square.assert_not_called()
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.avatar.name, self.profile.phone), (current, "8112345678"))
        self.assertTrue(default_storage.exists(current))

    def test_unchanged_avatar_is_not_reprocessed(self) -> None:
        self.upload(picture())
        self.profile.refresh_from_db()
        name = self.profile.avatar.name
        with mock.patch("comics.models.square_jpeg") as square:
            self.profile.is_public = False
            self.profile.save()
        square.assert_not_called()
        self.assertEqual(self.profile.avatar.name, name)

    def test_replacing_and_removing_delete_the_old_file(self) -> None:
        self.upload(picture())
        self.profile.refresh_from_db()
        first = self.profile.avatar.name
        with self.captureOnCommitCallbacks(execute=True):
            self.upload(picture("other.jpg", fmt="JPEG"))
        self.profile.refresh_from_db()
        second = self.profile.avatar.name
        self.assertNotEqual(first, second)
        self.assertFalse(default_storage.exists(first))
        self.assertTrue(default_storage.exists(second))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(PROFILE_URL, {"section": "remove_avatar"})
        self.assertRedirects(response, PROFILE_URL, fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar)
        self.assertFalse(default_storage.exists(second))
        self.assertContains(self.client.get(PROFILE_URL), "Profile picture removed.")

    def test_rejected_uploads(self) -> None:
        bmp = io.BytesIO()
        Image.new("RGB", (10, 10)).save(bmp, format="BMP")
        cases = {
            "not an image": SimpleUploadedFile("fake.png", b"this is not an image", content_type="image/png"),
            "other extension": SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain"),
            "unsupported format": SimpleUploadedFile("pic.bmp", bmp.getvalue(), content_type="image/bmp"),
        }
        for label, upload in cases.items():
            with self.subTest(label):
                response = self.upload(upload)
                self.assertEqual(response.status_code, 200)
                self.assertIn("avatar", response.context["forms"]["avatar"].errors)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar)

    def test_too_large_file_is_rejected_before_decoding(self) -> None:
        self.assertEqual(AvatarField.MAX_BYTES, 5 * 1024 * 1024)
        self.assertEqual(AvatarField.MAX_PIXELS, 25_000_000)
        with mock.patch.object(AvatarField, "MAX_BYTES", 100), mock.patch("PIL.Image.open") as pil_open:
            response = self.upload(picture())
        pil_open.assert_not_called()
        self.assertContains(response, "The picture is too large")

    def test_too_many_pixels_is_rejected(self) -> None:
        with mock.patch.object(AvatarField, "MAX_PIXELS", 100):
            response = self.upload(picture(size=(20, 20)))
        self.assertContains(response, "The picture has too many pixels.")

    def test_missing_file_is_an_error(self) -> None:
        response = self.client.post(PROFILE_URL, {"section": "avatar"})
        self.assertIn("avatar", response.context["forms"]["avatar"].errors)

    def test_picture_is_edited_from_a_dialog_opened_by_the_pencil(self) -> None:
        html = self.client.get(PROFILE_URL).content.decode()
        self.assertIn('data-bs-target="#avatarDialog"', html)
        self.assertIn('id="avatarDialog"', html)
        self.assertNotIn("data-open-on-load", html)
        self.assertNotIn('id="removeAvatarForm"', html)  # nothing to remove yet
        self.upload(picture())
        html = self.client.get(PROFILE_URL).content.decode()
        self.assertIn('id="removeAvatarForm"', html)
        self.assertIn('form="removeAvatarForm"', html)

    def test_a_rejected_upload_reopens_the_dialog(self) -> None:
        response = self.client.post(PROFILE_URL, {"section": "avatar"})
        self.assertContains(response, "data-open-on-load")

    def test_menu_shows_the_avatar_and_a_profile_link(self) -> None:
        html = self.client.get(reverse("comics")).content.decode()
        self.assertIn(f'href="{PROFILE_URL}"', html)
        self.assertNotIn('class="md-avatar md-avatar--sm"', html)
        self.upload(picture())
        self.profile.refresh_from_db()
        html = self.client.get(reverse("comics")).content.decode()
        self.assertIn(f'<img class="md-avatar md-avatar--sm" src="{self.profile.avatar.url}"', html)


class ProfilePrivacyTests(ComiTestCase):
    def setUp(self) -> None:
        self.owner = make_user(username="owner", first_name="Olga", last_name="Ruiz", email="olga@example.com")
        self.profile = Profile.objects.get(user=self.owner)
        self.profile.phone = "+52 81 5555 0000"
        self.profile.save()
        self.url = reverse("user_profile", args=["owner"])
        self.viewer = make_user(username="viewer")

    def page(self, viewer: User | None) -> str:
        self.client.logout()
        if viewer:
            self.client.force_login(viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def assert_no_contact_data(self, html: str) -> None:
        self.assertNotIn("olga@example.com", html)
        self.assertNotIn("5555", html)

    def test_url(self) -> None:
        self.assertEqual(self.url, "/u/owner/")

    def test_anonymous_visitors_must_log_in(self) -> None:
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)

    def test_public_profile_seen_by_another_user(self) -> None:
        html = self.page(self.viewer)
        self.assertIn("owner", html)
        self.assertIn("Olga Ruiz", html)
        self.assertIn("Member since", html)
        self.assertIn("Nothing earned in this category yet.", html)
        self.assertNotIn("Edit profile", html)
        self.assert_no_contact_data(html)

    def test_private_profile_seen_by_another_user_shows_the_username_only(self) -> None:
        self.profile.is_public = False
        self.profile.save()
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "profiles/private.html")
        html = response.content.decode()
        self.assertIn("owner", html)
        self.assertIn("This profile is private.", html)
        for hidden in ["Olga", "Ruiz", "Member since", "Achievements"]:
            self.assertNotIn(hidden, html)
        self.assert_no_contact_data(html)

    def test_owner_always_sees_their_profile(self) -> None:
        for is_public in [True, False]:
            with self.subTest(is_public=is_public):
                self.profile.is_public = is_public
                self.profile.save()
                html = self.page(self.owner)
                self.assertIn("Olga Ruiz", html)
                self.assertIn("Edit profile", html)
                self.assertEqual("Your profile is private" in html, not is_public)
                self.assert_no_contact_data(html)

    def test_own_page_shows_contact_data_to_the_owner_only(self) -> None:
        self.client.force_login(self.owner)
        html = self.client.get(PROFILE_URL).content.decode()
        self.assertIn("olga@example.com", html)
        self.assertIn("5555", html)
        # /perfil/ is always the signed-in user's own page.
        self.client.force_login(self.viewer)
        self.assert_no_contact_data(self.client.get(PROFILE_URL).content.decode())

    def test_unknown_and_inactive_users_are_not_found(self) -> None:
        make_user(username="sleeper", is_active=False)
        self.client.force_login(self.viewer)
        for username in ["nobody", "sleeper"]:
            with self.subTest(username=username):
                self.assertEqual(self.client.get(reverse("user_profile", args=[username])).status_code, 404)

    def test_profile_pages_are_get_only(self) -> None:
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.put(PROFILE_URL).status_code, 405)
