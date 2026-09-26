from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from .models import Collection, Edition, Publishing, Dealer


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

        # EDIT: objeto existente
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

            # Un registro no puede ser su propio previous_trade.
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            # No se puede vender algo que aun no se poseia: solo se ofrecen compras
            # ocurridas en la fecha de venta o antes.
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
    """Etiqueta corta sin consultas extra (Publishing.__str__ consulta sus editoriales)."""
    year = f" ({publishing.year})" if publishing.year else ""
    return f"{publishing.publishing_title}{year} {publishing.serie} · {publishing.language.upper()}"


class EditionManageForm(forms.ModelForm):
    """Datos de la edicion para Gestion. El contenido (issue o issues recopilados) se
    maneja aparte en la vista porque no son campos directos del modelo."""

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
            "image",
            "cover_artists",
            "notes",
        ]
        labels = {
            "publishing": "Publishing",
            "number": "Número",
            "variant": "Variante",
            "printing": "Impresión",
            "format": "Formato",
            "release_date": "Fecha de lanzamiento",
            "cover_price": "Precio de portada",
            "ratio": "Ratio",
            "limited_to": "Limitada a",
            "image": "Portada",
            "cover_artists": "Artistas de portada",
            "notes": "Notas",
        }
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "cover_artists": forms.CheckboxSelectMultiple,
            "image": forms.FileInput(attrs={"accept": "image/*"}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": "Ya existe una edición con ese publishing, número, variante e impresión.",
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        publishing = self.fields["publishing"]
        publishing.queryset = Publishing.objects.order_by("publishing_title", "year", "serie")
        publishing.label_from_instance = publishing_label
        self.fields["cover_artists"].queryset = self.fields["cover_artists"].queryset.order_by("name")

        # Estilo Material/Bootstrap: las etiquetas flotantes necesitan un placeholder.
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