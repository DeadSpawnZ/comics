"""Helpers of the Gestión edition form's import flow: required fields of the "Create" dialogs marked
and described while empty, editorial aliases (model rules, catalogue matching, the endpoint that
remembers one) and creating the artists an import did not find (the endpoint and the artist pickers
it feeds). HTTP to the catalogues is never used: proposals are built from the fixtures. Where the
created artists get selected happens in the browser (js/custom/catalog_import.js) and is not covered
here."""

import re
from typing import Any
from unittest import mock

from django.contrib.admin.models import ADDITION, LogEntry
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from comics.catalog_sources import metron
from comics.catalog_sources.mapper import build_proposal
from comics.forms import ArtistCreateForm, EditorialDialogForm, PublishingDialogForm
from comics.models import Artist, ArtistQuerySet, Editorial, EditorialAlias
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_editorial, make_user
from .test_catalog_sources import METRON_ISSUE, CatalogTestCase


def tag(html: str, pattern: str) -> str:
    """The first opening tag that matches `pattern` (a regex inside the tag)."""
    found = re.search(rf"<(?:input|select)[^>]*{pattern}[^>]*>", html)
    assert found, pattern
    return found.group(0)


# ---------- Required fields of the dialogs ----------


class RequiredMarkersTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.html = self.client.get(reverse("manage_edition_new")).content.decode()

    def test_empty_required_fields_start_highlighted_and_described_by_their_hint(self) -> None:
        for name, hint in (
            ("new_publishing-title", "Required: choose a title or create one with +."),
            ("new_publishing-language", "Required: choose the language."),
            ("new_publishing-date", "Required: enter the start date."),
            ("new_publishing-publishing_title", "Required: fill in this field."),
            ("new_publishing-editorials", "Required: choose an editorial or create one with +."),
            ("new_title-name", "Required: fill in this field."),
            ("new_editorial-name", "Required: fill in this field."),
            ("new_editorial-country", "Required: choose an option."),
            ("new_artist-name", "Required: fill in this field."),
        ):
            with self.subTest(field=name):
                control = tag(self.html, f'name="{name}"')
                self.assertIn('aria-required="true"', control)
                self.assertIn(f'aria-describedby="id_{name}-hint"', control)
                hint_div = re.search(
                    rf'<div class="required-hint" id="id_{name}-hint"[^>]*>(.*?)</div>', self.html, re.S
                )
                self.assertIsNotNone(hint_div)
                self.assertNotIn("hidden", hint_div.group(0).split(">")[0])
                self.assertIn(hint, hint_div.group(1))
                self.assertIn("data-required-hint", hint_div.group(0))
        # The wrapper is in the "needs a value" state and the label carries the asterisk.
        self.assertRegex(
            self.html,
            r'<div class="form-floating md-field needs-value" data-required-field>\s*<select name="new_publishing-title"',
        )
        self.assertIn('<span class="required-mark" aria-hidden="true"> *</span>', self.html)

    def test_a_required_field_with_a_value_is_marked_but_not_described(self) -> None:
        serie = tag(self.html, 'name="new_publishing-serie"')  # starts as "1st"
        self.assertIn('aria-required="true"', serie)
        self.assertNotIn("aria-describedby", serie)
        self.assertRegex(
            self.html, r'<div class="required-hint" id="id_new_publishing-serie-hint" data-required-hint hidden>'
        )

    def test_optional_fields_are_not_marked(self) -> None:
        for name in ("new_publishing-year", "new_publishing-metron_series_id"):
            with self.subTest(field=name):
                control = tag(self.html, f'name="{name}"')
                self.assertNotIn("aria-required", control)
                self.assertNotIn(f"id_{name}-hint", self.html)

    def test_prefilled_values_are_not_described(self) -> None:
        form = PublishingDialogForm(initial={"language": "en", "date": "2020-01-01"})
        self.assertNotIn("aria-describedby", str(form["language"]))
        self.assertIn('aria-describedby="id_new_publishing-title-hint"', str(form["title"]))
        self.assertEqual(form.fields["language"].widget.attrs["aria-required"], "true")

    def test_server_validation_is_unchanged(self) -> None:
        form = EditorialDialogForm(data={"new_editorial-name": "", "new_editorial-country": ""})
        self.assertEqual(set(form.errors), {"name", "country"})


# ---------- Editorial aliases: model ----------


