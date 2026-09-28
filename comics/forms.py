from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import NON_FIELD_ERRORS
from django.db.models import prefetch_related_objects
from django.utils.translation import gettext_lazy as _
from .models import Collection, Edition, Editorial, Publishing, Dealer, ReadingArc, Title


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.filter_editions_by_publishing()
        self.fields["participant"].queryset = Dealer.objects.all().order_by("name")
        self.filter_previous_trade()

    def filter_editions_by_publishing(self):
        if "publishing" in self.data:
            try:
                publishing_id = int(self.data.get("publishing"))
                self.fields["edition"].queryset = Edition.objects.filter(
                    publishing_id=publishing_id
                ).order_by("number", "variant")
            except (ValueError, TypeError):
                pass

        # EDIT: existing object
        elif self.instance.pk and self.instance.edition:
            publishing = self.instance.edition.publishing
            self.fields["edition"].queryset = Edition.objects.filter(
                publishing=publishing
            ).order_by("number", "variant")
            self.initial["publishing"] = publishing

    def filter_previous_trade(self):
        # if not self.instance.pk:
        #     self.fields["previous_trade"].queryset = Edition.objects.none()
        #     return

        if self.instance.previous_trade:
            self.initial["previous_trade"] = self.instance.previous_trade

        if self.instance.edition:
            edition_id = self.instance.edition.id
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
            self.fields["previous_trade"].label_from_instance = (
                lambda obj: f"{obj.edition} || {obj.trade_date} || {getattr(obj.participant, 'name', None)}"
            )


class EditionForm(forms.ModelForm):
    class Meta:
        model = Edition
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["publishing"].queryset = Publishing.objects.order_by(
            "publishing_title",
            "year",
            "serie",
        )


def publishing_label(publishing):
    """Label for publishing selects: title (year) series · language · editorials. The editorials
    tell apart publishings with the same title; prefetch them (see publishing_choices) to avoid
    one query per option."""
    year = f" ({publishing.year})" if publishing.year else ""
    editorials = "/".join(editorial.name for editorial in publishing.editorials.all())
    label = f"{publishing.publishing_title}{year} {publishing.serie} · {publishing.language.upper()}"
    return f"{label} · {editorials}" if editorials else label


def publishings_for_select():
    return Publishing.objects.prefetch_related("editorials").order_by("publishing_title", "year", "serie")


def publishing_choices():
    """[(pk, label)] of every publishing, for selects."""
    return [(publishing.pk, publishing_label(publishing)) for publishing in publishings_for_select()]


RECENT_PUBLISHINGS = 8
NEWEST_PUBLISHINGS = 3


def recent_publishings():
    """Publishings most likely to get a new edition: the newest created ones (they are usually
    created right before adding their editions), then those of the most recently added editions."""
    recent = list(Publishing.objects.order_by("-id")[:NEWEST_PUBLISHINGS])
    seen = {publishing.pk for publishing in recent}
    for edition in Edition.objects.select_related("publishing").order_by("-id")[:200]:
        if len(recent) >= RECENT_PUBLISHINGS:
            break
        if edition.publishing_id not in seen:
            seen.add(edition.publishing_id)
            recent.append(edition.publishing)
    prefetch_related_objects(recent, "editorials")
    return recent


class EditionManageForm(forms.ModelForm):
    """Edition data for Gestion. The content (issue or collected issues) is
    handled separately in the view because they are not direct model fields."""

    class Meta:
        model = Edition
        fields = [
            "publishing",
            "number",
            "variant",
            "printing",
            "format",
            "release_date",
            "cover_price",
            "ratio",
            "limited_to",
            "retailer_exclusive",
            "image",
            "cover_artists",
            "notes",
        ]
        labels = {
            "publishing": "Publishing",
            "number": _("Number"),
            "variant": _("Variant"),
            "printing": _("Printing"),
            "format": _("Format"),
            "release_date": _("Release date"),
            "cover_price": _("Cover price"),
            "ratio": "Ratio",
            "limited_to": _("Limited to"),
            "retailer_exclusive": _("Retailer exclusive (store)"),
            "image": _("Cover"),
            "cover_artists": _("Cover artists"),
            "notes": _("Notes"),
        }
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "cover_artists": forms.CheckboxSelectMultiple,
            "image": forms.FileInput(attrs={"accept": "image/*"}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("An edition with that publishing, number, variant and printing already exists."),
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        publishing = self.fields["publishing"]
        publishing.queryset = publishings_for_select()
        publishing.label_from_instance = publishing_label
        publishing.widget.attrs["data-combobox"] = ""
        if not self.instance.pk:
            # New editions: offer the recent publishings first (they repeat in the full list).
            publishing.widget.choices = [
                ("", "---------"),
                (_("Recent"), [(item.pk, publishing_label(item)) for item in recent_publishings()]),
                (_("All publishings"), [(item.pk, publishing_label(item)) for item in publishing.queryset]),
            ]
        self.fields["cover_artists"].queryset = self.fields["cover_artists"].queryset.order_by("name")

        # Material/Bootstrap style: floating labels need a placeholder.
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxSelectMultiple, forms.FileInput)):
                widget.attrs.setdefault("class", "form-control" if isinstance(widget, forms.FileInput) else "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()

class ReadingArcForm(forms.ModelForm):
    """Reading arc data for the Gestion module. The ordered issues are handled in the view."""

    class Meta:
        model = ReadingArc
        fields = ["name", "notes"]
        labels = {"name": _("Name"), "notes": _("Notes")}
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        error_messages = {"name": {"unique": _("A reading arc with that name already exists.")}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control", "placeholder": field.label})
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()


class PublishingManageForm(forms.ModelForm):
    """Publishing data for Gestión. The title (the group used by the letter filter) is typed as
    text: an existing one is reused and a new one is created when needed."""

    title_name = forms.CharField(
        label=_("Title (group)"),
        max_length=100,
        required=False,
        help_text=_("Groups publishings under a letter. Empty: the publishing title is used."),
    )

    class Meta:
        model = Publishing
        fields = ["publishing_title", "serie", "language", "date", "year", "editorials"]
        labels = {
            "publishing_title": _("Publishing title"),
            "serie": _("Series"),
            "language": _("Language"),
            "date": _("Start date"),
            "year": _("Year"),
            "editorials": _("Editorials"),
        }
        help_texts = {"year": _("Empty: taken from the start date.")}
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "editorials": forms.CheckboxSelectMultiple,
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("A publishing with that title, year, series and language already exists."),
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.title_id:
            self.fields["title_name"].initial = self.instance.title.name
        self.fields["editorials"].queryset = Editorial.objects.order_by("name")
        self.fields["title_name"].widget.attrs["list"] = "title-options"
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxSelectMultiple):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})

    def clean(self):
        cleaned = super().clean()
        # Derive the year before the unique constraint is validated.
        if cleaned.get("publishing_title"):
            cleaned["publishing_title"] = cleaned["publishing_title"].strip()
        if cleaned.get("year") is None and cleaned.get("date"):
            cleaned["year"] = cleaned["date"].year
        return cleaned

    def save(self, commit=True):
        publishing = super().save(commit=False)
        publishing.year = self.cleaned_data.get("year")
        name = (self.cleaned_data.get("title_name") or "").strip() or publishing.publishing_title.strip()
        title = Title.objects.filter(name__iexact=name).first() or Title.objects.create(name=name)
        publishing.title = title
        if commit:
            publishing.save()
            self.save_m2m()
        return publishing


def edition_label(edition):
    """Short edition label without extra queries (needs publishing selected)."""
    variant = f" {edition.variant}" if edition.variant else ""
    return f"#{edition.number}{variant} · {edition.get_printing_display()} · {edition.get_format_display()}"


def purchase_label(purchase):
    participant = f" · {purchase.participant.name}" if purchase.participant_id else ""
    return f"{purchase.trade_date:%Y-%m-%d} · ${purchase.amount}{participant}"


def available_purchases(collector, edition_id, before=None, current=None):
    """Purchases of `edition_id` that `collector` still owns (not sold yet) on or before `before`:
    the ones a sale can point to as its previous trade. `current` (the sale being edited) keeps
    the purchase it already points to."""
    purchases = Collection.objects.owned_by(collector).filter(
        edition_id=edition_id, trade_type=Collection.TradeChoices.BUYING
    )
    if current is not None and current.previous_trade_id:
        purchases = purchases | Collection.objects.filter(pk=current.previous_trade_id)
    if before:
        purchases = purchases.filter(trade_date__lte=before)
    if current is not None and current.pk:
        purchases = purchases.exclude(pk=current.pk)
    return purchases.select_related("participant").order_by("trade_date", "id")


class CollectionEntryForm(forms.ModelForm):
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
                "unique_together": _("That piece is already registered (same edition, date, amount, type and participant)."),
            },
        }

    def __init__(self, *args, collector=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_collector = collector
        if collector is not None:
            del self.fields["collector"]
            self.instance.collector = collector
        else:
            self.fields["collector"].queryset = self.fields["collector"].queryset.order_by("username")

        edition = self.fields["edition"]
        edition.required = True
        edition.queryset = Edition.objects.select_related("publishing")
        edition.label_from_instance = edition_label
        self.fields["participant"].queryset = Dealer.objects.order_by("name")
        self.fields["participant"].widget.attrs["data-combobox"] = ""
        self.fields["previous_trade"].queryset = Collection.objects.filter(trade_type=Collection.TradeChoices.BUYING)
        self.fields["previous_trade"].label_from_instance = purchase_label

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
        publishing_id = self._current("publishing") or (self.instance.edition.publishing_id if self.instance.edition_id else None)
        edition_id = self._current("edition") or self.instance.edition_id
        if publishing_id is None and edition_id:
            publishing_id = Edition.objects.filter(pk=edition_id).values_list("publishing_id", flat=True).first()
        if publishing_id:
            self.initial.setdefault("publishing", publishing_id)
        editions = Edition.objects.filter(publishing_id=publishing_id).select_related("publishing") if publishing_id else []
        editions = sorted(editions, key=lambda item: (_number_key(item.number), item.variant, item.printing))
        edition.widget.choices = [("", _("Choose a publishing first") if not publishing_id else "---------")] + [
            (item.pk, edition_label(item)) for item in editions
        ]
        owner = collector or self._current_collector()
        purchases = available_purchases(owner, edition_id, self._current("trade_date"), self.instance) if owner and edition_id else []
        self.fields["previous_trade"].widget.choices = [("", "---------")] + [(item.pk, purchase_label(item)) for item in purchases]

        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.RadioSelect):
                continue  # rendered as a segmented button
            if isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def _current(self, name):
        """Submitted value (bound form) or initial value of a field."""
        value = self.data.get(name) if self.is_bound else self.initial.get(name)
        return value or None

    def _current_collector(self):
        value = self._current("collector") or self.instance.collector_id
        return User.objects.filter(pk=value).first() if value else None

    def clean(self):
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
                    _("Choose a purchase of this edition by the same collector, made on or before the sale date and not sold yet."),
                )
        return cleaned

    def save(self, commit=True):
        if self.fixed_collector is not None:
            self.instance.collector = self.fixed_collector
        return super().save(commit)


def _number_key(number):
    number = number.strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)
