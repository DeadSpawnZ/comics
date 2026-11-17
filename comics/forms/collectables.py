"""Collectables: the article catalogue (Article, edited in Gestión) and a collector's purchases and
sales of its articles (Collectable)."""

from __future__ import annotations

import datetime
from typing import Any

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from ..models import Article, Collectable, Dealer
from .base import LimitedImageField, MaterialFieldsMixin
from .collections import CurrentValuesMixin, purchase_label

DUPLICATE_COLLECTABLE = _("That piece is already registered (same article, date, amount, type and participant).")


class ArticleManageForm(MaterialFieldsMixin, forms.ModelForm):
    """What Gestión edits of a catalogue article. The image is limited like other uploads
    (LimitedImageField) and converted to JPEG by Article.save."""

    image = LimitedImageField(
        label=_("Image"),
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
    )

    class Meta:
        model = Article
        fields = ["name", "category", "limited_to", "description", "image"]
        labels = {
            "name": _("Name"),
            "category": _("Category"),
            "limited_to": pgettext_lazy("article", "Limited to"),
            "description": _("Description"),
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.style_fields()
        self.fields["description"].widget.attrs["style"] = "height: 120px"


def article_choices() -> list[tuple[str, list[tuple[int, str]]]]:
    """The article catalogue for a select: grouped by category (in the order of CategoryChoices) and
    sorted by name, in one query."""
    grouped: dict[str, list[tuple[int, str]]] = {value: [] for value in Article.CategoryChoices.values}
    for article in Article.objects.order_by("name", "pk").only("id", "name", "category"):
        grouped.setdefault(article.category, []).append((article.pk, article.name))
    return [(label, grouped[value]) for value, label in Article.CategoryChoices.choices if grouped[value]]


def article_images() -> dict[str, str]:
    """{article id: image URL} of the articles with an image, for the preview of the article picker."""
    articles = Article.objects.exclude(image="").exclude(image__isnull=True).only("id", "image")
    return {str(article.pk): article.image.url for article in articles}


def available_collectable_purchases(
    collector: User,
    article_id: int,
    before: datetime.date | None = None,
    current: Collectable | None = None,
) -> QuerySet[Collectable]:
    """Purchases of `article_id` that `collector` still owns (not sold yet) on or before `before`: the
    ones a sale can point to as its previous trade. `current` (the sale being edited) keeps the
    purchase it already points to."""
    purchases = Collectable.objects.filter(article_id=article_id).sellable_by(collector, before, current)
    return purchases.select_related("participant")


class CollectableEntryForm(CurrentValuesMixin, forms.ModelForm):
    """A piece of an article bought or sold by a collector. In Gestión the collector is chosen; in
    Collectables it is the signed-in user (pass `collector`). Only existing articles can be picked.
    The previous trade rules live in Collectable.validate_previous_trade; this form adds the date rule
    and only lists the purchases a sale can point to."""

    class Meta:
        model = Collectable
        fields = [
            "collector",
            "article",
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
            "article": _("Article"),
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
        error_messages = {NON_FIELD_ERRORS: {"unique_together": DUPLICATE_COLLECTABLE}}

    def __init__(self, *args: Any, collector: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fixed_collector = collector
        if collector is not None:
            del self.fields["collector"]
            self.instance.collector = collector
        else:
            self.fields["collector"].queryset = User.objects.order_by("username")

        article = self.fields["article"]
        article.queryset = Article.objects.all()
        article.widget.attrs["data-combobox"] = ""
        article.widget.choices = [("", "---------"), *article_choices()]
        self.fields["participant"].queryset = Dealer.objects.order_by("name")
        self.fields["participant"].widget.attrs["data-combobox"] = ""

        # The purchase select only lists the options of the current article, collector and date; the
        # page reloads them (API) when those change. Validation uses the full queryset, limited to the
        # fixed collector's purchases so other users' purchase ids all get the same "invalid choice".
        previous = self.fields["previous_trade"]
        previous.queryset = Collectable.objects.filter(trade_type=Collectable.TradeChoices.BUYING)
        if collector is not None:
            previous.queryset = previous.queryset.filter(collector=collector)
        previous.label_from_instance = purchase_label
        owner = collector or self._current_collector()
        article_id = self._current_id("article") or self.instance.article_id
        purchases = (
            available_collectable_purchases(owner, article_id, self._current_date("trade_date"), self.instance)
            if owner and article_id
            else []
        )
        previous.widget.choices = [("", "---------")] + [(item.pk, purchase_label(item)) for item in purchases]

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
        previous = cleaned.get("previous_trade")
        sale_date = cleaned.get("trade_date")
        if cleaned.get("trade_type") != Collectable.TradeChoices.SELLING:
            cleaned["previous_trade"] = None
        elif previous and sale_date and previous.trade_date > sale_date:
            self.add_error("previous_trade", _("Choose a purchase made on or before the sale date."))
        return cleaned

    def validate_unique(self) -> None:
        super().validate_unique()
        if self.fixed_collector is None or self.errors:
            return
        # The collector is not a form field here, so the model validation skipped the unique
        # constraint that includes it; check it now instead of failing on save.
        try:
            self.instance.validate_constraints()
        except ValidationError:
            self.add_error(None, DUPLICATE_COLLECTABLE)
