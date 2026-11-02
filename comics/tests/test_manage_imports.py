"""Gestión of the MyComicShop import batches (comics.views.manage_imports): the list, the new-batch API
of the series dialog, the batch page in each of its states, and its actions (resolving names,
choosing the publishing, selecting rows, editing the batch's and the rows' values, creating, linking,
closing). MyComicShop pages and cover downloads are mocked."""

import itertools
from typing import Any
from unittest import mock

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.messages import get_messages
from django.db import OperationalError, connection
from django.db.models import Model
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from comics.catalog_sources import batch_creation
from comics.catalog_sources.batches import (
    MAX_BATCH_ROWS,
    CreditState,
    CreditStatus,
    close_batch,
    credit_statuses,
    set_publishing,
)
from comics.catalog_sources.batch_creation import create_rows
from comics.catalog_sources.errors import Unavailable
from comics.models import Artist, Edition, ImportBatch, ImportBatchNumber, ImportBatchRow
from comics.roles import ADMINISTRATORS
from comics.views.manage_imports import ResolvedView

from .factories import make_edition, make_publishing, make_user
from .test_catalog_sources import OPEN_URL, CatalogTestCase, FakeResponse, png_bytes
from .test_import_batch_creation import MCS_COVER, credit, proposal
from .test_import_batch_values import chosen
from .test_import_batches import make_batch, make_number, make_row
from .test_mycomicshop_import import BATMAN_TID, page_answer
from .test_mycomicshop_link import IVID_1A, IVID_1L

TID = 57389539


class ManageImportsTestCase(CatalogTestCase):
    """A Gestión user (signed in), a plain collector, a publishing with two artists and an open batch."""

    def setUp(self) -> None:
        super().setUp()
        self.manager = make_user(role=ADMINISTRATORS)
        self.collector = make_user()
        self.client.force_login(self.manager)
        self.publishing = make_publishing(publishing_title="Batman Spawn", year=2023, language="en")
        self.todd = Artist.objects.create(name="Todd McFarlane")
        self.greg = Artist.objects.create(name="Greg Capullo")
        self.batch = make_batch(
            publishing=self.publishing, series_id=TID, series_title="Batman Spawn", created_by=self.manager
        )

    def page_url(self, batch: ImportBatch | None = None) -> str:
        return reverse("manage_import_batch", args=[(batch or self.batch).pk])

    def get(self, url: str) -> HttpResponse:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response

    def post(self, name: str, data: dict[str, Any] | None = None, *args: int) -> HttpResponse:
        return self.client.post(reverse(name, args=[self.batch.pk, *args]), data or {})

    def messages(self, response: HttpResponse) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def last_message(self, response: HttpResponse) -> str:
        """The message of `response`: unfollowed redirects keep the earlier requests' messages queued."""
        return self.messages(response)[-1]

    def logs(self, obj: Model) -> list[LogEntry]:
        return list(
            LogEntry.objects.filter(object_id=str(obj.pk), content_type__model=obj._meta.model_name).order_by("pk")
        )

    def row(self, group: ImportBatchNumber, ivid: int, position: int, variant: str, **fields: Any) -> ImportBatchRow:
        fields.setdefault("proposal", proposal(variant))
        return make_row(group, ivid, position=position, label=f"{group.number}{variant}", **fields)


class PermissionTests(ManageImportsTestCase):
    """Every view follows management_required: anonymous users go to the login, collectors get 403."""

    def setUp(self) -> None:
        super().setUp()
        self.group = make_number(self.batch)
        self.batch_row = self.row(self.group, 101, 0, "A")

    def pages(self) -> list[str]:
        return [reverse("manage_import_batches"), self.page_url()]

    def actions(self) -> list[str]:
        pk = self.batch.pk
        return [
            reverse("manage_import_batch_resolve_name", args=[pk]),
            reverse("manage_import_batch_clear_name", args=[pk]),
            reverse("manage_import_batch_create", args=[pk]),
            reverse("manage_import_batch_link", args=[pk, self.batch_row.pk]),
            reverse("manage_import_batch_publishing", args=[pk]),
            reverse("manage_import_batch_select", args=[pk]),
            reverse("manage_import_batch_defaults", args=[pk]),
            reverse("manage_import_batch_edit_row", args=[pk, self.batch_row.pk]),
            reverse("manage_import_batch_reset_row", args=[pk, self.batch_row.pk]),
            reverse("manage_import_batch_close", args=[pk]),
            reverse("manage_import_batch_new"),
        ]

    def test_anonymous_users_go_to_the_login(self) -> None:
        self.client.logout()
        for url in self.pages() + self.actions():
            with self.subTest(url=url):
                response = self.client.post(url) if url in self.actions() else self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_collectors_get_403(self) -> None:
        self.client.force_login(self.collector)
        for url in self.pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for url in self.actions():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"name": "Todd McFarlane", "action": "discard"}).status_code, 403)
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.status, self.batch.name_resolutions), (ImportBatch.Status.OPEN, {}))
        self.assertEqual(self.batch.defaults, {})

    def test_actions_only_take_posts(self) -> None:
        for url in self.actions():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)


class BatchListTests(ManageImportsTestCase):
    def test_batches_are_listed_newest_first_with_their_row_counts(self) -> None:
        group = make_number(self.batch)
        self.row(group, 101, 0, "A")
        self.row(group, 102, 1, "B", status=ImportBatchRow.Status.FAILED)
        self.row(group, 103, 2, "C", status=ImportBatchRow.Status.CREATED)
        newer = make_batch(series_title="Spawn", series_year=1992, status=ImportBatch.Status.CLOSED)
        response = self.get(reverse("manage_import_batches"))
        listed = list(response.context["page_obj"])
        self.assertEqual(listed, [newer, self.batch])
        older = listed[1]
        self.assertEqual(
            (older.row_count, older.pending_count, older.failed_count, older.created_count, older.linked_count),
            (3, 1, 1, 1, 0),
        )
        self.assertContains(response, self.page_url(newer))
        self.assertContains(response, "Spawn (1992)")
        self.assertContains(response, "1 failed")
        self.assertContains(response, "No publishing")  # the newer batch has none

    def test_the_side_nav_links_to_the_imports(self) -> None:
        response = self.get(reverse("manage_home"))
        self.assertContains(response, reverse("manage_import_batches"))

    def test_an_empty_list(self) -> None:
        ImportBatch.objects.all().delete()
        self.assertContains(self.get(reverse("manage_import_batches")), "No import batches yet.")

    def test_the_list_opens_a_new_batch_in_the_series_dialog(self) -> None:
        response = self.get(reverse("manage_import_batches"))
        self.assertContains(response, 'id="import-new"', count=1)
        self.assertContains(response, 'id="import-new-series"', count=1)
        self.assertContains(response, "New batch")
        self.assertContains(response, 'id="catalog-series-dialog"', count=1)
        self.assertContains(response, 'id="catalog-series-batch"', count=1)
        self.assertContains(response, 'id="catalog-series-dialog-data"', count=1)
        self.assertContains(response, "js/custom/catalog_series_dialog.js")
        # The edition form's import box is not on this page: the dialog cannot fill a form here.
        self.assertNotContains(response, "js/custom/catalog_import.js")
        self.assertNotContains(response, 'id="catalog-load"')
        self.assertEqual(
            response.context["series_dialog"],
            {
                "seriesUrl": reverse("manage_catalog_mycomicshop_series"),
                "linkUrl": reverse("manage_catalog_mycomicshop_link"),
                "batchUrl": reverse("manage_import_batch_new"),
                "maxBatchRows": MAX_BATCH_ROWS,
                "sourceName": "MyComicShop",
            },
        )

    def test_the_edition_form_still_has_the_series_dialog_and_its_script(self) -> None:
        response = self.get(reverse("manage_edition_new"))
        html = response.content.decode()
        self.assertContains(response, 'id="catalog-series-dialog-data"', count=1)
        self.assertEqual(response.context["series_dialog"]["batchUrl"], reverse("manage_import_batch_new"))
        # The dialog's script comes first: the import box uses window.ComiSeriesDialog.
        self.assertLess(html.index("js/custom/catalog_series_dialog.js"), html.index("js/custom/catalog_import.js"))


