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

        self.fields["comic"].queryset = Comic.objects.none()

        if "publishing" in self.data:
            try:
                publishing_id = int(self.data.get("publishing"))
                self.fields["comic"].queryset = Comic.objects.filter(publishing_id=publishing_id).order_by(
                    "number", "variant"
                )
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.comic:
            publishing_instance = self.instance.comic.publishing
            self.fields["comic"].queryset = Comic.objects.filter(publishing=publishing_instance).order_by(
                "number", "variant"
            )
            self.initial["publishing"] = publishing_instance

        self.fields["participant"].queryset = Dealer.objects.all().order_by("name")
