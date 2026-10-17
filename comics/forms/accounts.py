"""Accounts: sign in, self-registration and the profile pages."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from ..models import Profile, validate_phone
from .base import LimitedImageField, _outlined_text_fields

# ---------- Accounts: sign in, self-registration and profile ----------


DUPLICATE_EMAIL = _("A user with that email already exists.")


def unique_email(value: str, *, exclude: User | None = None) -> str:
    """Normalize an email (EmailField already stripped it; the domain part is lowercased) and check
    that no other user has it, ignoring case. The rule lives in forms and not in the database because
    existing accounts have no email (several empty values).

    Telling that an email is taken is unavoidable without sending emails (and the same holds for
    usernames); with SMTP this can become a notice sent to that address instead."""
    email = User.objects.normalize_email(value)
    others = User.objects.exclude(pk=exclude.pk) if exclude else User.objects.all()
    if others.filter(email__iexact=email).exists():
        raise ValidationError(DUPLICATE_EMAIL, code="duplicate_email")
    return email


class LoginForm(AuthenticationForm):
    """Django's sign-in form (inactive users and wrong passwords get the same message, so the form
    does not reveal which accounts exist), with the app's labels and message."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Your username and password didn't match."),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Username")
        self.fields["password"].label = _("Password")
        _outlined_text_fields(self)


class RegistrationForm(UserCreationForm):
    """Self-registration: username, email and password (checked by AUTH_PASSWORD_VALIDATORS).

    The email is required and unique ignoring case (see unique_email). Without SMTP there is no email
    verification yet: when there is, the account can be created inactive here and activated from
    the emailed link."""

    email = forms.EmailField(label=_("Email"), max_length=254)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Username")
        self.fields["password1"].label = _("Password")
        self.fields["password2"].label = _("Confirm password")
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        _outlined_text_fields(self)

    def clean_email(self) -> str:
        return unique_email(self.cleaned_data["email"])

    def save(self, commit: bool = True) -> User:
        # The user and its default role (added by the post_save signal) are saved together.
        with transaction.atomic():
            return super().save(commit=commit)


class PersonalInfoForm(forms.ModelForm):
    """Name, email and phone of the signed-in user (the phone lives on the profile)."""

    phone = forms.CharField(label=_("Phone"), required=False, max_length=30, validators=[validate_phone])

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args: Any, profile: Profile, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.fields["first_name"].label = _("First name")
        self.fields["last_name"].label = _("Last name")
        self.fields["email"].label = _("Email")
        self.fields["email"].required = True  # every account needs one since self-registration
        self.fields["phone"].initial = profile.phone
        self.fields["phone"].widget.attrs.update({"autocomplete": "tel", "inputmode": "tel"})
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        _outlined_text_fields(self)

    def clean_email(self) -> str:
        return unique_email(self.cleaned_data["email"], exclude=self.instance)

    def clean_phone(self) -> str:
        return " ".join(self.cleaned_data["phone"].split())

    def save(self, commit: bool = True) -> User:
        """Save the user and the profile's phone together. With commit=False nothing is written:
        both objects are only updated in memory (the caller saves them)."""
        self.profile.phone = self.cleaned_data["phone"]
        if not commit:
            return super().save(commit=False)
        with transaction.atomic():
            user = super().save()
            self.profile.save(update_fields=["phone"])
        return user


class AvatarField(LimitedImageField):
    FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
    FORMAT_ERROR = _("Upload a JPEG, PNG, WebP or GIF picture.")


class AvatarForm(forms.ModelForm):
    avatar = AvatarField(label=_("Profile picture"))

    class Meta:
        model = Profile
        fields = ("avatar",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        widget = self.fields["avatar"].widget
        widget.attrs.update({"class": "form-control", "accept": "image/jpeg,image/png,image/webp,image/gif"})


class PrivacyForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("is_public",)
        labels = {"is_public": _("Public profile")}
        widgets = {"is_public": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"})}


class ProfilePasswordForm(PasswordChangeForm):
    """Django's password change (current password + validated new one), with the app's labels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = _("Current password")
        self.fields["new_password1"].label = _("New password")
        self.fields["new_password2"].label = _("Confirm new password")
        _outlined_text_fields(self)