class EditorialAliasModelTests(ComiTestCase):
    def setUp(self) -> None:
        self.marvel = make_editorial(name="Marvel Comics")
        self.dc = make_editorial(name="DC")

    def validate(self, editorial: Editorial, name: str) -> EditorialAlias:
        alias = EditorialAlias(editorial=editorial, name=name)
        alias.full_clean(exclude=["key"])
        return alias

    def test_a_valid_alias_gets_its_key(self) -> None:
        alias = self.validate(self.marvel, "  Marvel   Worldwide ")
        alias.save()
        self.assertEqual((alias.name, alias.key), ("Marvel Worldwide", "marvel worldwide"))
        self.assertEqual(str(alias), "Marvel Worldwide")

    def test_aliases_are_unique_ignoring_case_accents_and_spaces(self) -> None:
        EditorialAlias.objects.create(editorial=self.marvel, name="Marvel")
        for name in ("MARVEL", " márvel ", "Marvel"):
            with self.subTest(name=name), self.assertRaises(ValidationError) as caught:
                self.validate(self.dc, name)
            self.assertEqual(caught.exception.error_dict["name"][0].code, "duplicate")
            self.assertEqual(caught.exception.messages, ["“Marvel” is already an alias of Marvel Comics."])

    def test_the_key_is_unique_in_the_database(self) -> None:
        EditorialAlias.objects.create(editorial=self.marvel, name="Marvel")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EditorialAlias.objects.create(editorial=self.dc, name="MÁRVEL")

    def test_an_alias_can_not_be_an_editorial_name(self) -> None:
        for name in ("marvel comics", "dc", " Márvel  Comics "):
            with self.subTest(name=name), self.assertRaises(ValidationError) as caught:
                self.validate(self.marvel, name)
            self.assertEqual(caught.exception.error_dict["name"][0].code, "editorial_name")

    def test_its_own_editorial_is_compared_by_the_name_being_saved(self) -> None:
        # Renaming "Marvel Comics" to "Marvel" in the admin while adding the alias "Marvel Comics".
        self.marvel.name = "Marvel"
        self.validate(self.marvel, "Marvel Comics")
        with self.assertRaises(ValidationError):
            self.validate(self.marvel, "marvel")

    def test_an_editorial_name_can_not_be_an_alias(self) -> None:
        EditorialAlias.objects.create(editorial=self.marvel, name="Marvel")
        editorial = Editorial(name="MARVEL", country="US")
        with self.assertRaises(ValidationError) as caught:
            editorial.full_clean()
        self.assertEqual(caught.exception.messages, ["“Marvel” is an alias of Marvel Comics: delete the alias first."])
        form = EditorialDialogForm(data={"new_editorial-name": "marvel", "new_editorial-country": "US"})
        self.assertEqual(form.errors["name"], ["“Marvel” is an alias of Marvel Comics: delete the alias first."])

    def test_remember_is_idempotent_for_the_same_editorial(self) -> None:
        alias, created = EditorialAlias.objects.remember(self.marvel, "Marvel")
        self.assertTrue(created)
        again, created = EditorialAlias.objects.remember(self.marvel, "MARVEL ")
        self.assertEqual((again.pk, created), (alias.pk, False))
        with self.assertRaises(ValidationError):
            EditorialAlias.objects.remember(self.dc, "marvel")
        self.assertEqual(EditorialAlias.objects.count(), 1)

    def test_natural_key(self) -> None:
        alias = EditorialAlias.objects.create(editorial=self.marvel, name="Marvel")
        self.assertEqual(alias.natural_key(), ("Marvel",))
        self.assertEqual(EditorialAlias.objects.get_by_natural_key("marvel").pk, alias.pk)

    def test_deleting_the_editorial_deletes_its_aliases(self) -> None:
        EditorialAlias.objects.create(editorial=self.dc, name="Detective Comics")
        self.dc.delete()
        self.assertFalse(EditorialAlias.objects.exists())


# ---------- Editorial aliases: catalogue matching ----------


class MatchByAliasTests(CatalogTestCase):
    def test_the_publisher_matches_an_alias(self) -> None:
        marvel = make_editorial(name="Marvel Comics")
        EditorialAlias.objects.create(editorial=marvel, name="MARVEL")
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))  # publisher "Marvel"
        self.assertEqual(proposal.editorial, {"id": marvel.pk, "name": "Marvel Comics"})
        self.assertEqual(proposal.unmatched["editorial"], "")
        self.assertEqual(proposal.new_publishing.editorial, {"id": marvel.pk, "name": "Marvel Comics"})
        self.assertIsNone(proposal.new_publishing.new_editorial)

    def test_names_match_ignoring_accents_and_spaces_and_win_over_aliases(self) -> None:
        named = make_editorial(name="Márvel")
        self.assertEqual(Editorial.objects.matching("  marvel "), named)
        other = make_editorial(name="Other")
        # An alias row equal to a name can only exist bypassing clean(): the name still wins.
        EditorialAlias.objects.create(editorial=other, name="Marvel")
        self.assertEqual(Editorial.objects.matching("MARVEL"), named)

    def test_no_match(self) -> None:
        make_editorial(name="DC")
        self.assertIsNone(Editorial.objects.matching("Marvel"))
        self.assertIsNone(Editorial.objects.matching("   "))
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertEqual(proposal.unmatched["editorial"], "Marvel")


