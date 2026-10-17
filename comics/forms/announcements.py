"""Gestión announcements, the texts that notify every user or the members of some roles."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from ..models import Announcement
from ..roles import ROLE_PERMISSIONS, role_label
from .base import _material_widgets


class AnnouncementForm(forms.ModelForm):
    """An announcement's texts and link (what can be edited after publishing it)."""

    class Meta:
        model = Announcement
        fields = ("title_en", "title_es", "body_en", "body_es", "link")
        labels = {
            "title_en": _("Title (English)"),
            "title_es": _("Title (Spanish)"),
            "body_en": _("Message (English)"),
            "body_es": _("Message (Spanish)"),
            "link": _("Link (optional)"),
        }
        help_texts = {"link": _("A page of this site, e.g. /comics/. Opening the notification goes there.")}
        widgets = {
            "body_en": forms.Textarea(attrs={"rows": 4}),
            "body_es": forms.Textarea(attrs={"rows": 4}),
        }

    unstyled: tuple[str, ...] = ()  # fields that keep their own widget classes

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _material_widgets(self, skip=self.unstyled)


class RoleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj: Group) -> str:
        return role_label(obj.name)


class AnnouncementCreateForm(AnnouncementForm):
    """A new announcement, with its audience: everyone or the members of some roles. The audience
    is fixed once published (the notifications are created then)."""

    roles = RoleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        label=_("Roles"),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
    )

    class Meta(AnnouncementForm.Meta):
        fields = (*AnnouncementForm.Meta.fields, "audience", "roles")
        labels = {**AnnouncementForm.Meta.labels, "audience": _("Send to")}
        widgets = {**AnnouncementForm.Meta.widgets, "audience": forms.RadioSelect(attrs={"class": "form-check-input"})}

    unstyled = ("audience", "roles")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["roles"].queryset = Group.objects.filter(name__in=ROLE_PERMISSIONS).order_by("name")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if cleaned.get("audience") == Announcement.Audience.ROLES:
            if not cleaned.get("roles"):
                self.add_error("roles", _("Choose at least one role."))
        else:
            cleaned["roles"] = Group.objects.none()  # everyone: roles do not apply
        return cleaned
