from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
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
    """Short label without extra queries (Publishing.__str__ queries its editorials)."""
    year = f" ({publishing.year})" if publishing.year else ""
    return f"{publishing.publishing_title}{year} {publishing.serie} · {publishing.language.upper()}"


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
        publishing.queryset = Publishing.objects.order_by("publishing_title", "year", "serie")
        publishing.label_from_instance = publishing_label
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
