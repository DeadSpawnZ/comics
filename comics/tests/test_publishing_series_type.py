"""Publishing.series_type: a collected line (TPB, hardcover...) coexists with the single issues of
the same title, year, series and language, while every periodical type stays on one line. Covers
the migration default, the line rule (the functional unique constraint itself, the save() guard,
the Gestión page, the "Create publishing" dialog and the admin), a missing year, the labels, and
the edition and piece duplicate checks, which now compare by id instead of by text."""

import datetime
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import StateApps
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import translation

from comics.models import Collection, Edition, Publishing, Title
from comics.roles import ADMINISTRATORS

from .base import TEST_MEDIA_ROOT, TEST_STORAGES, ComiTestCase
from .factories import (
    make_collection,
    make_dealer,
    make_edition,
    make_editorial,
    make_publishing,
    make_superuser,
    make_user,
)

SeriesType = Publishing.SeriesTypeChoices
LINE_TAKEN = "A publishing with that title, year, series and language already exists for this kind of series."
EDITION_TAKEN = "An edition with that publishing, number, variant, printing, event and store already exists."


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEST_MEDIA_ROOT, LANGUAGE_CODE="en")
class SeriesTypeMigrationTests(TransactionTestCase):
    """0124 forward on the test database: existing rows get the default and the constraint now
    includes the type. Migrating runs DDL, so this runs outside a transaction; the database is
    always left at the latest migration."""

    serialized_rollback = True
    before = [("comics", "0123_wanted_editions")]
    after = [("comics", "0124_publishing_series_type")]

    def migrate(self, targets: list[tuple[str, str]]) -> StateApps:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_rows_become_ongoing_and_a_collected_line_fits(self) -> None:
        old = self.migrate(self.before)
        old_publishing = old.get_model("comics", "Publishing")
        old_publishing.objects.create(publishing_title="Marvel vs. DC", year=1996, serie="1st", language="en")
        old_publishing.objects.create(publishing_title="Marvel vs. DC", year=1996, serie="1st", language="es")

        new = self.migrate(self.after)
        new_publishing = new.get_model("comics", "Publishing")
        self.assertEqual(set(new_publishing.objects.values_list("series_type", flat=True)), {"ongoing"})
        new_publishing.objects.create(
            publishing_title="Marvel vs. DC", year=1996, serie="1st", language="en", series_type="trade_paperback"
        )
        for series_type in ("ongoing", "limited"):
            with self.subTest(series_type=series_type), self.assertRaises(IntegrityError), transaction.atomic():
                new_publishing.objects.create(
                    publishing_title="Marvel vs. DC", year=1996, serie="1st", language="en", series_type=series_type
                )
        self.assertEqual(new_publishing.objects.count(), 3)


