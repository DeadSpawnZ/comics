from django import forms
from .models import Collection, Comic, Publishing, Dealer


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = [
            "collector",
            "publishing",
            "comic",
            "amount",
            "trade_date",
            "trade_type",
            "participant",
            "valuation",
            "signatures",
            "previous_trade",
        ]

    publishing = forms.ModelChoiceField(
        queryset=Publishing.objects.all().order_by("publishing_title"),
        required=False,
        label="Publishing",
        help_text="Select a publishing to filter comics",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.filter_comics_by_publishing()
        self.fields["participant"].queryset = Dealer.objects.all().order_by("name")
        self.filter_previous_trade()

    def filter_comics_by_publishing(self):
        if self.instance.pk and self.instance.comic:
            publishing_instance = self.instance.comic.publishing
            self.fields["comic"].queryset = Comic.objects.filter(publishing=publishing_instance).order_by(
                "number", "variant"
            )
            self.initial["publishing"] = publishing_instance

    def filter_previous_trade(self):
        # if not self.instance.pk:
        #     self.fields["previous_trade"].queryset = Comic.objects.none()
        #     return

        if self.instance.previous_trade:
            self.initial["previous_trade"] = self.instance.previous_trade

        if self.instance.comic:
            comic_id = self.instance.comic.id
            used_previous_trades_ids = (
                Collection.objects.filter(comic_id=comic_id)
                .exclude(previous_trade=None)
                .values_list("previous_trade_id", flat=True)
            )
            if self.instance.previous_trade and self.instance.previous_trade.id in used_previous_trades_ids:
                used_previous_trades_ids = [
                    uid for uid in used_previous_trades_ids if uid != self.instance.previous_trade.id
                ]
            qs = (
                Collection.objects.filter(comic_id=comic_id, trade_type=Collection.TradeChoices.BUYING)
                .exclude(id__in=used_previous_trades_ids)
                .order_by(
                    "comic__publishing__publishing_title",
                    "comic__number",
                    "comic__variant",
                    "trade_date",
                )
            )

            self.fields["previous_trade"].queryset = qs
            self.fields["previous_trade"].label_from_instance = (
                lambda obj: f"{obj.comic} || {obj.trade_date} || {obj.participant.name}"
            )