class NewBatchTests(ManageImportsTestCase):
    """The API the series dialog creates an import batch with (manage_import_batch_new)."""

    def create(self, data: dict[str, Any]) -> HttpResponse:
        return self.client.post(reverse("manage_import_batch_new"), data)

    def test_the_batch_is_created_and_its_page_answered(self) -> None:
        ImportBatch.objects.all().delete()
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.create({"tid": str(BATMAN_TID), "ivids": [str(IVID_1L), str(IVID_1A)]})
        self.assertEqual(response.status_code, 200)
        batch = ImportBatch.objects.get()
        self.assertEqual(response.json(), {"url": self.page_url(batch)})
        self.assertEqual((batch.series_id, batch.created_by), (BATMAN_TID, self.manager))
        self.assertEqual([row.label for row in batch.rows.all()], ["1A", "1L"])

    def test_invalid_selections_are_answered_with_400(self) -> None:
        too_many = [str(ivid) for ivid in range(1, MAX_BATCH_ROWS + 2)]
        cases = [
            ({"tid": str(BATMAN_TID)}, "Select at least one variant."),
            ({"tid": str(BATMAN_TID), "ivids": too_many}, f"A batch can have at most {MAX_BATCH_ROWS} variants."),
            ({"ivids": [str(IVID_1A)]}, "The MyComicShop series is missing."),
            ({"tid": "abc", "ivids": [str(IVID_1A)]}, "Enter a whole number."),
            ({"tid": str(BATMAN_TID), "ivids": ["1A"]}, "The selection is not valid."),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                with mock.patch(OPEN_URL) as open_url:
                    response = self.create(data)
                self.assertEqual((response.status_code, response.json()), (400, {"error": message}))
                open_url.assert_not_called()
        self.assertEqual(list(ImportBatch.objects.all()), [self.batch])

    def test_a_variant_the_series_does_not_list_is_answered_with_404(self) -> None:
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.create({"tid": str(BATMAN_TID), "ivids": [str(IVID_1A), "999"]})
        self.assertEqual(response.status_code, 404)
        self.assertIn("999", response.json()["error"])
        self.assertEqual(list(ImportBatch.objects.all()), [self.batch])

    def test_a_variant_an_edition_already_has_is_answered_with_409(self) -> None:
        edition = make_edition(number="1", mycomicshop_id=IVID_1L)
        with mock.patch(OPEN_URL, side_effect=page_answer):
            response = self.create({"tid": str(BATMAN_TID), "ivids": [str(IVID_1A), str(IVID_1L)]})
        self.assertEqual(
            (response.status_code, response.json()),
            (409, {"error": f"{edition.short_name} already has this MyComicShop ID."}),
        )
        self.assertEqual(list(ImportBatch.objects.all()), [self.batch])

    def test_mycomicshop_errors_are_answered_with_their_status(self) -> None:
        error = Unavailable("MyComicShop did not answer in time. Try again in a moment.")
        with mock.patch("comics.catalog_sources.mycomicshop.fetch_series", side_effect=error):
            response = self.create({"tid": str(BATMAN_TID), "ivids": [str(IVID_1A)]})
        self.assertEqual((response.status_code, response.json()), (504, {"error": error.message}))
        self.assertEqual(list(ImportBatch.objects.all()), [self.batch])


class CreditStatusesTests(ManageImportsTestCase):
    def test_every_state_once_per_name(self) -> None:
        self.batch.name_resolutions = {
            "greg capulo": {"name": "Greg Capulo", "action": "artist", "artist_id": self.greg.pk},
            "jon glapion": {"name": "Jon Glapion", "action": "new", "artist_id": None, "new_name": "Jonathan Glapion"},
            "inker": {"name": "Inker", "action": "discard", "artist_id": None},
        }
        credits = [
            credit("Todd McFarlane", self.todd.pk),
            credit("todd  mcfarlane", self.todd.pk, True),  # the same name again: listed once
            credit("Greg Capullo", self.greg.pk, True),
            credit("Nobody Known"),
            credit("Greg Capulo"),
            credit("Jon Glapion"),
            credit("Inker", self.todd.pk),  # a resolution wins over the match
        ]
        self.assertEqual(
            credit_statuses(self.batch, credits),
            [
                CreditStatus("Todd McFarlane", CreditState.MATCHED, self.todd.pk),
                CreditStatus("Greg Capullo", CreditState.GUESSED, self.greg.pk),
                CreditStatus("Nobody Known", CreditState.UNMATCHED, None),
                CreditStatus("Greg Capulo", CreditState.RESOLVED, self.greg.pk),
                CreditStatus("Jon Glapion", CreditState.NEW, None, "Jonathan Glapion"),
                CreditStatus("Inker", CreditState.DISCARDED, None),
            ],
        )


class BatchPageTests(ManageImportsTestCase):
    def test_a_batch_without_publishing_cannot_create(self) -> None:
        batch = make_batch(series_title="Spawn", warnings=["The publishing matched a title written otherwise."])
        self.row(make_number(batch), 101, 0, "A")
        response = self.get(self.page_url(batch))
        self.assertContains(response, "No publishing of Comi matched this series")
        self.assertContains(response, "The publishing matched a title written otherwise.")
        self.assertFalse(response.context["can_create"])
        self.assertContains(response, 'class="md-btn md-btn--filled" disabled')

    def test_the_header_links_the_series_and_the_publishing(self) -> None:
        response = self.get(self.page_url())
        self.assertContains(response, f"https://www.mycomicshop.com/search?TID={TID}")
        self.assertContains(response, reverse("manage_publishing_edit", args=[self.publishing.pk]))

    def test_pending_and_resolved_names(self) -> None:
        group = make_number(
            self.batch,
            creators=[credit("Jonathan Glapion"), credit("Todd McFarlane", self.todd.pk, True)],
        )
        self.row(group, 101, 0, "A", proposal=proposal("A", cover_artists=[credit("Greg Capulo")]))
        self.batch.name_resolutions = {
            "greg capulo": {"name": "Greg Capulo", "action": "artist", "artist_id": self.greg.pk}
        }
        self.batch.save()
        response = self.get(self.page_url())
        pending = response.context["pending"]
        self.assertEqual([name.name for name in pending], ["Jonathan Glapion", "Todd McFarlane"])
        self.assertEqual((pending[1].artist, pending[1].numbers, pending[1].rows), ("Todd McFarlane", 1, 0))
        self.assertContains(response, "No artist has this name")
        self.assertContains(response, "Guessed from the text: Todd McFarlane?")
        # Each pending name can be associated, created (its spelling prefilled) or discarded.
        self.assertContains(response, 'name="new_name" value="Jonathan Glapion"')
        # The artists are listed once for the page; each name's select is filled from them, the
        # guessed artist preselected.
        self.assertContains(response, 'id="import-artists"', count=1)
        self.assertIn([self.todd.pk, "Todd McFarlane"], response.context["artist_options"])
        self.assertContains(response, f'data-selected="{self.todd.pk}"', count=1)
        self.assertContains(response, 'data-selected=""', count=1)
        self.assertContains(response, reverse("manage_import_batch_resolve_name", args=[self.batch.pk]))
        # The resolved name is listed with its undo; the row's chip shows the artist it became.
        self.assertEqual(response.context["resolved"], [ResolvedView("Greg Capulo", "artist", "Greg Capullo")])
        self.assertContains(response, reverse("manage_import_batch_clear_name", args=[self.batch.pk]))
        chips = response.context["numbers"][0].row_list[0].chips
        self.assertEqual([(chip.label, chip.state, chip.source_name) for chip in chips], [("Greg Capullo", "resolved", "Greg Capulo")])
        story = response.context["numbers"][0].chips
        self.assertEqual([chip.state for chip in story], ["unmatched", "guessed"])

    def test_numbers_show_their_issue(self) -> None:
        existing = make_edition(publishing=self.publishing, number="1", variant="A")
        make_number(self.batch, "1", issue=existing.issue)
        make_number(self.batch, "2", position=1, creators=[credit("Todd McFarlane", self.todd.pk)])
        response = self.get(self.page_url())
        self.assertContains(response, reverse("manage_issue_history", args=[existing.issue.pk]))
        self.assertContains(response, "The batch does not change the story creators of an existing issue.")
        self.assertContains(response, "New issue")
        self.assertEqual([chip.state for chip in response.context["numbers"][1].chips], ["matched"])

    def test_rows_show_their_values_state_and_links(self) -> None:
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        created = make_edition(publishing=self.publishing, number="1", variant="C")
        group = make_number(self.batch, issue=main.issue)
        probable = self.row(
            group,
            101,
            0,
            "A",
            match=ImportBatchRow.Match.PROBABLE,
            selected=False,
            source_text="Todd McFarlane cover",
            proposal=proposal("A", cover_url=MCS_COVER, warnings=["The cover price is in CAD."]),
        )
        probable.probable_editions.set([main])
        refused_printing = proposal("B", ratio="1:25", rejected=["printing"])
        del refused_printing["printing"]  # e.g. a 7th printing, which Comi has no choice for
        failed = self.row(
            group,
            102,
            1,
            "B",
            status=ImportBatchRow.Status.FAILED,
            errors={"cover_artists": ["Resolve these names first: Greg Capulo."], "__all__": ["Something else."]},
            proposal=refused_printing,
        )
        done = self.row(group, 103, 2, "C", status=ImportBatchRow.Status.CREATED, edition=created)
        response = self.get(self.page_url())
        self.assertContains(response, f'src="{MCS_COVER}"')
        self.assertContains(response, "Possible match")
        self.assertContains(response, "The cover price is in CAD.")
        self.assertContains(response, "Todd McFarlane cover")
        self.assertContains(response, "Cover artists: Resolve these names first: Greg Capulo.")
        self.assertContains(response, "<li>Something else.</li>", html=True)
        self.assertContains(response, reverse("manage_import_batch_link", args=[self.batch.pk, probable.pk]))
        self.assertContains(response, reverse("manage_edition_edit", args=[created.pk]))
        rows = {row.pk: row for row in response.context["numbers"][0].row_list}
        self.assertTrue(rows[probable.pk].can_link)
        self.assertFalse(rows[done.pk].can_link)
        summary = {item.label: item for item in rows[failed.pk].summary}
        self.assertEqual(summary["Ratio"].value, "1:25")
        self.assertTrue(summary["Printing"].missing)  # refused by Comi, nothing replaces it
        self.assertEqual((summary["Rarity"].value, summary["Rarity"].note), ("Common", "preselected"))
        self.assertEqual(summary["Format"].value, "Single issue")
        # Only the failed row is retried: the probable one is unselected and the third one is created.
        self.assertEqual(response.context["to_create"], 1)

    def test_a_closed_batch_shows_no_actions(self) -> None:
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        group = make_number(self.batch, issue=main.issue)
        probable = self.row(group, 101, 0, "A", match=ImportBatchRow.Match.PROBABLE)
        probable.probable_editions.set([main])
        self.row(group, 102, 1, "B", proposal=proposal("B", cover_artists=[credit("Greg Capulo")]))
        self.batch.status = ImportBatch.Status.CLOSED
        self.batch.save()
        response = self.get(self.page_url())
        self.assertContains(response, "The batch is closed")
        self.assertContains(response, "Greg Capulo")  # still listed as pending, without its forms
        for name in ("create", "close", "resolve_name", "publishing", "select", "defaults"):
            with self.subTest(action=name):
                self.assertNotContains(response, reverse(f"manage_import_batch_{name}", args=[self.batch.pk]))
        self.assertNotContains(response, reverse("manage_import_batch_link", args=[self.batch.pk, probable.pk]))
        self.assertNotContains(response, reverse("manage_import_batch_edit_row", args=[self.batch.pk, probable.pk]))
        self.assertNotContains(response, 'id="import-artists"')
        self.assertNotContains(response, 'form="import-selection"')
        self.assertContains(response, "check_box</span>Selected")  # the selection is shown as state only

    def test_an_unknown_batch_is_404(self) -> None:
        self.assertEqual(self.client.get(reverse("manage_import_batch", args=[999999])).status_code, 404)

    def test_the_queries_do_not_grow_with_the_rows(self) -> None:
        sequence = itertools.count(1)
        elsewhere = make_publishing()  # the probable editions; their publishing does not matter here

        def build(batch: ImportBatch, numbers: int, rows_per_number: int) -> None:
            for position in range(numbers):
                issue = make_edition(publishing=self.publishing, number=str(next(sequence))).issue
                group = make_number(
                    batch,
                    str(position + 1),
                    position=position,
                    issue=issue if position else None,
                    creators=[credit("Todd McFarlane", self.todd.pk), credit(f"Unknown {position}")],
                )
                for index in range(rows_per_number):
                    row = self.row(
                        group,
                        next(sequence),
                        position * 10 + index,
                        "ABCDE"[index],
                        match=ImportBatchRow.Match.PROBABLE,
                        status=ImportBatchRow.Status.CREATED if index == 1 else ImportBatchRow.Status.PENDING,
                        edition=issue.editions.first() if index == 1 else None,
                        proposal=proposal("A", cover_artists=[credit("Greg Capullo", self.greg.pk, True)]),
                        overrides={"ratio": "1:5", "notes": "", "cover_artists": [chosen(self.todd)]}
                        if index == 2
                        else {},
                    )
                    row.probable_editions.set([make_edition(publishing=elsewhere, number=str(next(sequence)))])
            batch.name_resolutions = {"unknown 0": {"name": "Unknown 0", "action": "discard", "artist_id": None}}
            batch.defaults = {"format": "trade_paperback", "release_date": "2024-01-01"}
            batch.save()

        small = self.batch
        build(small, 1, 2)
        large = make_batch(publishing=self.publishing, series_id=TID, series_title="Large", created_by=self.manager)
        build(large, 3, 3)

        def queries(batch: ImportBatch) -> int:
            with CaptureQueriesContext(connection) as captured:
                self.get(self.page_url(batch))
            return len(captured)

        self.assertEqual(queries(small), queries(large))
        # Without created rows the page also lists the publishings to choose from.
        ImportBatchRow.objects.filter(status=ImportBatchRow.Status.CREATED).update(
            status=ImportBatchRow.Status.PENDING, edition=None
        )
        self.assertTrue(self.get(self.page_url(small)).context["can_change_publishing"])
        self.assertEqual(queries(small), queries(large))


class ResolveNameTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        group = make_number(self.batch, creators=[credit("Jonathan Glapion"), credit("Greg Capulo")])
        self.row(group, 101, 0, "A")

    def resolve(self, **data: Any) -> HttpResponse:
        return self.post("manage_import_batch_resolve_name", data)

    def test_a_name_is_associated_with_an_artist(self) -> None:
        response = self.resolve(name="Greg Capulo", action="artist", artist=self.greg.pk, new_name="ignored")
        self.assertRedirects(response, f"{self.page_url()}#import-names", fetch_redirect_response=False)
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.name_resolutions["greg capulo"],
            {"name": "Greg Capulo", "action": "artist", "artist_id": self.greg.pk},
        )
        self.assertEqual(self.messages(response), ["Name resolved: Greg Capulo"])

    def test_a_new_artist_takes_the_corrected_name(self) -> None:
        self.resolve(name="Jonathan Glapion", action="new", artist=self.greg.pk, new_name="  Jonathan   Glapión ")
        self.batch.refresh_from_db()
        resolution = self.batch.name_resolutions["jonathan glapion"]
        self.assertEqual((resolution["action"], resolution["new_name"]), ("new", "Jonathan Glapión"))
        self.assertIsNone(resolution["artist_id"])
        self.assertFalse(Artist.objects.filter(name="Jonathan Glapión").exists())  # created with the batch

    def test_a_name_is_discarded(self) -> None:
        self.resolve(name="Jonathan Glapion", action="discard")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions["jonathan glapion"]["action"], "discard")

    def test_invalid_resolutions_are_reported(self) -> None:
        cases = [
            ({"name": "Greg Capulo", "action": "artist"}, "Choose an existing artist."),
            ({"name": "Greg Capulo", "action": "artist", "artist": 999999}, "Choose an existing artist."),
            ({"name": "Greg Capulo", "action": "new", "new_name": "   "}, "Enter a name."),
            ({"name": "Somebody Else", "action": "discard"}, "“Somebody Else” is not a name of this batch."),
            ({"name": "Greg Capulo", "action": "rename"}, "Select a valid choice. rename is not one of the available choices."),
            ({"action": "discard"}, "The name is missing."),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                response = self.resolve(**data)
                self.assertRedirects(response, f"{self.page_url()}#import-names", fetch_redirect_response=False)
                self.assertEqual(self.last_message(response), message)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions, {})

    def test_a_resolution_is_undone(self) -> None:
        self.resolve(name="Greg Capulo", action="discard")
        response = self.post("manage_import_batch_clear_name", {"name": "Greg Capulo"})
        self.assertRedirects(response, f"{self.page_url()}#import-names", fetch_redirect_response=False)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.name_resolutions, {})
        self.assertEqual(self.last_message(response), "Resolution undone: Greg Capulo")


class CreateTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.group = make_number(self.batch, creators=[credit("Todd McFarlane", self.todd.pk)])
        self.first = self.row(
            self.group,
            201,
            0,
            "A",
            proposal=proposal("A", cover_url=MCS_COVER, cover_artists=[credit("Jonathan Glapion")]),
        )
        self.second = self.row(self.group, 202, 1, "B", proposal=proposal("B", cover_artists=[credit("Greg Capulo")]))
        self.batch.name_resolutions = {
            "jonathan glapion": {"name": "Jonathan Glapion", "action": "new", "artist_id": None,
                                 "new_name": "Jonathan Glapion"},
        }
        self.batch.save()

    def create(self) -> HttpResponse:
        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())):
            response = self.post("manage_import_batch_create")
        self.assertRedirects(response, self.page_url(), fetch_redirect_response=False)
        return response

    def test_creating_logs_the_editions_artists_and_tid(self) -> None:
        response = self.create()
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.status, ImportBatchRow.Status.CREATED)
        self.assertEqual(self.second.status, ImportBatchRow.Status.FAILED)  # "Greg Capulo" is unresolved
        glapion = Artist.objects.get(name="Jonathan Glapion")
        [artist_log] = self.logs(glapion)
        self.assertEqual(artist_log.action_flag, ADDITION)
        self.assertEqual(artist_log.user, self.manager)
        self.assertIn(f"lote de importación #{self.batch.pk}", artist_log.change_message)
        [edition_log] = self.logs(self.first.edition)
        self.assertEqual(edition_log.action_flag, ADDITION)
        self.assertIn("IVID 201", edition_log.change_message)
        [publishing_log] = self.logs(self.publishing)
        self.assertEqual(publishing_log.action_flag, CHANGE)
        self.assertIn(f"TID {TID}", publishing_log.change_message)
        self.assertEqual(
            self.messages(response),
            ["1 edition created. 1 artist created. 1 variant could not be created: see its errors."],
        )
        # The page shows the created edition and the failed row's error.
        page = self.get(self.page_url())
        self.assertContains(page, reverse("manage_edition_edit", args=[self.first.edition.pk]))
        self.assertContains(page, "Resolve these names first: Greg Capulo.")

    def test_a_clean_run_is_a_success(self) -> None:
        self.second.selected = False
        self.second.save()
        response = self.create()
        self.assertEqual(self.messages(response), ["1 edition created. 1 artist created."])
        self.assertEqual(Edition.objects.filter(mycomicshop_id=201).count(), 1)

    def test_nothing_to_create(self) -> None:
        ImportBatchRow.objects.filter(batch=self.batch).update(selected=False)
        response = self.create()
        self.assertEqual(self.messages(response), ["There was nothing to create: no selected variant is pending."])
        self.assertFalse(LogEntry.objects.exists())

    def test_a_batch_without_publishing_is_refused(self) -> None:
        self.batch.publishing = None
        self.batch.save()
        response = self.create()
        self.assertEqual(self.messages(response), ["Choose the publishing of the batch first."])
        self.assertFalse(Edition.objects.filter(mycomicshop_id__in=[201, 202]).exists())

    def test_the_page_shows_the_values_creating_uses(self) -> None:
        """The page and create_rows read the same values (batch_creation.row_form_values)."""
        refused = proposal("A", rejected=["printing"])
        del refused["printing"]  # e.g. a 7th printing, which Comi has no choice for
        # A refused printing replaced by an empty one is never defaulted: the row fails on it.
        self.first.proposal, self.first.overrides = refused, {"printing": ""}
        self.first.save()
        # A printing the catalogue did not refuse, left empty, still gets the preselected default.
        self.second.proposal["cover_artists"] = []
        self.second.overrides = {"printing": ""}
        self.second.save()
        page = self.get(self.page_url())
        rows = {row.pk: row for row in page.context["numbers"][0].row_list}
        first = {item.label: item for item in rows[self.first.pk].summary}
        second = {item.label: item for item in rows[self.second.pk].summary}
        self.assertTrue(first["Printing"].missing)
        self.assertEqual((second["Printing"].value, second["Printing"].note), ("1st", "preselected"))
        self.create()
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.status, ImportBatchRow.Status.FAILED)
        self.assertIn("printing", self.first.errors)
        self.assertEqual(self.second.status, ImportBatchRow.Status.CREATED)
        self.assertEqual(self.second.edition.printing, "1st")

    def test_the_history_is_written_with_what_it_records(self) -> None:
        """The callbacks run inside the transactions that create the artist and the edition: when
        one fails, what it was recording is undone with it."""
        self.second.selected = False
        self.second.save()
        artists: list[str] = []

        def fail(row: ImportBatchRow) -> None:
            raise RuntimeError("history down")

        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())), self.assertRaises(RuntimeError):
            create_rows(
                self.batch, user=self.manager, on_artist_created=lambda a: artists.append(a.name), on_row_created=fail
            )
        self.assertEqual(artists, ["Jonathan Glapion"])  # called, and its transaction committed
        self.first.refresh_from_db()
        self.assertEqual((self.first.status, self.first.edition), (ImportBatchRow.Status.PENDING, None))
        self.assertFalse(Edition.objects.filter(mycomicshop_id=201).exists())

    def test_the_tid_is_logged_on_the_publishing_used(self) -> None:
        """The batch's publishing changes after the view read the batch and before create_rows locks
        it: the TID and its LogEntry go to the publishing the editions went to."""
        other = make_publishing(publishing_title="Batman/Spawn")

        def create_after_a_change(batch: ImportBatch, **kwargs: Any) -> Any:
            set_publishing(ImportBatch.objects.get(pk=batch.pk), other)  # another request, meanwhile
            return create_rows(batch, **kwargs)

        with mock.patch("comics.views.manage_imports.create_rows", side_effect=create_after_a_change):
            self.create()
        self.first.refresh_from_db()
        self.assertEqual(self.first.edition.publishing, other)
        other.refresh_from_db()
        self.assertEqual(other.mycomicshop_series_id, TID)
        [publishing_log] = self.logs(other)
        self.assertIn(f"TID {TID}", publishing_log.change_message)
        self.assertEqual(self.logs(self.publishing), [])

    def test_a_run_stopped_by_a_change_of_the_batch_is_reported(self) -> None:
        create_new_artists = batch_creation._create_new_artists

        def then_close(*args: Any, **kwargs: Any) -> Any:
            created = create_new_artists(*args, **kwargs)
            close_batch(ImportBatch.objects.get(pk=self.batch.pk))  # another request, meanwhile
            return created

        with mock.patch.object(batch_creation, "_create_new_artists", side_effect=then_close):
            response = self.create()
        self.assertEqual(
            self.messages(response),
            [
                "1 artist created. The batch changed meanwhile (closed or given another publishing): the other "
                "variants were not tried."
            ],
        )
        self.assertFalse(Edition.objects.filter(mycomicshop_id__in=[201, 202]).exists())

    def test_the_tid_log_is_written_with_the_tid(self) -> None:
        """on_series_id_filled runs in the transaction that stores the TID: when it fails, the TID is undone."""
        self.second.selected = False
        self.second.save()

        def fail(publishing_id: int) -> None:
            raise RuntimeError("history down")

        with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())), self.assertRaises(RuntimeError):
            create_rows(self.batch, user=self.manager, on_series_id_filled=fail)
        self.publishing.refresh_from_db()
        self.assertIsNone(self.publishing.mycomicshop_series_id)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, ImportBatchRow.Status.CREATED)  # saved in its own transaction


class PublishingTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.unmatched = make_batch(series_id=TID, series_title="Batman Spawn")
        self.row(make_number(self.unmatched), 101, 0, "A")

    def choose(self, batch: ImportBatch, data: dict[str, Any]) -> HttpResponse:
        response = self.client.post(reverse("manage_import_batch_publishing", args=[batch.pk]), data)
        self.assertRedirects(response, self.page_url(batch), fetch_redirect_response=False)
        return response

    def test_a_batch_without_publishing_shows_the_chooser(self) -> None:
        response = self.get(self.page_url(self.unmatched))
        self.assertContains(response, reverse("manage_import_batch_publishing", args=[self.unmatched.pk]))
        self.assertIn((self.publishing.pk, self.publishing.select_label), response.context["publishing_options"])
        self.assertNotContains(response, "Change publishing")
        self.assertFalse(response.context["can_create"])

    def test_choosing_a_publishing(self) -> None:
        main = make_edition(publishing=self.publishing, number="1", variant="A")
        response = self.choose(self.unmatched, {"publishing": self.publishing.pk})
        self.assertEqual(self.messages(response), [f"The batch now goes to {self.publishing.select_label}."])
        self.unmatched.refresh_from_db()
        self.assertEqual(self.unmatched.publishing, self.publishing)
        self.assertEqual(self.unmatched.numbers.get().issue, main.issue)
        page = self.get(self.page_url(self.unmatched))
        self.assertTrue(page.context["can_create"])
        self.assertContains(page, "Change publishing")
        self.assertContains(page, f'<option value="{self.publishing.pk}" selected>')

    def test_choosing_the_same_publishing(self) -> None:
        response = self.choose(self.batch, {"publishing": self.publishing.pk})
        self.assertEqual(self.messages(response), [f"The batch already goes to {self.publishing.select_label}."])

    def test_invalid_choices_are_reported(self) -> None:
        cases = [
            ({}, "Choose a publishing."),
            ({"publishing": 999999}, "Choose an existing publishing."),
            ({"publishing": "abc"}, "Choose an existing publishing."),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertEqual(self.last_message(self.choose(self.unmatched, data)), message)
        self.unmatched.refresh_from_db()
        self.assertIsNone(self.unmatched.publishing)

    def test_a_batch_with_editions_keeps_its_publishing(self) -> None:
        self.row(make_number(self.batch), 201, 0, "A", status=ImportBatchRow.Status.LINKED)
        page = self.get(self.page_url())
        self.assertFalse(page.context["can_change_publishing"])
        self.assertNotContains(page, reverse("manage_import_batch_publishing", args=[self.batch.pk]))
        response = self.choose(self.batch, {"publishing": make_publishing().pk})
        self.assertEqual(
            self.last_message(response),
            "The publishing can no longer change: some variants of the batch already have their edition.",
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.publishing, self.publishing)


class SelectionTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        group = make_number(self.batch)
        self.pending = self.row(group, 101, 0, "A")
        self.probable = self.row(group, 102, 1, "B", match=ImportBatchRow.Match.PROBABLE, selected=False)
        self.created = self.row(group, 103, 2, "C", status=ImportBatchRow.Status.CREATED)

    def select(self, data: dict[str, Any]) -> HttpResponse:
        response = self.post("manage_import_batch_select", data)
        self.assertRedirects(response, self.page_url(), fetch_redirect_response=False)
        return response

    def selected(self) -> list[bool]:
        return [row.selected for row in self.batch.rows.order_by("position")]

    def test_rows_without_edition_have_a_checkbox(self) -> None:
        page = self.get(self.page_url())
        self.assertContains(page, reverse("manage_import_batch_select", args=[self.batch.pk]))
        self.assertContains(page, 'form="import-selection"', count=2)
        self.assertContains(page, f'value="{self.pending.pk}" form="import-selection" checked>')
        self.assertContains(page, f'value="{self.probable.pk}" form="import-selection">')
        self.assertNotContains(page, f'id="import-select-{self.created.pk}"')
        # "Create selected" is another form: the page says to save the checkboxes first.
        self.assertContains(page, "Save the selection before creating.")

    def test_the_checked_rows_are_saved(self) -> None:
        response = self.select({"rows": [self.probable.pk, self.created.pk]})
        self.assertEqual(self.messages(response), ["Selection saved."])
        self.assertEqual(self.selected(), [False, True, True])

    def test_select_all_and_none(self) -> None:
        self.select({"select": "all", "rows": [self.pending.pk]})  # the shortcut wins over the checkboxes
        self.assertEqual(self.selected(), [True, True, True])
        self.select({"select": "none"})
        self.assertEqual(self.selected(), [False, False, True])  # a created row keeps its state

    def test_invalid_selections_are_reported(self) -> None:
        cases = [
            ({"rows": ["abc"]}, "The selection is not valid."),
            ({"rows": ["0"]}, "The selection is not valid."),
            ({"select": "some"}, "Select a valid choice. some is not one of the available choices."),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertEqual(self.last_message(self.select(data)), message)
        self.assertEqual(self.selected(), [True, False, True])


class LinkTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.main = make_edition(publishing=self.publishing, number="1", variant="A")
        self.other = make_edition(publishing=self.publishing, number="1", variant="B")
        group = make_number(self.batch, issue=self.main.issue)
        self.probable = self.row(group, 500, 0, "A", match=ImportBatchRow.Match.PROBABLE, selected=False)
        self.probable.probable_editions.set([self.main])

    def link(self, edition: Edition | int | None) -> HttpResponse:
        data = {} if edition is None else {"edition": edition if isinstance(edition, int) else edition.pk}
        response = self.post("manage_import_batch_link", data, self.probable.pk)
        anchor = f"#import-row-{self.probable.pk}"
        self.assertRedirects(response, f"{self.page_url()}{anchor}", fetch_redirect_response=False)
        return response

    def test_a_probable_row_is_linked(self) -> None:
        response = self.link(self.main)
        self.probable.refresh_from_db()
        self.assertEqual((self.probable.status, self.probable.edition), (ImportBatchRow.Status.LINKED, self.main))
        self.main.refresh_from_db()
        self.assertEqual(self.main.mycomicshop_id, 500)
        [edition_log] = self.logs(self.main)
        self.assertEqual(edition_log.action_flag, CHANGE)
        self.assertIn("IVID 500", edition_log.change_message)
        [publishing_log] = self.logs(self.publishing)
        self.assertIn(f"TID {TID}", publishing_log.change_message)
        self.assertIn(self.main.short_name, publishing_log.change_message)
        self.assertEqual(self.messages(response), [f"1A linked to {self.main.short_name}."])

    def test_invalid_links_are_reported(self) -> None:
        self.other.mycomicshop_id = 500  # another edition holds the IVID: link_mycomicshop_edition refuses
        self.other.save()
        self.probable.probable_editions.add(self.other)
        cases = [
            (None, "Choose the edition to link."),
            (self.other.pk + 1000, "Choose one of the editions the variant may be."),
            (self.main, f"{self.other.short_name} already has this MyComicShop ID."),
        ]
        for edition, message in cases:
            with self.subTest(edition=edition):
                self.assertEqual(self.last_message(self.link(edition)), message)
        self.probable.refresh_from_db()
        self.assertEqual(self.probable.status, ImportBatchRow.Status.PENDING)
        self.assertFalse(LogEntry.objects.exists())

    def test_a_row_of_another_batch_is_404(self) -> None:
        other = make_batch(publishing=self.publishing)
        foreign = self.row(make_number(other), 600, 0, "A")
        url = reverse("manage_import_batch_link", args=[self.batch.pk, foreign.pk])
        self.assertEqual(self.client.post(url, {"edition": self.main.pk}).status_code, 404)


class CloseTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.main = make_edition(publishing=self.publishing, number="1", variant="A")
        group = make_number(self.batch, issue=self.main.issue)
        self.probable = self.row(group, 500, 0, "A", match=ImportBatchRow.Match.PROBABLE)
        self.probable.probable_editions.set([self.main])
        self.pending = self.row(group, 501, 1, "B", proposal=proposal("B", cover_artists=[credit("Greg Capulo")]))

    def test_closing_makes_the_batch_read_only(self) -> None:
        response = self.post("manage_import_batch_close")
        self.assertRedirects(response, self.page_url(), fetch_redirect_response=False)
        self.assertEqual(self.messages(response), ["Batch closed."])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.CLOSED)

        attempts = [
            ("manage_import_batch_resolve_name", {"name": "Greg Capulo", "action": "discard"}, ()),
            ("manage_import_batch_clear_name", {"name": "Greg Capulo"}, ()),
            ("manage_import_batch_create", {}, ()),
            ("manage_import_batch_link", {"edition": self.main.pk}, (self.probable.pk,)),
            ("manage_import_batch_publishing", {"publishing": make_publishing().pk}, ()),
            ("manage_import_batch_select", {"select": "none"}, ()),
            ("manage_import_batch_defaults", {"rarity": "rare"}, ()),
            ("manage_import_batch_edit_row", {"ratio": "1:50"}, (self.pending.pk,)),
            ("manage_import_batch_reset_row", {}, (self.pending.pk,)),
            ("manage_import_batch_close", {}, ()),
        ]
        for name, data, args in attempts:
            with self.subTest(action=name):
                with mock.patch(OPEN_URL, return_value=FakeResponse(png_bytes())):
                    response = self.post(name, data, *args)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.last_message(response), "The batch is closed.")
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.name_resolutions, self.batch.defaults), ({}, {}))
        self.assertEqual(self.batch.publishing, self.publishing)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.overrides, {})
        for row in (self.probable, self.pending):
            row.refresh_from_db()
            self.assertEqual((row.status, row.edition, row.selected), (ImportBatchRow.Status.PENDING, None, True))
        self.main.refresh_from_db()
        self.assertIsNone(self.main.mycomicshop_id)


class DefaultsTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.batch_row = self.row(make_number(self.batch), 101, 0, "A")

    def test_the_panel_shows_the_values_set_and_the_form(self) -> None:
        page = self.get(self.page_url())
        self.assertContains(page, "None set: each variant keeps what the catalogue gives.")
        self.assertContains(page, reverse("manage_import_batch_defaults", args=[self.batch.pk]))
        self.assertContains(page, 'id="import-defaults-format"')
        self.batch.defaults = {"format": "trade_paperback", "cover_price": "3.99"}
        self.batch.save()
        page = self.get(self.page_url())
        defaults = page.context["defaults"]
        self.assertEqual(
            [(item.label, item.value) for item in defaults],
            [("Format", "TPB - Trade Paperback"), ("Cover price", "3.99")],
        )
        # The form shows what is set; an unset choice offers "Not set".
        self.assertContains(page, '<option value="trade_paperback" selected>', html=False)
        self.assertContains(page, "Not set")
        summary = {item.label: item for item in page.context["numbers"][0].row_list[0].summary}
        self.assertEqual(summary["Format"].value, "TPB - Trade Paperback")

    def test_saving_the_defaults(self) -> None:
        data = {"format": "trade_paperback", "notes": "", "rarity": "rare"}
        response = self.post("manage_import_batch_defaults", data)
        self.assertRedirects(response, self.page_url() + "#import-defaults", fetch_redirect_response=False)
        self.assertEqual(self.last_message(response), "Values for every variant saved.")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {"format": "trade_paperback", "rarity": "rare"})

    def test_invalid_defaults_are_reported_by_field(self) -> None:
        response = self.post("manage_import_batch_defaults", {"cover_price": "abc", "rarity": "rare"})
        self.assertRedirects(response, self.page_url() + "#import-defaults", fetch_redirect_response=False)
        self.assertEqual(self.last_message(response), "Cover price: Enter a number.")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.defaults, {})