class LineRuleTests(ComiTestCase):
    """The rule on save (Publishing.validate_duplicates) and in the database itself."""

    def setUp(self) -> None:
        self.spawn = make_publishing(publishing_title="Spawn", year=1992)

    def publishing(self, series_type: str, **fields: Any) -> Publishing:
        values = {"publishing_title": "Spawn", "year": 1992, "series_type": series_type, **fields}
        return Publishing(**values)

    def test_the_default_is_ongoing(self) -> None:
        self.assertEqual(self.spawn.series_type, SeriesType.ONGOING)

    def test_periodical_types_share_one_line(self) -> None:
        for series_type in Publishing.PERIODICAL_TYPES:
            with self.subTest(series_type=series_type), self.assertRaisesMessage(ValidationError, LINE_TAKEN):
                self.publishing(series_type).save()
        self.assertEqual(Publishing.objects.count(), 1)

    def test_the_database_refuses_a_repeated_line(self) -> None:
        # bulk_create skips save(), so only the unique constraint stands in the way.
        for series_type in (SeriesType.LIMITED, SeriesType.ONGOING):
            with self.subTest(series_type=series_type), self.assertRaises(IntegrityError), transaction.atomic():
                Publishing.objects.bulk_create([self.publishing(series_type)])
        Publishing.objects.bulk_create([self.publishing(SeriesType.OMNIBUS)])
        self.assertEqual(Publishing.objects.count(), 2)

    def test_a_missing_year_repeats_like_any_other(self) -> None:
        Publishing.objects.filter(pk=self.spawn.pk).update(year=None)
        # Without a start date save() derives no year, so it is checked missing.
        with self.assertRaisesMessage(ValidationError, LINE_TAKEN):
            self.publishing(SeriesType.LIMITED, year=None, date=None).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Publishing.objects.bulk_create([self.publishing(SeriesType.LIMITED, year=None)])
        Publishing.objects.bulk_create([self.publishing(SeriesType.TRADE_PAPERBACK, year=None)])
        self.assertEqual(Publishing.objects.filter(year=None).count(), 2)

    def test_each_collected_type_is_its_own_line(self) -> None:
        self.publishing(SeriesType.TRADE_PAPERBACK).save()
        self.publishing(SeriesType.HARDCOVER).save()
        with self.assertRaisesMessage(ValidationError, LINE_TAKEN):
            self.publishing(SeriesType.TRADE_PAPERBACK).save()
        self.assertEqual(Publishing.objects.filter(publishing_title="Spawn").count(), 3)

    def test_another_language_is_another_line(self) -> None:
        self.publishing(SeriesType.LIMITED, language="es").save()
        self.assertEqual(Publishing.objects.count(), 2)

    def test_editing_the_type_onto_a_taken_line_is_refused(self) -> None:
        tpb = make_publishing(publishing_title="Spawn", year=1992, series_type=SeriesType.TRADE_PAPERBACK)
        tpb.series_type = SeriesType.LIMITED
        with self.assertRaisesMessage(ValidationError, LINE_TAKEN):
            tpb.save()
        tpb.series_type = SeriesType.HARDCOVER
        tpb.save()
        # Its own line does not count: the ongoing one may become limited.
        self.spawn.series_type = SeriesType.LIMITED
        self.spawn.save()
        self.assertEqual(
            dict(Publishing.objects.values_list("pk", "series_type")),
            {self.spawn.pk: SeriesType.LIMITED, tpb.pk: SeriesType.HARDCOVER},
        )

    def test_labels_name_the_type_unless_ongoing(self) -> None:
        editorials = [make_editorial(name="DC Comics")]  # one: the editorials have no fixed order
        single = make_publishing(publishing_title="Marvel vs. DC", year=1996, editorials=editorials)
        tpb = make_publishing(
            publishing_title="Marvel vs. DC", year=1996, series_type=SeriesType.TRADE_PAPERBACK, editorials=editorials
        )
        self.assertEqual(single.select_label, "Marvel vs. DC (1996) 1st · EN · DC Comics")
        self.assertEqual(tpb.select_label, "Marvel vs. DC (1996) 1st · EN · Trade paperback · DC Comics")
        self.assertEqual(str(single), "Marvel vs. DC (1996) 1st Series  [DC Comics]")
        self.assertEqual(str(tpb), "Marvel vs. DC (1996) 1st Series · Trade paperback  [DC Comics]")

    def test_type_labels_stay_in_english_in_every_language(self) -> None:
        tpb = make_publishing(publishing_title="Marvel vs. DC", year=1996, series_type=SeriesType.TRADE_PAPERBACK)
        with translation.override("es"):
            self.assertEqual(dict(SeriesType.choices)[SeriesType.ONE_SHOT], "One-shot")
            self.assertEqual(tpb.get_series_type_display(), "Trade paperback")
            self.assertEqual(tpb.series_type_suffix, " · Trade paperback")


