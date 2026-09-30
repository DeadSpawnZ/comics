"""Collection.edition is mandatory (point 1)."""

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.urls import reverse

from comics.forms import CollectionEntryForm, CollectionForm
from comics.models import Collection

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_publishing, make_user


class CollectionEditionRequiredTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()
        self.edition = make_edition()

    def test_model_field_is_not_nullable(self) -> None:
        field = Collection._meta.get_field("edition")
        self.assertFalse(field.null)
        self.assertFalse(field.blank)

    def test_database_rejects_a_collection_without_edition(self) -> None:
        # bulk_create skips Collection.save(), so the NOT NULL constraint itself is exercised.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collection.objects.bulk_create(
                [Collection(collector=self.user, trade_date=datetime.date(2020, 1, 1), amount=Decimal("1"))]
            )

    def test_entry_form_requires_edition_without_forcing_it(self) -> None:
        # The model makes the form field required; the form no longer has to set it.
        form = CollectionEntryForm(
            data={"trade_type": "buying", "trade_date": "2020-01-01", "amount": "5", "valuation": "0"},
            collector=self.user,
        )
        self.assertTrue(form.fields["edition"].required)
        self.assertFalse(form.is_valid())
        self.assertIn("edition", form.errors)

    def test_admin_form_builds_for_a_new_collection(self) -> None:
        # A new Collection has no edition yet: the admin form must not touch the unset relation.
        form = CollectionForm()
        self.assertTrue(form.fields["edition"].required)
        self.assertNotIn("publishing", form.initial)

    def test_admin_form_edit_prefills_the_publishing_and_previous_trades(self) -> None:
        purchase = make_collection(collector=self.user, edition=self.edition)
        sale = make_collection(
            collector=self.user,
            edition=self.edition,
            trade_type=Collection.TradeChoices.SELLING,
            trade_date=datetime.date(2021, 1, 1),
            previous_trade=purchase,
        )
        form = CollectionForm(instance=sale)
        self.assertEqual(form.initial["publishing"], self.edition.publishing)
        self.assertEqual(list(form.fields["previous_trade"].queryset), [purchase])

    def test_collection_page_lists_pieces_and_their_sibling_editions(self) -> None:
        publishing = make_publishing(publishing_title="Spawn")
        own = make_edition(publishing=publishing, number="1", variant="A")
        sibling = make_edition(publishing=publishing, number="1", variant="B")
        make_collection(collector=self.user, edition=own)
        self.client.force_login(self.user)

        response = self.client.get(reverse("comics"), {"letter": ""})

        self.assertEqual(response.status_code, 200)
        piece = response.context["page_obj"].object_list[0]
        self.assertEqual(piece.sibling_editions, [sibling])
