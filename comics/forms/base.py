"""Widgets, fields, mixins and helpers shared by the form modules of several areas."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseModelFormSet
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


class MaterialFieldsMixin:
    """Material/Bootstrap styling of a form's widgets (call style_fields() at the end of __init__,
    once the fields are final) and the `is-invalid` class on the fields with errors."""

    fields: dict[str, forms.Field]
    errors: dict[str, Any]

    def style_fields(self) -> None:
        # Floating labels need a placeholder.
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxSelectMultiple, forms.FileInput)):
                widget.attrs.setdefault(
                    "class", "form-control" if isinstance(widget, forms.FileInput) else "form-check-input"
                )
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})

    def full_clean(self) -> None:
        super().full_clean()  # type: ignore[misc]
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()


class RequiredHintsMixin:
    """Makes the required fields of a Gestión dialog stand out while they are empty: the widget gets
    aria-required and each field a short hint (`required_hint`, shown by
    manage/includes/field.html with an asterisk on the label, and kept in sync with the value by
    js/custom/publishing_dialogs.js). An empty field starts described by its hint. `required_hints`
    gives a field its own hint; the rest get a generic one. Call mark_required() at the end of
    __init__. The validation is the form's as usual: this is only how the page shows it."""

    required_hints: dict[str, str | Promise] = {}
    fields: dict[str, forms.Field]

    def mark_required(self) -> None:
        for name, field in self.fields.items():
            if not field.required:
                continue
            bound: forms.BoundField = self[name]  # type: ignore[index]  # the form's __getitem__
            if name in self.required_hints:
                hint = self.required_hints[name]
            elif isinstance(field.widget, forms.Select):
                hint = _("Required: choose an option.")
            else:
                hint = _("Required: fill in this field.")
            field.required_hint = hint  # type: ignore[attr-defined]
            field.widget.attrs["aria-required"] = "true"
            if bound.value() in (None, "", [], ()):
                field.widget.attrs["aria-describedby"] = f"{bound.auto_id}-hint"


class PlaceholderSelect(forms.Select):
    """A select whose empty option is a placeholder: shown while nothing is chosen, never choosable."""

    def create_option(
        self,
        name: str,
        value: Any,
        label: int | str,
        selected: bool,
        index: int,
        subindex: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value in ("", None):
            option["attrs"]["disabled"] = True
        return option


class UniqueNameMixin:
    """A unique `name`, compared ignoring case (MySQL's collation does, SQLite does not), so a
    duplicate is a form error on every database instead of an IntegrityError on one of them."""

    duplicate_message: str | Promise = ""
    _meta: Any
    instance: Any
    cleaned_data: dict[str, Any]

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        others = self._meta.model.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if name and others.exists():
            raise ValidationError(self.duplicate_message, code="unique")
        return name


def save_formset_reinserting(formset: BaseModelFormSet) -> None:
    """Save an inline whose rows have UniqueConstraints (position, order, etc.).
    Swapping values between rows violates the constraints if rows are updated one by
    one (MySQL checks every UPDATE and does not support deferred constraints), so the
    changed rows are deleted and inserted again with their final values. The formset
    already validated that the final state has no duplicates."""
    instances = formset.save(commit=False)
    for obj in formset.deleted_objects:
        obj.delete()
    formset.model.objects.filter(pk__in=[obj.pk for obj in instances if obj.pk]).delete()
    for obj in instances:
        obj.pk = None
        obj._state.adding = True
        obj.save()
    formset.save_m2m()


def _outlined_text_fields(form: forms.BaseForm) -> None:
    """Material/Bootstrap outlined fields: floating labels need a placeholder."""
    for field in form.fields.values():
        field.widget.attrs.update({"class": "form-control", "placeholder": field.label})


class LimitedImageField(forms.ImageField):
    """An uploaded picture with limits: checked by size before Pillow parses it, then by format
    and pixel count (a small file can still decode to a huge image). Only raster formats Pillow
    decodes can pass, so SVG (which can carry scripts) never does."""

    MAX_BYTES = 5 * 1024 * 1024
    MAX_PIXELS = 25_000_000
    FORMATS: tuple[str, ...] = ("JPEG", "PNG", "WEBP")
    FORMAT_ERROR = _("Upload a JPEG, PNG or WebP picture.")

    def to_python(self, data: Any) -> Any:
        if data and getattr(data, "size", 0) > self.MAX_BYTES:
            raise ValidationError(
                _("The picture is too large (maximum %(size)s MB).") % {"size": self.MAX_BYTES // (1024 * 1024)},
                code="too_large",
            )
        upload = super().to_python(data)
        if upload is not None:
            image = upload.image
            if image.format not in self.FORMATS:
                raise ValidationError(self.FORMAT_ERROR, code="invalid_format")
            if image.width * image.height > self.MAX_PIXELS:
                raise ValidationError(_("The picture has too many pixels."), code="too_many_pixels")
        return upload


def _material_widgets(form: forms.BaseForm, skip: Iterable[str] = ()) -> None:
    """Bootstrap/Material classes: switches, selects and outlined fields (with placeholders)."""
    for name, field in form.fields.items():
        widget = field.widget
        if name in skip:
            continue
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select):
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs.update({"class": "form-control", "placeholder": field.label})
