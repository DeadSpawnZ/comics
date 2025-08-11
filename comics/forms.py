from django import forms
from .models import Collection, Comic, Publishing


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
        queryset=Publishing.objects.all(),
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
                self.fields["comic"].queryset = Comic.objects.filter(publishing_id=publishing_id)
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.comic:
            self.fields["comic"].queryset = Comic.objects.filter(publishing=self.instance.comic.publishing)