class GestionLineRuleTests(ComiTestCase):
    """The same rule as a form error (the constraint, through the model form's validation) on the
    Gestión publishing page, the "Create publishing" dialog (JSON) and the admin."""

    def setUp(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        self.editorial = make_editorial(name="Image")
        self.spawn = make_publishing(publishing_title="Spawn", year=1992, editorials=[self.editorial])

    def page_data(self, **values: Any) -> dict[str, Any]:
        data = {
            "publishing_title": "Spawn",
            "serie": "1st",
            "language": "en",
            "series_type": SeriesType.ONGOING,
            "date": "1992-05-01",
            "year": "1992",
            "editorials": [self.editorial.pk],
        }
        data.update(values)
        return data

    def dialog_data(self, **values: Any) -> dict[str, Any]:
        data = {"title": Title.objects.get_or_create(name="Spawn")[0].pk, **self.page_data(**values)}
        return {f"new_publishing-{key}": value for key, value in data.items()}

    def test_the_page_and_the_dialog_render_the_field(self) -> None:
        page = self.client.get(reverse("manage_publishing_edit", args=[self.spawn.pk])).content.decode()
        self.assertIn('name="series_type"', page)
        self.assertIn('<option value="ongoing" selected>Ongoing series</option>', page)
        dialog = self.client.get(reverse("manage_edition_new")).content.decode()
        self.assertIn('name="new_publishing-series_type"', dialog)
        self.assertIn('<option value="trade_paperback">Trade paperback</option>', dialog)

    def test_creating_on_the_page(self) -> None:
        response = self.client.post(reverse("manage_publishing_new"), self.page_data(series_type=SeriesType.LIMITED))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].non_field_errors(), [LINE_TAKEN])
        response = self.client.post(reverse("manage_publishing_new"), self.page_data())
        self.assertEqual(response.context["form"].non_field_errors(), [LINE_TAKEN])  # the constraint, said once
        response = self.client.post(
            reverse("manage_publishing_new"), self.page_data(series_type=SeriesType.TRADE_PAPERBACK)
        )
        self.assertEqual(response.status_code, 302)
        tpb = Publishing.objects.exclude(pk=self.spawn.pk).get()
        self.assertEqual((tpb.publishing_title, tpb.series_type), ("Spawn", SeriesType.TRADE_PAPERBACK))

    def test_a_blank_year_is_checked_as_the_one_derived_from_the_date(self) -> None:
        response = self.client.post(
            reverse("manage_publishing_new"), self.page_data(year="", series_type=SeriesType.LIMITED)
        )
        self.assertEqual(response.context["form"].non_field_errors(), [LINE_TAKEN])
        self.assertEqual(Publishing.objects.count(), 1)

    def test_editing_the_type_on_the_page(self) -> None:
        tpb = make_publishing(publishing_title="Spawn", year=1992, series_type=SeriesType.TRADE_PAPERBACK)
        url = reverse("manage_publishing_edit", args=[tpb.pk])
        response = self.client.post(url, self.page_data(series_type=SeriesType.ANNUAL))
        self.assertEqual(response.context["form"].non_field_errors(), [LINE_TAKEN])
        tpb.refresh_from_db()
        self.assertEqual(tpb.series_type, SeriesType.TRADE_PAPERBACK)
        self.assertEqual(self.client.post(url, self.page_data(series_type=SeriesType.OMNIBUS)).status_code, 302)
        tpb.refresh_from_db()
        self.assertEqual(tpb.series_type, SeriesType.OMNIBUS)
        # The ongoing publishing may change type within its own line.
        url = reverse("manage_publishing_edit", args=[self.spawn.pk])
        self.assertEqual(self.client.post(url, self.page_data(series_type=SeriesType.LIMITED)).status_code, 302)
        self.spawn.refresh_from_db()
        self.assertEqual(self.spawn.series_type, SeriesType.LIMITED)

    def test_creating_in_the_dialog(self) -> None:
        url = reverse("manage_publishing_create_api")
        response = self.client.post(url, self.dialog_data(series_type=SeriesType.ONE_SHOT))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["non_field_errors"], [LINE_TAKEN])
        response = self.client.post(url, self.dialog_data(series_type=SeriesType.HARDCOVER))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "Spawn (1992) 1st · EN · Hardcover · Image")
        self.assertEqual(Publishing.objects.get(pk=response.json()["id"]).series_type, SeriesType.HARDCOVER)

    def test_the_admin_shows_the_error(self) -> None:
        self.client.force_login(make_superuser())
        url = reverse("admin:comics_publishing_add")
        data = {"title": Title.objects.create(name="Spawn").pk, **self.page_data(series_type=SeriesType.LIMITED)}
        for year in ("1992", ""):  # a blank year is the start date's, as on save
            with self.subTest(year=year):
                response = self.client.post(url, {**data, "year": year})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["adminform"].form.non_field_errors(), [LINE_TAKEN])
        self.assertEqual(Publishing.objects.count(), 1)
        response = self.client.post(url, {**data, "series_type": SeriesType.GRAPHIC_NOVEL})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Publishing.objects.count(), 2)