class RowEditTests(ManageImportsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.group = make_number(self.batch)
        refused = proposal("A", ratio="1:25", cover_artists=[credit("Greg Capulo")], rejected=["printing"])
        del refused["printing"]
        self.pending = self.row(self.group, 101, 0, "A", proposal=refused)
        self.failed = self.row(
            self.group, 102, 1, "B", status=ImportBatchRow.Status.FAILED, errors={"__all__": ["Something else."]}
        )
        self.created = self.row(
            self.group,
            103,
            2,
            "C",
            status=ImportBatchRow.Status.CREATED,
            edition=make_edition(publishing=self.publishing, number="1", variant="C"),
        )
        self.batch.defaults = {"format": "trade_paperback"}
        self.batch.name_resolutions = {
            "greg capulo": {"name": "Greg Capulo", "action": "artist", "artist_id": self.greg.pk}
        }
        self.batch.save()

    def edit(self, row: ImportBatchRow, data: dict[str, Any]) -> HttpResponse:
        response = self.post("manage_import_batch_edit_row", data, row.pk)
        self.assertRedirects(response, self.page_url() + f"#import-row-{row.pk}", fetch_redirect_response=False)
        return response

    def rows(self) -> dict[int, ImportBatchRow]:
        page = self.get(self.page_url())
        return {row.pk: row for row in page.context["numbers"][0].row_list}

    def test_rows_without_their_edition_have_an_edit_form(self) -> None:
        page = self.get(self.page_url())
        for row in (self.pending, self.failed):
            with self.subTest(row=row.label):
                self.assertContains(page, reverse("manage_import_batch_edit_row", args=[self.batch.pk, row.pk]))
        self.assertNotContains(page, reverse("manage_import_batch_edit_row", args=[self.batch.pk, self.created.pk]))
        # The artist list is on the page for the cover artists selects, though no name is pending.
        self.assertEqual(page.context["pending"], [])
        self.assertContains(page, 'id="import-artists"', count=1)
        self.assertContains(page, f'id="import-row-{self.pending.pk}-add_artists"')
        self.assertContains(page, f'id="import-row-{self.pending.pk}-blank_variant"')
        self.assertNotContains(page, f'id="import-row-{self.pending.pk}-blank_printing"')
        # Every "Leave blank" box names its field for screen readers, and the page script finds it.
        self.assertContains(page, '<span class="visually-hidden"> (Variant)</span>')
        self.assertContains(page, "data-leave-blank")
        form ={row.pk: row for row in page.context["numbers"][0].row_list}[self.pending.pk].edit_form
        hints = {name: form.fields[name].help_text for name in ("printing", "format", "rarity", "ratio", "notes")}
        self.assertEqual(
            hints,
            {
                "printing": "Inherits nothing: Comi did not accept the catalogue's value.",
                "format": "Inherits the batch value: TPB - Trade Paperback",
                "rarity": "Inherits the preselected value: Common",
                "ratio": "Inherits the catalogue value: 1:25",
                "notes": "Inherits nothing.",
            },
        )
        # The cover artist to remove is labelled with the artist its name was resolved as.
        self.assertEqual(form.fields["drop_artists"].choices, [("Greg Capulo", "Greg Capullo")])
        self.assertContains(page, f'aria-describedby="import-row-{self.pending.pk}-ratio_helptext"')

    def test_saving_a_row_shows_its_new_values(self) -> None:
        response = self.edit(self.pending, {"printing": "2nd", "ratio": "1:50", "blank_variant": "on"})
        self.assertEqual(self.last_message(response), "1A saved.")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.overrides, {"printing": "2nd", "ratio": "1:50", "variant": ""})
        row = self.rows()[self.pending.pk]
        self.assertTrue(row.is_edited)
        summary = {item.label: item for item in row.summary}
        self.assertEqual((summary["Printing"].value, summary["Printing"].missing), ("2nd", False))
        self.assertEqual(summary["Ratio"].value, "1:50")
        self.assertEqual((summary["Variant"].value, summary["Variant"].note), ("—", "left blank"))
        # The form shows the row's changes.
        self.assertEqual(row.edit_form.initial, {"printing": "2nd", "ratio": "1:50", "blank_variant": True})
        page = self.get(self.page_url())
        self.assertContains(page, reverse("manage_import_batch_reset_row", args=[self.batch.pk, self.pending.pk]))
        self.assertNotContains(page, reverse("manage_import_batch_reset_row", args=[self.batch.pk, self.failed.pk]))

    def test_cover_artists_are_changed(self) -> None:
        self.edit(self.pending, {"drop_artists": ["Greg Capulo"], "add_artists": [str(self.todd.pk)]})
        chips = self.rows()[self.pending.pk].chips
        self.assertEqual([(chip.label, chip.state) for chip in chips], [("Todd McFarlane", "matched")])

    def test_a_failed_row_is_pending_again(self) -> None:
        self.edit(self.failed, {"notes": "Fixed"})
        self.failed.refresh_from_db()
        self.assertEqual((self.failed.status, self.failed.errors), (ImportBatchRow.Status.PENDING, {}))

    def test_no_changes_are_reported(self) -> None:
        response = self.edit(self.pending, {})
        self.assertEqual(self.last_message(response), "1A had no changes.")

    def test_invalid_values_are_reported_by_field(self) -> None:
        response = self.edit(self.pending, {"ratio": "abc", "limited_to": "12x"})
        self.assertEqual(
            self.last_message(response),
            "Ratio: Ratio must be a valid relation Example: (1:100) Limited to: Not a valid number",
        )
        response = self.edit(self.pending, {"add_artists": ["999999"]})
        self.assertEqual(self.last_message(response), "Cover artists: Choose existing artists.")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.overrides, {})

    def test_a_row_with_its_edition_is_refused(self) -> None:
        response = self.edit(self.created, {"ratio": "1:50"})
        self.assertEqual(self.last_message(response), "The variant already has its edition.")

    def test_a_row_of_another_batch_is_404(self) -> None:
        other = make_batch(publishing=self.publishing)
        foreign = make_row(make_number(other), 999)
        for name in ("manage_import_batch_edit_row", "manage_import_batch_reset_row"):
            with self.subTest(action=name):
                self.assertEqual(self.post(name, {"ratio": "1:50"}, foreign.pk).status_code, 404)

    def test_resetting_a_row(self) -> None:
        ImportBatchRow.objects.filter(pk=self.pending.pk).update(overrides={"ratio": "1:50"})
        response = self.post("manage_import_batch_reset_row", {}, self.pending.pk)
        anchor = f"#import-row-{self.pending.pk}"
        self.assertRedirects(response, self.page_url() + anchor, fetch_redirect_response=False)
        self.assertEqual(self.last_message(response), "1A is back to the proposal.")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.overrides, {})


