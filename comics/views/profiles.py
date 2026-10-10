"""User profiles: the signed-in user's own page (/perfil/) and everyone's profile page (/u/<username>/).

Profiles are only for signed-in users. Email and phone are private: they appear on the owner's
own page and nowhere else."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods

from comics.achievements.display import showcase
from comics.forms import AvatarForm, PersonalInfoForm, PrivacyForm, ProfilePasswordForm
from comics.models import Profile

# Each section of the own profile page is a separate form, posted with its name in "section".
SUCCESS_MESSAGES = {
    "info": _("Personal information saved."),
    "avatar": _("Profile picture updated."),
    "privacy": _("Privacy settings saved."),
    "password": _("Password changed."),
}


def _section_forms(request: HttpRequest, profile: Profile, bound: str | None) -> dict[str, forms.BaseForm]:
    """Every section's form; only the posted one (`bound`) gets the submitted data."""
    user = request.user
    # A ModelForm writes the submitted values into its instance while validating: give the
    # personal info form its own copy, so a rejected POST does not show unsaved names in the
    # header and menu (which read request.user).
    info_user = User.objects.get(pk=user.pk)

    def data(section: str) -> dict[str, Any]:
        return {"data": request.POST, "files": request.FILES} if section == bound else {}

    return {
        "info": PersonalInfoForm(instance=info_user, profile=profile, **data("info")),
        "avatar": AvatarForm(instance=profile, **data("avatar")),
        "privacy": PrivacyForm(instance=profile, **data("privacy")),
        "password": ProfilePasswordForm(user, **data("password")),
    }


@login_required
@require_http_methods(["GET", "POST"])
def own_profile(request: HttpRequest) -> HttpResponse:
    profile = Profile.objects.for_user(request.user)
    section = request.POST.get("section") if request.method == "POST" else None

    if section == "remove_avatar":
        profile.remove_avatar()
        messages.success(request, _("Profile picture removed."))
        return redirect("profile")

    section_forms = _section_forms(request, profile, section if section in SUCCESS_MESSAGES else None)
    if section in SUCCESS_MESSAGES:
        form = section_forms[section]
        if form.is_valid():
            form.save()
            if section == "password":
                # Changing the password invalidates other sessions; keep this one signed in.
                update_session_auth_hash(request, form.user)
            messages.success(request, SUCCESS_MESSAGES[section])
            return redirect("profile")

    return render(
        request,
        "profiles/own.html",
        {
            "profile": profile,
            "forms": section_forms,
            "failed_section": section,
            # The owner also sees locked achievements and their progress.
            "achievements": showcase(request.user, include_locked=True),
        },
    )


@login_required
@require_GET
def user_profile(request: HttpRequest, username: str) -> HttpResponse:
    owner = get_object_or_404(User, username=username, is_active=True)
    profile = Profile.objects.for_user(owner)
    if not profile.is_public and owner != request.user:
        return render(request, "profiles/private.html", {"owner_username": owner.username})
    return render(
        request,
        "profiles/public.html",
        {
            # Only what a public profile shows: never the email or the phone.
            "owner": owner,
            "profile": profile,
            "is_own": owner == request.user,
            # Only earned ones, also when the owner previews their page as others see it.
            "achievements": showcase(owner, include_locked=False),
        },
    )