class EditionDuplicateTests(ComiTestCase):
    """Edition.validate_duplicate mirrors the unique constraint: the same slot of the same publishing."""

    def setUp(self) -> None:
        editorials = [make_editorial(name="DC Comics"), make_editorial(name="Marvel Comics")]
        self.single = make_publishing(publishing_title="Marvel vs. DC", year=1996, editorials=editorials)
        self.tpb = make_publishing(
            publishing_title="Marvel vs. DC", year=1996, series_type=SeriesType.TRADE_PAPERBACK, editorials=editorials
        )
        self.single_first = make_edition(publishing=self.single, number="1", variant="A")

    def test_a_collected_line_takes_the_same_slot(self) -> None:
        tpb_first = make_edition(publishing=self.tpb, number="1", variant="A")
        self.assertEqual(
            (tpb_first.number, tpb_first.variant, tpb_first.printing), ("1", "A", Edition.PrintingChoices.FIRST)
        )
        self.assertEqual(Edition.objects.filter(number="1").count(), 2)

    def test_publishings_differing_by_language_do_not_collide(self) -> None:
        spanish = make_publishing(publishing_title="Marvel vs. DC", year=1996, language="es")
        make_edition(publishing=spanish, number="1", variant="A")
        self.assertEqual(Edition.objects.filter(number="1").count(), 2)

    def test_the_same_slot_is_refused_on_create_and_when_moving_an_edition(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Duplicated comic"):
            make_edition(publishing=self.single, number="1", variant="a")  # the variant is uppercased
        moved = make_edition(publishing=self.tpb, number="1", variant="A")
        moved.publishing = self.single
        with self.assertRaisesMessage(ValidationError, "Duplicated comic"):
            moved.save()
        self.assertEqual(Edition.objects.get(pk=moved.pk).publishing_id, self.tpb.pk)
        # Saving an edition again does not find itself.
        self.single_first.notes = "Saved again"
        self.single_first.save()

    def test_moving_an_edition_onto_a_taken_slot_in_gestion_is_refused(self) -> None:
        moved = make_edition(publishing=self.tpb, number="1", variant="A")
        self.client.force_login(make_user(role=ADMINISTRATORS))
        data = {
            "publishing": self.single.pk,
            "number": "1",
            "variant": "A",
            "printing": moved.printing,
            "format": moved.format,
            "rarity": moved.rarity,
            "release_date": moved.release_date.isoformat(),
            "cover_price": "0",
        }
        response = self.client.post(reverse("manage_edition_edit", args=[moved.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].non_field_errors(), [EDITION_TAKEN])
        self.assertEqual(Edition.objects.get(pk=moved.pk).publishing_id, self.tpb.pk)


class CollectionDuplicateTests(ComiTestCase):
    """Collection.validate_duplicate compares the edition by id, not by its text."""

    def setUp(self) -> None:
        editorials = [make_editorial(name="DC Comics")]
        single = make_publishing(publishing_title="Marvel vs. DC", year=1996, editorials=editorials)
        tpb = make_publishing(
            publishing_title="Marvel vs. DC", year=1996, series_type=SeriesType.TRADE_PAPERBACK, editorials=editorials
        )
        self.single_first = make_edition(publishing=single, number="1", variant="A")
        self.tpb_first = make_edition(publishing=tpb, number="1", variant="A")
        self.collector = make_user()
        self.piece = {
            "collector": self.collector,
            "trade_date": datetime.date(2024, 5, 1),
            "amount": Decimal("10.00"),
            "participant": make_dealer(),
        }

    def test_the_same_piece_of_one_edition_is_refused(self) -> None:
        make_collection(edition=self.single_first, **self.piece)
        with self.assertRaisesMessage(ValidationError, "Duplicated collectable"):
            make_collection(edition=self.single_first, **self.piece)
        self.assertEqual(Collection.objects.count(), 1)

    def test_pieces_of_two_editions_that_read_the_same_are_allowed(self) -> None:
        self.assertEqual(str(self.single_first), str(self.tpb_first))
        make_collection(edition=self.single_first, **self.piece)
        make_collection(edition=self.tpb_first, **self.piece)
        self.assertEqual(Collection.objects.count(), 2)