# ---------- Editorial aliases: endpoint ----------


class AliasEndpointTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("manage_editorial_alias_create_api")
        self.manager = make_user(role=ADMINISTRATORS)
        self.marvel = make_editorial(name="Marvel Comics")

    def post(self, **data: Any) -> Any:
        return self.client.post(self.url, {"editorial": self.marvel.pk, "name": "Marvel", **data})

    def test_permissions_and_post_only(self) -> None:
        self.assertEqual(self.post().status_code, 302)
        self.client.force_login(make_user())
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(EditorialAlias.objects.exists())

    def test_the_csrf_token_is_required(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        data = {"editorial": self.marvel.pk, "name": "Marvel"}
        self.assertEqual(client.post(self.url, data).status_code, 403)
        client.get(reverse("manage_edition_new"))
        response = client.post(self.url, {**data, "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 200)

    def test_remembers_the_alias_and_the_next_import_matches_it(self) -> None:
        self.client.force_login(self.manager)
        response = self.post(name=" Marvel ")
        self.assertEqual(response.status_code, 200)
        alias = EditorialAlias.objects.get()
        self.assertEqual(
            response.json(),
            {
                "id": alias.pk,
                "name": "Marvel",
                "editorial": {"id": self.marvel.pk, "label": "Marvel Comics"},
                "created": True,
            },
        )
        entry = LogEntry.objects.get(content_type__model="editorialalias", object_id=str(alias.pk))
        self.assertEqual((entry.action_flag, entry.user_id), (ADDITION, self.manager.pk))
        proposal = build_proposal(metron.to_source_issue(METRON_ISSUE))
        self.assertEqual(proposal.editorial["id"], self.marvel.pk)

    def test_remembering_it_again_is_not_an_error(self) -> None:
        self.client.force_login(self.manager)
        first = self.post().json()
        again = self.post(name="MARVEL")
        self.assertEqual(again.status_code, 200)
        self.assertEqual((again.json()["id"], again.json()["created"]), (first["id"], False))
        self.assertEqual(LogEntry.objects.filter(content_type__model="editorialalias").count(), 1)

    def test_conflicts_are_field_errors(self) -> None:
        dc = make_editorial(name="DC")
        EditorialAlias.objects.create(editorial=dc, name="Detective")
        self.client.force_login(self.manager)
        for name, message in (
            ("detective", "“Detective” is already an alias of DC."),
            ("dc", "“DC” is the name of an editorial: it needs no alias."),
            ("", "This field is required."),
        ):
            with self.subTest(name=name):
                response = self.post(name=name)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"errors": {"name": [message]}, "non_field_errors": []})
        response = self.post(editorial=999999)
        self.assertEqual(set(response.json()["errors"]), {"editorial"})
        self.assertEqual(EditorialAlias.objects.count(), 1)

    def test_a_race_is_a_form_error(self) -> None:
        self.client.force_login(self.manager)
        with mock.patch.object(EditorialAlias, "save", side_effect=IntegrityError("duplicate")):
            response = self.post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"], {"name": ["That alias already exists."]})
        self.assertFalse(LogEntry.objects.filter(content_type__model="editorialalias").exists())


# ---------- Creating missing artists ----------


class ArtistCreateEndpointTests(ComiTestCase):
    def setUp(self) -> None:
        self.url = reverse("manage_artists_create_api")
        self.manager = make_user(role=ADMINISTRATORS)

    def post(self, *names: str) -> Any:
        return self.client.post(self.url, {"new_artist-name": list(names)})

    def logged(self) -> int:
        return LogEntry.objects.filter(content_type__model="artist", action_flag=ADDITION).count()

    def test_permissions_and_post_only(self) -> None:
        self.assertEqual(self.post("Ron Wilson").status_code, 302)
        self.client.force_login(make_user())
        self.assertEqual(self.post("Ron Wilson").status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(Artist.objects.exists())

    def test_the_csrf_token_is_required(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        self.assertEqual(client.post(self.url, {"new_artist-name": "Ron Wilson"}).status_code, 403)
        client.get(reverse("manage_edition_new"))
        token = client.cookies["csrftoken"].value
        response = client.post(self.url, {"new_artist-name": "Ron Wilson", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 200)

    def test_creates_several_in_order_and_logs_them(self) -> None:
        self.client.force_login(self.manager)
        response = self.post("Frank  Giacoia ", "Armando Gil")
        self.assertEqual(response.status_code, 200)
        frank, armando = Artist.objects.get(name="Frank Giacoia"), Artist.objects.get(name="Armando Gil")
        self.assertEqual(
            response.json(),
            {
                "artists": [
                    {"requested": "Frank Giacoia", "id": frank.pk, "name": "Frank Giacoia", "created": True},
                    {"requested": "Armando Gil", "id": armando.pk, "name": "Armando Gil", "created": True},
                ]
            },
        )
        entry = LogEntry.objects.get(content_type__model="artist", object_id=str(frank.pk))
        self.assertEqual((entry.action_flag, entry.user_id), (ADDITION, self.manager.pk))
        self.assertEqual(self.logged(), 2)

    def test_existing_names_answer_the_artist_and_repeating_creates_nothing(self) -> None:
        jose = Artist.objects.create(name="José García")
        self.client.force_login(self.manager)
        response = self.post("jose  garcia", "Ron Wilson", "RON WILSON")
        artists = response.json()["artists"]
        ron = Artist.objects.get(name="Ron Wilson")
        self.assertEqual(
            [(item["id"], item["created"]) for item in artists], [(jose.pk, False), (ron.pk, True), (ron.pk, False)]
        )
        self.assertEqual(artists[0]["name"], "José García")
        again = self.post("Ron Wilson").json()["artists"]
        self.assertEqual((again[0]["id"], again[0]["created"]), (ron.pk, False))
        self.assertEqual((Artist.objects.count(), self.logged()), (2, 1))

    def test_invalid_names(self) -> None:
        self.client.force_login(self.manager)
        long_name = "x" * 101
        for names, message in (
            ((), "This field is required."),
            (("Ron Wilson", "  "), "Enter a name."),
            ((long_name,), f"“{long_name}” is longer than 100 characters."),
            (("a",) * (ArtistCreateForm.MAX_NAMES + 1), "Send at most 100 names at a time."),
        ):
            with self.subTest(message=message[:30]):
                response = self.post(*names)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"errors": {"name": [message]}, "non_field_errors": []})
        self.assertFalse(Artist.objects.exists())

    def test_all_or_nothing_and_one_retry_after_a_race(self) -> None:
        self.client.force_login(self.manager)
        ron = Artist.objects.create(name="Ron Wilson")
        real = ArtistQuerySet.ensure_named
        calls = []

        def racing(queryset: ArtistQuerySet, names: list[str]) -> Any:
            calls.append(names)
            if len(calls) == 1:
                real(queryset, names)  # creates them, then the request "loses" the race
                raise IntegrityError("duplicate")
            return real(queryset, names)

        with mock.patch.object(ArtistQuerySet, "ensure_named", racing):
            response = self.post("Ron Wilson", "Tom DeFalco")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        # The first attempt was rolled back: Tom is created (and logged) once.
        self.assertEqual(Artist.objects.filter(name="Tom DeFalco").count(), 1)
        self.assertEqual(response.json()["artists"][0]["id"], ron.pk)
        self.assertEqual(self.logged(), 1)

        with mock.patch.object(ArtistQuerySet, "ensure_named", side_effect=IntegrityError("duplicate")):
            response = self.post("Someone")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["non_field_errors"], ["The artists could not be created. Try again."])


class ArtistPickerPageTests(ComiTestCase):
    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))

    def test_pickers_offer_the_create_dialog_even_without_artists(self) -> None:
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        for name in ("cover_artists", "issue_data-creators"):
            with self.subTest(picker=name):
                self.assertIn(f'data-artist-picker data-field-name="{name}"', html)
                self.assertIn(f'data-open-dialog="artist-dialog" data-artist-target="{name}"', html)
        self.assertEqual(html.count("data-artist-list"), 2)  # the lists exist to receive new artists
        self.assertNotIn("There are no artists yet.", html)
        form = re.search(r'<form id="artist-dialog-form" action="([^"]+)".*?</form>', html, flags=re.DOTALL)
        self.assertEqual(form.group(1), reverse("manage_artists_create_api"))
        self.assertIn('name="csrfmiddlewaretoken"', form.group(0))
        self.assertIn(f'"artistsCreateUrl": "{reverse("manage_artists_create_api")}"', html)
        self.assertIn(f'data-alias-url="{reverse("manage_editorial_alias_create_api")}"', html)
        self.assertIn('id="publisher-alias" hidden', html)

    def test_existing_artists_are_still_listed(self) -> None:
        Artist.objects.create(name="Ron Wilson")
        html = self.client.get(reverse("manage_edition_new")).content.decode()
        self.assertEqual(html.count('data-artist-name="Ron Wilson"'), 2)
