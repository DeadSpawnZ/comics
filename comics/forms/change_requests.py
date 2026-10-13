"""Change requests: the forms collectors propose issues and editions with, and the reviewers' note."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

from ..change_requests import encode_value
from ..models import Issue
from .base import MaterialFieldsMixin
from .catalog import EditionManageForm, publishing_label, publishings_for_select

# ---------- Change requests ----------


class ProposalForm(forms.ModelForm):
    """Base of the forms collectors propose a new issue or edition with, or a correction to one
    (comics.change_requests). Bound to a copy of the row (or a new one) only to validate the values
    with the model's rules: it never saves it. Adds the request's reason and evidence link."""

    REQUEST_FIELDS = ("reason", "evidence_url")

    reason = forms.CharField(
        label=_("Why this change?"),
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Tell the reviewers what is wrong or missing, and where you checked it."),
    )
    evidence_url = forms.URLField(
        label=_("Evidence link (optional)"),
        max_length=500,
        required=False,
        assume_scheme="https",
        validators=[URLValidator(schemes=["http", "https"])],
        help_text=_("A page that confirms the data, e.g. the publisher's catalogue."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            # Fields with a callable default (release_date) would otherwise compare the submitted
            # value with an initial value the browser posts back; compare with the row's instead.
            field.show_hidden_initial = False

    def proposed_fields(self) -> list[str]:
        """The object's fields the request carries: every field for a new object, for a correction
        only the ones that differ from the current values."""
        names = [name for name in self.fields if name not in self.REQUEST_FIELDS]
        if self.instance.pk is None:
            return names
        return [name for name in names if name in self.changed_data]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if self.instance.pk is not None and not self.proposed_fields():
            raise ValidationError(_("Change at least one value: this is the current data."), code="no_changes")
        return cleaned

    def proposed_changes(self) -> dict[str, Any]:
        """The proposed values, as ChangeRequest.changes stores them (call after is_valid())."""
        model_meta = self._meta.model._meta
        return {
            name: encode_value(model_meta.get_field(name), self.cleaned_data[name]) for name in self.proposed_fields()
        }


class EditionProposalForm(ProposalForm, EditionManageForm):
    """An edition as collectors propose it: the Gestión edition form (same fields, labels and
    validation) without the cover upload, which v1 requests do not carry (an unreviewed file would
    have to be stored somewhere until the decision). Its content (own issue or collected issues)
    is not proposed either: a new edition gets its own issue, as in Gestión."""

    catalog_cover_url = None  # requests carry no cover (see above)

    class Meta(EditionManageForm.Meta):
        fields = [name for name in EditionManageForm.Meta.fields if name != "image"]


class IssueProposalForm(MaterialFieldsMixin, ProposalForm):
    """An issue as collectors propose it. A correction only covers its synopsis, creators and
    external catalogue ids: its series and number are what its editions follow, so those are
    corrected through an edition."""

    class Meta:
        model = Issue
        fields = ["publishing", "number", "synopsis", "creators", "metron_id", "comicvine_id", "gcd_id"]
        labels = {
            "publishing": "Publishing",
            "number": _("Number"),
            "synopsis": _("Synopsis"),
            "creators": _("Creators"),
            "metron_id": _("Metron ID (optional)"),
            "comicvine_id": _("Comic Vine ID (optional)"),
            "gcd_id": _("GCD ID (optional)"),
        }
        widgets = {
            "synopsis": forms.Textarea(attrs={"rows": 4}),
            "creators": forms.CheckboxSelectMultiple,
        }
        error_messages = {
            NON_FIELD_ERRORS: {"unique_together": _("An issue with that publishing and number already exists.")},
            "metron_id": {"unique": _("Another issue already has this Metron ID.")},
            "comicvine_id": {"unique": _("Another issue already has this Comic Vine ID.")},
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk is not None:
            del self.fields["publishing"], self.fields["number"]
        else:
            publishing = self.fields["publishing"]
            publishing.queryset = publishings_for_select()
            publishing.label_from_instance = publishing_label
            publishing.widget.attrs["data-combobox"] = ""
        self.fields["creators"].queryset = self.fields["creators"].queryset.order_by("name")
        self.style_fields()
        self.fields["synopsis"].widget.attrs["style"] = "height: 120px"
        self.fields["reason"].widget.attrs["style"] = "height: 90px"


class ReviewNoteForm(forms.Form):
    """The optional note a reviewer leaves with a step (validate, approve, reject); the author sees it."""

    note = forms.CharField(
        label=_("Note for the author (optional)"),
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )
