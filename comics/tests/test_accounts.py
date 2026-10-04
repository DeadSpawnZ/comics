"""Accounts: the login page, self-registration, POST-only logout and the styled error pages."""

from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from comics.roles import ADMINISTRATORS, COLLECTORS

from .base import ComiTestCase
from .factories import make_superuser, make_user

LANDING = "/collectables/"


class LoginPageTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user(username="reader", password="right-pass")
        self.url = reverse("login")

    def test_get_shows_the_login_page(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertContains(response, 'id="loginForm"')
        self.assertContains(response, f'href="{reverse("register")}"')
        self.assertNotContains(response, "didn't match")

    def test_login_page_is_the_only_sign_in_ui(self) -> None:
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('name="password"'), 1)
        self.assertNotIn("loginDialog", html)
        # No navbar "Log in" button on the login page itself.
        self.assertNotIn(f'href="{self.url}?next=', html)

    def test_navbar_links_anonymous_users_to_the_login_page_with_next(self) -> None:
        html = self.client.get("/no-such-page/").content.decode()
        self.assertIn(f'href="{self.url}?next=/no-such-page/"', html)

    def test_post_with_valid_credentials_signs_in(self) -> None:
        response = self.client.post(self.url, {"username": "reader", "password": "right-pass"})
        self.assertRedirects(response, LANDING, fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_wrong_password_unknown_user_and_inactive_user_get_the_same_error(self) -> None:
        make_user(username="sleeper", password="right-pass", is_active=False)
        attempts = [("reader", "wrong"), ("nobody", "right-pass"), ("sleeper", "right-pass")]
        for username, password in attempts:
            with self.subTest(username=username):
                response = self.client.post(self.url, {"username": username, "password": password})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Your username and password didn&#x27;t match.")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_empty_post_redisplays_the_form(self) -> None:
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_other_methods_are_not_allowed(self) -> None:
        self.assertEqual(self.client.put(self.url).status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_signed_in_user_is_sent_to_the_landing_page_or_next(self) -> None:
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), LANDING, fetch_redirect_response=False)
        response = self.client.get(self.url, {"next": "/comics/"})
        self.assertRedirects(response, "/comics/", fetch_redirect_response=False)

    def test_login_required_pages_send_their_url_as_next(self) -> None:
        response = self.client.get(reverse("comics"))
        self.assertRedirects(response, f"{self.url}?next=/comics/", fetch_redirect_response=False)

    def test_next_is_carried_in_the_form_and_the_registration_link(self) -> None:
        response = self.client.get(self.url, {"next": "/comics/arcos/"})
        self.assertContains(response, '<input type="hidden" name="next" value="/comics/arcos/">', html=True)
        self.assertContains(response, f'href="{reverse("register")}?next=/comics/arcos/"')

    def test_no_next_field_without_a_valid_next(self) -> None:
        for params in [{}, {"next": "https://evil.example.com/"}]:
            with self.subTest(params=params):
                html = self.client.get(self.url, params).content.decode()
                form = html[html.index('id="loginForm"') :]
                self.assertNotIn('name="next"', form[: form.index("</form>")])
                # The registration link does not carry it either.
                self.assertIn(f'href="{reverse("register")}"', html)

    def test_post_redirects_to_a_same_site_next(self) -> None:
        response = self.client.post(
            self.url, {"username": "reader", "password": "right-pass", "next": "/comics/arcos/"}
        )
        self.assertRedirects(response, "/comics/arcos/", fetch_redirect_response=False)

    def test_post_rejects_an_external_next(self) -> None:
        for target in ["https://evil.example.com/", "//evil.example.com/", "javascript:alert(1)"]:
            with self.subTest(next=target):
                self.client.logout()
                response = self.client.post(self.url, {"username": "reader", "password": "right-pass", "next": target})
                self.assertRedirects(response, LANDING, fetch_redirect_response=False)

    def test_failed_post_keeps_the_next(self) -> None:
        response = self.client.post(self.url, {"username": "reader", "password": "wrong", "next": "/comics/"})
        self.assertContains(response, '<input type="hidden" name="next" value="/comics/">', html=True)


