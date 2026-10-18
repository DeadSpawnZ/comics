"""What collectors own and follow: purchases and sales, their signatures and reading arcs."""

from __future__ import annotations

import datetime
from typing import Any

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import NON_FIELD_ERRORS
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from ..helper import parse_date_or_none, parse_id_or_none
from ..models import Collectable, Collection, Dealer, Edition, Publishing, ReadingArc, Signature
from .catalog import publishing_label, publishings_for_select, recent_publishings


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = [
            "collector",
            "publishing",
            "edition",
            "amount",
            "trade_date",
            "trade_type",
            "participant",
            "valuation",
            "signatures",
            "previous_trade",
        ]

    publishing = forms.ModelChoiceField(
        queryset=Publishing.objects.all().order_by("publishing_title", "date"),
        required=False,
        label="Publishing",
        help_text="Select a publishing to filter comics",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.filter_editions_by_publishing()
        self.fields["participant"].queryset = Dealer.objects.all().order_by("name")
        self.filter_previous_trade()

    def filter_editions_by_publishing(self) -> None:
        if "publishing" in self.data:
            try:
                publishing_id = int(self.data.get("publishing"))
                self.fields["edition"].queryset = Edition.objects.filter(publishing_id=publishing_id).order_by(
                    "number", "variant"
                )
            except (ValueError, TypeError):
                pass

        # EDIT: existing object
        elif self.instance.pk:
            publishing = self.instance.edition.publishing
            self.fields["edition"].queryset = Edition.objects.filter(publishing=publishing).order_by(
                "number", "variant"
            )
            self.initial["publishing"] = publishing

    def filter_previous_trade(self) -> None:
        # if not self.instance.pk:
        #     self.fields["previous_trade"].queryset = Edition.objects.none()
        #     return

        if self.instance.previous_trade:
            self.initial["previous_trade"] = self.instance.previous_trade

        if self.instance.edition_id:
            edition_id = self.instance.edition_id
            used_previous_trades_ids = (
                Collection.objects.filter(edition_id=edition_id)
                .exclude(previous_trade=None)
                .values_list("previous_trade_id", flat=True)
            )
            if self.instance.previous_trade and self.instance.previous_trade.id in used_previous_trades_ids:
                used_previous_trades_ids = [
                    uid for uid in used_previous_trades_ids if uid != self.instance.previous_trade.id
                ]
            qs = Collection.objects.filter(edition_id=edition_id, trade_type=Collection.TradeChoices.BUYING).exclude(
                id__in=used_previous_trades_ids
            )

            # A record cannot be its own previous_trade.
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            # You cannot sell something you did not own yet: only purchases made
            # on or before the sale date are offered.
            if self.instance.trade_date:
                qs = qs.filter(trade_date__lte=self.instance.trade_date)

            qs = qs.order_by(
                "edition__publishing__publishing_title",
                "edition__number",
                "edition__variant",
                "trade_date",
            )

            self.fields["previous_trade"].queryset = qs
            self.fields["previous_trade"].label_from_instance = lambda obj: (
                f"{obj.edition} || {obj.trade_date} || {getattr(obj.participant, 'name', None)}"
            )


class ReadingArcForm(forms.ModelForm):
    """Reading arc data for the Gestion module. The ordered issues are handled in the view."""

    class Meta:
        model = ReadingArc
        fields = ["name", "notes"]
        labels = {"name": _("Name"), "notes": _("Notes")}
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        error_messages = {"name": {"unique": _("A reading arc with that name already exists.")}}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control", "placeholder": field.label})
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def full_clean(self) -> None:
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()


def edition_label(edition: Edition) -> str:
    """Short edition label without extra queries (needs publishing selected)."""
    variant = f" {edition.variant_label}" if edition.variant_label else ""
    return f"#{edition.number}{variant} · {edition.get_printing_display()} · {edition.get_format_display()}"


def purchase_label(purchase: Collection | Collectable) -> str:
    """A purchase (of an edition or an article) as listed in the "purchase being sold" select."""
    participant = f" · {purchase.participant.name}" if purchase.participant_id else ""
    return f"{purchase.trade_date:%Y-%m-%d} · ${purchase.amount}{participant}"


def available_purchases(
    collector: User,
    edition_id: int,
    before: datetime.date | None = None,
    current: Collection | None = None,
) -> QuerySet[Collection]:
    """Purchases of `edition_id` that `collector` still owns (not sold yet) on or before `before`:
    the ones a sale can point to as its previous trade. `current` (the sale being edited) keeps
    the purchase it already points to."""
    purchases = Collection.objects.filter(edition_id=edition_id).sellable_by(collector, before, current)
    return purchases.select_related("participant")


class CurrentValuesMixin:
    """Read what a trade form currently holds (submitted data when bound, else the initial values),
    to narrow its selects before validation. Used by the piece forms of comics and collectables."""

    data: Any
    initial: dict[str, Any]
    instance: Any
    is_bound: bool

    def _current(self, name: str) -> Any:
        """Submitted value (bound form) or initial value of a field."""
        value = self.data.get(name) if self.is_bound else self.initial.get(name)
        return value or None

    def _current_id(self, name: str) -> int | None:
        """Current value of a choice field as an id; None if it is missing or not a valid id. A malformed
        submission then narrows nothing here, and the field validation reports it."""
        return parse_id_or_none(self._current(name))

    def _current_date(self, name: str) -> datetime.date | None:
        """Current value of a date field; None if it is missing or not a valid date."""
        value = self._current(name)
        if isinstance(value, datetime.date):
            return value
        return parse_date_or_none(str(value)) if value else None

    def _current_collector(self) -> User | None:
        value = self._current_id("collector") or self.instance.collector_id
        return User.objects.filter(pk=value).first() if value else None


class CollectionEntryForm(CurrentValuesMixin, forms.ModelForm):
    """A piece bought or sold by a collector. In Gestión the collector is chosen; in Mis comics
    it is the signed-in user (pass `collector`). The publishing field only narrows the edition list."""

    publishing = forms.ModelChoiceField(queryset=Publishing.objects.none(), required=False, label="Publishing")

    class Meta:
        model = Collection
        fields = [
            "collector",
            "edition",
            "trade_type",
            "trade_date",
            "amount",
            "participant",
            "valuation",
            "previous_trade",
            "notes",
        ]
        labels = {
            "collector": _("Collector"),
            "edition": _("Edition"),
            "trade_type": _("Type"),
            "trade_date": _("Date"),
            "amount": _("Amount (MXN)"),
            "participant": _("Participant"),
            "valuation": _("Valuation"),
            "previous_trade": _("Purchase being sold"),
            "notes": _("Notes"),
        }
        widgets = {
            "trade_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "trade_type": forms.RadioSelect,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _(
                    "That piece is already registered (same edition, date, amount, type and participant)."
                ),
            },
        }

    def __init__(self, *args: Any, collector: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fixed_collector = collector
        if collector is not None:
            del self.fields["collector"]
            self.instance.collector = collector
        else:
            self.fields["collector"].queryset = self.fields["collector"].queryset.order_by("username")

        edition = self.fields["edition"]
        edition.queryset = Edition.objects.select_related("publishing")
        edition.label_from_instance = edition_label
        self.fields["participant"].queryset = Dealer.objects.order_by("name")
        self.fields["participant"].widget.attrs["data-combobox"] = ""
        previous = self.fields["previous_trade"]
        previous.queryset = Collection.objects.filter(trade_type=Collection.TradeChoices.BUYING)
        if collector is not None:
            previous.queryset = previous.queryset.filter(collector=collector)
        previous.label_from_instance = purchase_label

        publishing = self.fields["publishing"]
        publishing.queryset = publishings_for_select()
        publishing.widget.attrs["data-combobox"] = ""
        publishing.widget.choices = [
            ("", "---------"),
            (_("Recent"), [(item.pk, publishing_label(item)) for item in recent_publishings()]),
            (_("All publishings"), [(item.pk, publishing_label(item)) for item in publishing.queryset]),
        ]

        # The edition and purchase selects only list the options of the current choice; the page
        # reloads them (API) when the publishing, edition or date change. Validation uses the full querysets.
        publishing_id = self._current_id("publishing") or (
            self.instance.edition.publishing_id if self.instance.edition_id else None
        )
        edition_id = self._current_id("edition") or self.instance.edition_id
        if publishing_id is None and edition_id:
            publishing_id = Edition.objects.filter(pk=edition_id).values_list("publishing_id", flat=True).first()
        if publishing_id:
            self.initial.setdefault("publishing", publishing_id)
        editions = (
            Edition.objects.filter(publishing_id=publishing_id).select_related("publishing") if publishing_id else []
        )
        editions = sorted(editions, key=lambda item: (_number_key(item.number), item.variant, item.printing))
        edition.widget.choices = [("", _("Choose a publishing first") if not publishing_id else "---------")] + [
            (item.pk, edition_label(item)) for item in editions
        ]
        owner = collector or self._current_collector()
        purchases = (
            available_purchases(owner, edition_id, self._current_date("trade_date"), self.instance)
            if owner and edition_id
            else []
        )
        self.fields["previous_trade"].widget.choices = [("", "---------")] + [
            (item.pk, purchase_label(item)) for item in purchases
        ]

        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.RadioSelect):
                continue  # rendered as a segmented button
            if isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        collector = self.fixed_collector or cleaned.get("collector")
        previous = cleaned.get("previous_trade")
        if cleaned.get("trade_type") != Collection.TradeChoices.SELLING:
            cleaned["previous_trade"] = None
        elif previous and collector and cleaned.get("edition"):
            allowed = available_purchases(collector, cleaned["edition"].pk, cleaned.get("trade_date"), self.instance)
            if not allowed.filter(pk=previous.pk).exists():
                self.add_error(
                    "previous_trade",
                    _(
                        "Choose a purchase of this edition by the same collector, "
                        "made on or before the sale date and not sold yet."
                    ),
                )
        return cleaned

    def save(self, commit: bool = True) -> Collection:
        if self.fixed_collector is not None:
            self.instance.collector = self.fixed_collector
        return super().save(commit)


def _number_key(number: str) -> tuple[int, int, str]:
    number = number.strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


class SignatureForm(forms.ModelForm):
    """One signature of a piece (artist, date, price, certificate)."""

    class Meta:
        model = Signature
        fields = ["artist", "date", "price", "has_coa"]
        labels = {
            "artist": _("Artist"),
            "date": _("Date"),
            "price": _("Price (MXN)"),
            "has_coa": _("COA"),
        }
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        artist = self.fields["artist"]
        artist.queryset = artist.queryset.order_by("name")
        artist.widget.attrs.update({"class": "form-select", "data-combobox": ""})
        self.fields["date"].widget.attrs.update({"class": "form-control", "placeholder": self.fields["date"].label})
        self.fields["price"].widget.attrs.update({"class": "form-control", "placeholder": self.fields["price"].label})
        self.fields["has_coa"].widget.attrs["class"] = "form-check-input"
        self.fields["has_coa"].help_text = _("Certificate of authenticity")


SignatureFormSet = forms.inlineformset_factory(
    Collection, Signature, form=SignatureForm, fk_name="collectable", extra=0, can_delete=True
)
