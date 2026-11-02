"""Saving an edition from the values of Gestión's edition form (EditionManageForm, plus the content
section and the issue section, IssueSectionForm): the same validation and saving for the Gestión
page (comics.views.manage_editions) and for code without a page of its own per edition, such as
creating editions from an import batch (create_edition)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms import BaseForm
from django.utils.translation import gettext as _

from comics.forms import EditionManageForm, IssueSectionForm
from comics.history import history_batch
from comics.models import CollectedIssue, Edition, Issue
from comics.versions import schedule_edition_owners

CONTENT_SINGLE = "single"
CONTENT_COMPILATION = "compilation"


@dataclass(frozen=True)
class EditionResult:
    """What create_edition did: the saved edition, or the errors that kept it from being saved."""

    edition: Edition | None = None
    # Field name (NON_FIELD_ERRORS "__all__" for the form-wide ones) -> messages, in the active
    # language. Plain strings, so the dict can be stored as JSON.
    errors: dict[str, list[str]] = field(default_factory=dict)
    # Whether the edition's own issue was created with it (False when it already existed).
    issue_created: bool = False

    @property
    def ok(self) -> bool:
        return self.edition is not None


def clean_content(data: Mapping[str, Any], content: str) -> tuple[int | None, list[int], list[str]]:
    """Validate the content section: single issue (or automatic) or list of collected issues.
    Returns (chosen issue id or None for the own issue, collected issue ids, error messages)."""
    if content == CONTENT_COMPILATION:
        collected_ids, errors = _clean_collected(data)
        return None, collected_ids, errors
    if content == CONTENT_SINGLE:
        issue_id, errors = _clean_single_issue(data)
        return issue_id, [], errors
    return None, [], [_("Choose whether the edition is a single issue or a compilation.")]


def _clean_collected(data: Mapping[str, Any]) -> tuple[list[int], list[str]]:
    """The collected issues of a compilation (`collected`: a JSON list of issue ids)."""
    errors = []
    try:
        collected_ids = [int(value) for value in json.loads(data.get("collected") or "[]")]
    except (TypeError, ValueError):
        errors.append(_("The list of collected issues is not valid."))
        collected_ids = []
    if not errors and not collected_ids:
        errors.append(_("A compilation needs at least one collected issue."))
    if len(collected_ids) != len(set(collected_ids)):
        errors.append(_("An issue is repeated in the compilation."))
    if collected_ids and Issue.objects.filter(pk__in=collected_ids).count() != len(set(collected_ids)):
        errors.append(_("One of the collected issues no longer exists."))
    return collected_ids, errors


def _clean_single_issue(data: Mapping[str, Any]) -> tuple[int | None, list[str]]:
    """The issue chosen for a single-issue edition (`issue`), or None for its own issue."""
    raw = data.get("issue") or ""
    if not raw:
        return None, []
    try:
        issue_id = int(raw)
    except ValueError:
        return None, [_("The chosen issue is not valid.")]
    if not Issue.objects.filter(pk=issue_id).exists():
        return issue_id, [_("The chosen issue no longer exists.")]
    return issue_id, []


@transaction.atomic
def save_edition(
    form: EditionManageForm,
    content: str,
    issue_id: int | None,
    collected_ids: list[int],
    issue_form: IssueSectionForm | None,
) -> Edition:
    """Save the edition (`form`, already valid), its content and, for a single-issue edition, the
    issue section (`issue_form`, already valid; None leaves the issue untouched) on the issue it
    ends up with. A ValidationError rolls everything back."""
    edition = form.save(commit=False)
    old_issue_id = (
        Edition.objects.filter(pk=edition.pk).values_list("issue_id", flat=True).first() if edition.pk else None
    )
    if content == CONTENT_COMPILATION:
        edition.save()
        form.save_m2m()
        edition.collected_entries.all().delete()
        CollectedIssue.objects.bulk_create(
            CollectedIssue(edition=edition, issue_id=issue, order=order)
            for order, issue in enumerate(collected_ids, start=1)
        )
        edition.sync_compilation_state()
    else:
        # Remove the collected issues first: otherwise save() still treats it as a compilation.
        if edition.pk:
            edition.collected_entries.all().delete()
        edition.issue_id = issue_id  # None = own issue (by publishing and number)
        edition.save()
        form.save_m2m()
        if edition.issue_id and issue_form is not None:
            issue_form.apply_to(edition.issue)
    # What an edition contains counts for issue-based achievements (multiverse, reading arcs,
    # rebuys). The collected issues are bulk-inserted (no signals), so the owners are scheduled here.
    schedule_edition_owners(edition, old_issue_id, contents_changed=content == CONTENT_COMPILATION)
    return edition


def create_edition(
    data: Mapping[str, Any],
    *,
    issue_data: Mapping[str, Any] | None,
    user: User | None,
    reason: str | None = None,
) -> EditionResult:
    """Create a single-issue edition on its own issue (its publishing and number) without a
    request, with the validation and saving of the Gestión edition form.

    `data` are EditionManageForm values as the form posts them (field names without prefix; a list
    of ids for `cover_artists`). A `catalog_cover_url` is downloaded during validation, as in the
    form. `issue_data` are IssueSectionForm values without its "issue_data-" prefix (e.g. `creators`
    as a list of ids, `synopsis`): validated, and written on the own issue only when this call
    creates it. An issue that already exists is never changed, nor is it when `issue_data` is None.

    Saving runs in its own transaction and history batch, so the edition, its issue and their
    versions (by `user`, with `reason`) are committed together; a nested batch writes its own rows
    even inside a request's (HistoryMiddleware). Invalid values, or a save that fails validation
    (e.g. an identifier of `issue_data` already used by another issue), write nothing and come
    back as `errors`. So does an IntegrityError while saving (another request saved the same
    edition or IVID in between): it is caught here, so every caller gets a result instead of
    an exception. Achievements are scheduled as after a Gestión save.

    It writes no admin LogEntry and no messages: that is the caller's, which has the request."""
    form = EditionManageForm(data)
    issue_form = IssueSectionForm(issue_data, instance=Issue(), prefix=None) if issue_data is not None else None
    # Both forms are validated, so every error is reported at once.
    edition_valid = form.is_valid()
    issue_valid = issue_form is None or issue_form.is_valid()
    if not (edition_valid and issue_valid):
        return EditionResult(errors=_form_errors(form, issue_form))

    publishing, number = form.cleaned_data["publishing"], form.cleaned_data["number"].strip()
    try:
        with transaction.atomic(), history_batch(user=user, reason=reason):
            # Not locked: Django runs MySQL at READ COMMITTED, where InnoDB takes no gap locks. If
            # another transaction creates the issue after this check, unique_issue_publishing_number
            # keeps it single: Edition.assign_default_issue (get_or_create) then picks that issue up,
            # and the issue section is written on it as if new (a known, narrow race; see BACKLOG).
            issue_exists = Issue.objects.filter(publishing=publishing, number=number).exists()
            edition = save_edition(form, CONTENT_SINGLE, None, [], None if issue_exists else issue_form)
    except ValidationError as exc:
        form.add_error(None, exc)
        return EditionResult(errors=_form_errors(form, issue_form))
    except IntegrityError:
        # The atomic block has rolled everything back, the versions included.
        form.add_error(None, _("The edition could not be saved: something it uses changed meanwhile. Try again."))
        return EditionResult(errors=_form_errors(form, issue_form))
    return EditionResult(edition=edition, issue_created=not issue_exists)


def _form_errors(*forms: BaseForm | None) -> dict[str, list[str]]:
    """The forms' errors as {field: [message, ...]}, in plain strings (the form-wide ones of every
    form together under NON_FIELD_ERRORS)."""
    errors: dict[str, list[str]] = {}
    for form in forms:
        if form is None:
            continue
        for name, messages in form.errors.items():
            errors.setdefault(name, []).extend(str(message) for message in messages)
    return errors