class RegistrationTests(ComiTestCase):
    url = "/registro/"

    def data(self, **overrides: str) -> dict[str, str]:
        values = {
            "username": "newreader",
            "email": "new.reader@example.com",
            "password1": "a-Long-unusual-pass-42",
            "password2": "a-Long-unusual-pass-42",
        }
        values.update(overrides)
        return values

    def assert_rejected(self, response: object, field: str) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_url_and_page(self) -> None:
        self.assertEqual(reverse("register"), self.url)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "accounts/register.html")
        for name in ["username", "email", "password1", "password2"]:
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, f'href="{reverse("login")}"')

    def test_happy_path_creates_a_signed_in_collector(self) -> None:
        response = self.client.post(self.url, self.data(email="  New.Reader@EXAMPLE.com "))

        self.assertRedirects(response, LANDING, fetch_redirect_response=False)
        user = User.objects.get(username="newreader")
        self.assertEqual(user.email, "New.Reader@example.com")  # stripped, domain lowercased
        self.assertTrue(user.check_password("a-Long-unusual-pass-42"))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [COLLECTORS])
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_success_follows_a_same_site_next_only(self) -> None:
        response = self.client.post(self.url, {**self.data(), "next": "/comics/arcos/"})
        self.assertRedirects(response, "/comics/arcos/", fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(
            self.url, {**self.data(username="other", email="other@example.com"), "next": "https://evil.example.com/"}
        )
        self.assertRedirects(response, LANDING, fetch_redirect_response=False)

    def test_email_is_required(self) -> None:
        for email in ["", "   ", "not-an-email"]:
            with self.subTest(email=email):
                self.assert_rejected(self.client.post(self.url, self.data(email=email)), "email")
        self.assertFalse(User.objects.filter(username="newreader").exists())

    def test_duplicate_email_ignores_case(self) -> None:
        make_user(email="new.reader@example.com")
        response = self.client.post(self.url, self.data(email="NEW.Reader@Example.com"))
        self.assert_rejected(response, "email")
        self.assertContains(response, "A user with that email already exists.")
        self.assertFalse(User.objects.filter(username="newreader").exists())

    def test_existing_users_without_email_do_not_block_registration(self) -> None:
        make_user(email="")
        make_user(email="")
        response = self.client.post(self.url, self.data())
        self.assertRedirects(response, LANDING, fetch_redirect_response=False)

    def test_duplicate_username_ignores_case(self) -> None:
        make_user(username="NewReader")
        self.assert_rejected(self.client.post(self.url, self.data()), "username")
        self.assertEqual(User.objects.filter(username__iexact="newreader").count(), 1)

    def test_weak_passwords_are_rejected_by_the_validators(self) -> None:
        for password in ["123", "password", "12345678901", "newreader1"]:
            with self.subTest(password=password):
                response = self.client.post(self.url, self.data(password1=password, password2=password))
                self.assert_rejected(response, "password2")
        self.assertFalse(User.objects.filter(username="newreader").exists())

    def test_mismatched_passwords(self) -> None:
        self.assert_rejected(self.client.post(self.url, self.data(password2="another-Long-pass-42")), "password2")
        self.assertFalse(User.objects.filter(username="newreader").exists())

    def test_overlong_values_are_rejected(self) -> None:
        self.assert_rejected(self.client.post(self.url, self.data(username="u" * 151)), "username")
        self.assert_rejected(self.client.post(self.url, self.data(email=f"{'a' * 250}@example.com")), "email")
        self.assertEqual(User.objects.count(), 0)

    def test_signed_in_users_are_redirected(self) -> None:
        self.client.force_login(make_user())
        self.assertRedirects(self.client.get(self.url), LANDING, fetch_redirect_response=False)
        response = self.client.post(self.url, {**self.data(), "next": "/comics/"})
        self.assertRedirects(response, "/comics/", fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(username="newreader").exists())

    def test_other_methods_are_not_allowed(self) -> None:
        self.assertEqual(self.client.put(self.url).status_code, 405)


class LogoutTests(ComiTestCase):
    def test_post_signs_out_and_goes_to_the_login_page(self) -> None:
        for user in [make_user(), make_superuser()]:
            with self.subTest(superuser=user.is_superuser):
                self.client.force_login(user)
                response = self.client.post(reverse("logout"))
                self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_is_not_allowed_and_keeps_the_session(self) -> None:
        user = make_user()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_post_without_csrf_token_is_refused(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(make_user())
        self.assertEqual(client.post(reverse("logout")).status_code, 403)
        self.assertIn("_auth_user_id", client.session)

    def test_user_menu_signs_out_with_a_post_form(self) -> None:
        self.client.force_login(make_user())
        html = self.client.get(reverse("comics")).content.decode()
        form = html[html.index(f'action="{reverse("logout")}"') - 40 :]
        self.assertIn('method="post"', form[:60])
        self.assertIn('name="csrfmiddlewaretoken"', form[: form.index("</form>")])

    def test_admin_logout_goes_to_the_login_page(self) -> None:
        self.client.force_login(make_superuser())
        response = self.client.post(reverse("admin:logout"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


class ErrorPageTests(ComiTestCase):
    def test_collector_gets_the_styled_403_page_on_gestion(self) -> None:
        self.client.force_login(make_user())
        response = self.client.get(reverse("manage_home"))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, "Access denied", status_code=403)
        self.assertContains(response, f'href="{reverse("comics")}"', status_code=403)

    def test_administrator_does_not_get_it(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.assertEqual(self.client.get(reverse("manage_home")).status_code, 200)

    def test_styled_404_page(self) -> None:
        self.client.force_login(make_user())
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        self.assertContains(response, "Page not found", status_code=404)

    @override_settings(DEBUG=True)
    def test_debug_mode_keeps_the_technical_404_and_the_styled_403(self) -> None:
        self.client.force_login(make_user())
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateNotUsed(response, "404.html")
        self.assertEqual(self.client.get(reverse("manage_home")).status_code, 403)
