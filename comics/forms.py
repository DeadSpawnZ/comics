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
        if "publishing" not in self.data:
            return

        if self.instance.pk and self.instance.comic:
            publishing_instance = self.instance.comic.publishing
            self.fields["comic"].queryset = Comic.objects.filter(publishing=publishing_instance).order_by(
                "number", "variant"
            )
            self.initial["publishing"] = publishing_instance

    def filter_previous_trade(self):
        if "previous_trade" in self.data:
            return

        comic_instance = self.instance.comic
        qs = Collection.objects.filter(comic=comic_instance, trade_type=Collection.TradeChoices.BUYING).order_by(
            "comic__publishing__publishing_title",
            "comic__number",
            "comic__variant",
            "trade_date",
        )

        self.fields["previous_trade"].queryset = qs
        self.fields["previous_trade"].label_from_instance = (
            lambda obj: f"{obj.comic} || {obj.trade_date} || {obj.participant.name}"
        )
