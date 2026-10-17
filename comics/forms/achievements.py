"""Gestión achievements: achievements, their categories and their levels (tiers)."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.dates import MONTHS
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from ..achievements.catalogue import BUILTIN_CODES
from ..achievements.registry import Rule, generic_rules
from ..achievements.windows import ALL_WINDOW_KINDS, WindowKind, window_kinds_of
from ..models import Achievement, AchievementCategory, AchievementTier, unique_code
from .base import LimitedImageField, _material_widgets, _outlined_text_fields

# ---------- Gestión: achievements ----------


class AchievementIconField(LimitedImageField):
    MAX_BYTES = 2 * 1024 * 1024


def _icon_field(label: str | Promise) -> AchievementIconField:
    return AchievementIconField(
        label=label,
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp", "class": "form-control"}),
    )


WINDOW_FIELDS = (
    "calendar_window",
    "window_start_day",
    "window_start_month",
    "window_end_day",
    "window_end_month",
    "window_start_date",
    "window_end_date",
    "window_start_time",
    "window_end_time",
)


def _month_field(label: str | Promise) -> forms.TypedChoiceField:
    return forms.TypedChoiceField(
        label=label, choices=[("", "---------"), *MONTHS.items()], coerce=int, empty_value=None, required=False
    )


class AchievementForm(forms.ModelForm):
    """What Gestión edits of an achievement. The code and the rule are fixed once it exists; the
    time windows only appear for rules that read them (`rule.needs_window`), and such a rule
    needs at least one window. Window consistency is validated by Achievement.clean()."""

    icon_image = _icon_field(_("Icon image"))
    window_start_month = _month_field(_("Start month"))
    window_end_month = _month_field(_("End month"))

    class Meta:
        model = Achievement
        fields = (
            "name_en",
            "name_es",
            "description_en",
            "description_es",
            "hint_en",
            "hint_es",
            "category",
            "icon_image",
            "display_order",
            "is_secret",
            "is_enabled",
            *WINDOW_FIELDS,
        )
        labels = {
            "name_en": _("Name (English)"),
            "name_es": _("Name (Spanish)"),
            "description_en": _("Description (English)"),
            "description_es": _("Description (Spanish)"),
            "hint_en": _("Hint (English)"),
            "hint_es": _("Hint (Spanish)"),
            "category": _("Category"),
            "display_order": _("Order"),
            "is_secret": _("Secret"),
            "is_enabled": _("Enabled"),
            "calendar_window": _("Date window"),
            "window_start_day": _("Start day"),
            "window_end_day": _("End day"),
            "window_start_date": _("Start date"),
            "window_end_date": _("End date"),
            "window_start_time": _("From (time)"),
            "window_end_time": _("Until (time)"),
        }
        widgets = {
            "description_en": forms.Textarea(attrs={"rows": 2}),
            "description_es": forms.Textarea(attrs={"rows": 2}),
            "hint_en": forms.Textarea(attrs={"rows": 2}),
            "hint_es": forms.Textarea(attrs={"rows": 2}),
            "window_start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "window_end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "window_start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "window_end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args: Any, rule: Rule | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rule = rule
        if rule is None or not rule.needs_window:
            for name in WINDOW_FIELDS:
                del self.fields[name]
        _material_widgets(self, skip=("icon_image",))

    @property
    def has_windows(self) -> bool:
        return "calendar_window" in self.fields

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if self.has_windows and self.rule is not None and self.rule.needs_window:
            self._check_window_kinds(cleaned, self.rule.window_kinds)
        return cleaned

    def _check_window_kinds(self, cleaned: dict[str, Any], allowed: frozenset[WindowKind]) -> None:
        """The rule reads only some kinds of window: at least one of them is needed, and a kind it
        does not read would be misleading (it would be ignored)."""
        used = window_kinds_of(
            cleaned.get("calendar_window"), cleaned.get("window_start_time"), cleaned.get("window_end_time")
        )
        # Non-field errors: a field error would drop the field from cleaned_data and the model's
        # own window checks would then report a misleading half-filled window.
        unused = [_("This rule does not use %(kind)s.") % {"kind": kind.label} for kind in sorted(used - allowed)]
        if unused:
            raise ValidationError(unused, code="window_not_used")
        if not used & allowed:
            if allowed == ALL_WINDOW_KINDS:
                message = _("This rule only counts inside a time window: set a date window, a time window or both.")
            else:
                kinds = ", ".join(str(kind.label) for kind in sorted(allowed))
                message = _("This rule needs %(kinds)s.") % {"kinds": kinds}
            raise ValidationError(message, code="window_required")


class AchievementCreateForm(AchievementForm):
    """A new achievement made in Gestión: only with a generic rule (built-in rules have their own
    seeded row). Its code is generated from the name, unique and never reused from the catalogue."""

    rule = forms.ChoiceField(label=_("Rule"))

    class Meta(AchievementForm.Meta):
        fields = ("rule", *AchievementForm.Meta.fields)

    def __init__(self, *args: Any, rule: Rule | None = None, **kwargs: Any) -> None:
        super().__init__(*args, rule=rule, **kwargs)
        self.fields["rule"].choices = [
            (key, f"{key} · {item.measure}") for key, item in sorted(generic_rules().items())
        ]
        self.fields["rule"].widget.attrs["class"] = "form-select"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        self.assign_code()
        return cleaned

    def assign_code(self) -> None:
        """Give the new achievement a free code made from its name (again after a collision)."""
        name = self.cleaned_data.get("name_en") or self.cleaned_data.get("name_es")
        if name:
            taken = set(Achievement.objects.values_list("code", flat=True)) | BUILTIN_CODES
            self.instance.code = unique_code(name, taken)


class AchievementCategoryForm(forms.ModelForm):
    """A category of achievements. Its code is generated from the name when it is created."""

    image = _icon_field(_("Medallion image"))

    class Meta:
        model = AchievementCategory
        fields = (
            "name_en",
            "name_es",
            "description_en",
            "description_es",
            "display_order",
            "is_enabled",
            "image",
        )
        labels = {
            "name_en": _("Name (English)"),
            "name_es": _("Name (Spanish)"),
            "description_en": _("Description (English)"),
            "description_es": _("Description (Spanish)"),
            "display_order": _("Order"),
            "is_enabled": _("Enabled"),
        }
        widgets = {
            "description_en": forms.Textarea(attrs={"rows": 2}),
            "description_es": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _material_widgets(self, skip=("image",))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if self.instance.pk is None:
            self.assign_code()
        return cleaned

    def assign_code(self) -> None:
        """Give a new category a free code made from its name (again after a collision)."""
        name = self.cleaned_data.get("name_en") or self.cleaned_data.get("name_es")
        if name:
            taken = AchievementCategory.objects.values_list("code", flat=True)
            self.instance.code = unique_code(name, taken, max_length=30)


class AchievementTierForm(forms.ModelForm):
    class Meta:
        model = AchievementTier
        fields = ("level", "threshold", "label_en", "label_es")
        labels = {
            "level": _("Level"),
            "threshold": _("Threshold"),
            "label_en": _("Label (English)"),
            "label_es": _("Label (Spanish)"),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _outlined_text_fields(self)


class BaseAchievementTierFormSet(forms.BaseInlineFormSet):
    """Levels of an achievement. Count-based rules need at least one, numbered from 1 with no
    gaps and thresholds growing with the level; yes/no rules have a single implicit level."""

    def __init__(self, *args: Any, counted: bool, **kwargs: Any) -> None:
        self.counted = counted
        super().__init__(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if any(form.errors for form in self.forms):
            return
        kept = [form.cleaned_data for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if not self.counted:
            if kept:
                raise ValidationError(_("This achievement is earned or not: it has no levels."))
            return
        if not kept:
            raise ValidationError(_("Add at least one level."))
        kept.sort(key=lambda data: data["level"])
        if [data["level"] for data in kept] != list(range(1, len(kept) + 1)):
            raise ValidationError(_("Levels must be numbered 1, 2, 3… with no gaps or repeats."))
        thresholds = [data["threshold"] for data in kept]
        if any(lower >= higher for lower, higher in zip(thresholds, thresholds[1:], strict=False)):
            raise ValidationError(_("Each level needs a higher threshold than the previous one."))


AchievementTierFormSet = forms.inlineformset_factory(
    Achievement,
    AchievementTier,
    form=AchievementTierForm,
    formset=BaseAchievementTierFormSet,
    extra=1,
    can_delete=True,
)
