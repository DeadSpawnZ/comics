from django import forms
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