class BusyBatchTests(ManageImportsTestCase):
    """An action that waited for the batch lock longer than MySQL allows is answered, not a 500."""

    LOCK_WAIT = OperationalError(1205, "Lock wait timeout exceeded; try restarting transaction")

    def test_a_lock_wait_timeout_is_reported(self) -> None:
        group = make_number(self.batch)
        row = self.row(group, 101, 0, "A")
        attempts = [
            ("set_selection", "manage_import_batch_select", {"select": "none"}, ()),
            ("set_defaults", "manage_import_batch_defaults", {"rarity": "rare"}, ()),
            ("set_row_overrides", "manage_import_batch_edit_row", {"ratio": "1:50"}, (row.pk,)),
            ("create_rows", "manage_import_batch_create", {}, ()),
            ("close_batch", "manage_import_batch_close", {}, ()),
        ]
        for service, name, data, args in attempts:
            with self.subTest(action=name):
                with mock.patch(f"comics.views.manage_imports.{service}", side_effect=self.LOCK_WAIT):
                    response = self.post(name, data, *args)
                self.assertRedirects(response, self.page_url(), fetch_redirect_response=False)
                self.assertEqual(
                    self.last_message(response), "The batch is busy (it is creating editions): try again in a moment."
                )

    def test_other_database_errors_are_not_swallowed(self) -> None:
        for error in (OperationalError(2006, "MySQL server has gone away"), OperationalError("no code")):
            with self.subTest(error=error):
                with mock.patch("comics.views.manage_imports.close_batch", side_effect=error):
                    with self.assertRaises(OperationalError):
                        self.post("manage_import_batch_close")
