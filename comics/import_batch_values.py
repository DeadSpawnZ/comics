"""Editing what an import batch (comics.models.ImportBatch) will create, before creating it: the
values every variant inherits (the batch defaults) and each variant's own changes (its overrides,
including its cover artists).

Both are validated with the edition form's own fields (comics.forms.BatchDefaultsForm and
BatchRowForm, built from EditionManageForm), so what is stored is what the edition form accepts,
in the shape it is posted. Only the rows without their edition yet (PENDING, FAILED) can change:
the values only matter when a row is created. Every change locks the batch first, then the row, as
the rest of the batch's changes do (comics.catalog_sources.batches.locked_open_batch). A top-level
module rather than part of comics.catalog_sources, so that package does not depend on comics.forms."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from comics.catalog_sources.batches import (
    COVER_ARTISTS,
    HAS_EDITION,
    BatchCredit,
    effective_cover_artists,
    locked_open_batch,
    needs_resolution,
)
from comics.forms import BatchDefaultsForm, BatchRowForm
from comics.helper import name_key
from comics.models import Artist, ImportBatch, ImportBatchRow

# The keys of a row's overrides BatchRowForm sets; any other key of them is kept as it is.
EDITED_KEYS = frozenset({*BatchRowForm.field_names, COVER_ARTISTS})
# BatchRowForm's cover artists fields: their errors are the edition's cover artists'.
ARTIST_FIELDS = ("drop_artists", "add_artists")


def set_defaults(batch: ImportBatch, values: Mapping[str, Any]) -> dict[str, str]:
    """Replace the defaults of the open `batch` (the values its rows inherit, see
    batches.effective_values) with `values`, the BatchDefaultsForm data: a field left empty is not
    set, so the rows inherit their proposal. Returns the defaults stored.

    When they change, the FAILED rows go back to PENDING without their errors, which were about the
    values before (the next creation tries them again anyway). Raises ValidationError, changing
    nothing, with the form's errors by field, or when the batch is closed."""
    form = BatchDefaultsForm(values)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    defaults = form.given_values()
    with transaction.atomic():
        locked = locked_open_batch(batch)
        if locked.defaults != defaults:
            locked.defaults = defaults
            locked.save(update_fields=["defaults", "updated_at"])
            locked.rows.filter(status=ImportBatchRow.Status.FAILED).update(
                status=ImportBatchRow.Status.PENDING, errors={}
            )
    batch.defaults, batch.updated_at = locked.defaults, locked.updated_at
    return defaults


def set_row_overrides(batch: ImportBatch, row: ImportBatchRow, values: Mapping[str, Any]) -> bool:
    """Replace the changes of `row`, a row of the open `batch` without its edition yet, with
    `values`, the BatchRowForm data. Returns whether its overrides changed.

    - Edition values: each one is inherited (left empty: the key is not stored), given, or, for an
      optional one, left blank on purpose (stored as ""), e.g. to accept no variant letter when
      Comi refused the catalogue's one. A required value is a value or inherited (BatchRowForm).
    - Cover artists, changed from the row's current list (batches.effective_cover_artists): the
      names in `drop_artists` are removed from this row only, and the artists `add_artists` are
      added as `chosen` credits (already resolved: the batch's name resolutions do not apply to
      them), replacing a credit with the same name. A list equal to the proposal's is not stored.
    Any other key of the overrides (e.g. the cover URL) is kept. When they change, a FAILED row goes
    back to PENDING without its errors, which were about the values before.

    Raises ValidationError, changing nothing, with the form's errors by edition field, or when an
    added artist does not exist, the batch is closed, the row is not one of its rows or already
    has its edition."""
    with transaction.atomic():
        locked_batch = locked_open_batch(batch)
        locked = _locked_row(locked_batch, row)
        current = effective_cover_artists(locked)
        form = BatchRowForm(values, cover_artists=[(credit["name"], credit["name"]) for credit in current])
        if not form.is_valid():
            raise ValidationError(_by_edition_field(form.errors.as_data()))
        added = _existing_artists(form.cleaned_data["add_artists"])
        overrides = {key: value for key, value in locked.overrides.items() if key not in EDITED_KEYS}
        overrides.update(form.value_overrides())
        cover = _edited_cover_artists(current, {name_key(name) for name in form.cleaned_data["drop_artists"]}, added)
        if cover != locked.proposal.get(COVER_ARTISTS, []):
            overrides[COVER_ARTISTS] = cover
        changed = _save_overrides(locked, overrides)
    row.overrides, row.status, row.errors = locked.overrides, locked.status, locked.errors
    return changed


def clear_row_overrides(batch: ImportBatch, row: ImportBatchRow) -> bool:
    """Drop every change of `row`, a row of the open `batch` without its edition yet: it goes back
    to the proposal and the batch defaults (as set_row_overrides, a FAILED row goes back to
    PENDING). Returns whether it had any. Raises ValidationError as set_row_overrides does."""
    with transaction.atomic():
        locked = _locked_row(locked_open_batch(batch), row)
        changed = _save_overrides(locked, {})
    row.overrides, row.status, row.errors = locked.overrides, locked.status, locked.errors
    return changed


def _locked_row(batch: ImportBatch, row: ImportBatchRow) -> ImportBatchRow:
    """`row` read again and locked, as a row of `batch` (already locked) without its edition yet;
    ValidationError otherwise."""
    locked = ImportBatchRow.objects.select_for_update().filter(pk=row.pk, batch=batch).first()
    if locked is None:
        raise ValidationError(_("The variant is not in this batch."))
    if locked.status in HAS_EDITION:
        raise ValidationError(_("The variant already has its edition."))
    locked.batch = batch
    return locked


def _save_overrides(row: ImportBatchRow, overrides: dict[str, Any]) -> bool:
    """Store `overrides` on `row` (locked) when they differ from its current ones; a FAILED row then
    goes back to PENDING without its errors. Returns whether they changed."""
    if overrides == row.overrides:
        return False
    row.overrides = overrides
    if row.status == ImportBatchRow.Status.FAILED:
        row.status, row.errors = ImportBatchRow.Status.PENDING, {}
    row.save(update_fields=["overrides", "status", "errors"])
    return True


def _by_edition_field(errors: dict[str, list[ValidationError]]) -> dict[str, list[ValidationError]]:
    """BatchRowForm's errors keyed by the edition form field they are about: its cover artists
    fields become "cover_artists"."""
    keyed: dict[str, list[ValidationError]] = {}
    for name, field_errors in errors.items():
        keyed.setdefault("cover_artists" if name in ARTIST_FIELDS else name, []).extend(field_errors)
    return keyed


def _existing_artists(ids: list[int]) -> list[Artist]:
    """The artists `ids`, in that order and each once (one query); ValidationError when one does not exist."""
    if not ids:
        return []
    found = Artist.objects.in_bulk(ids)
    if len(found) != len(set(ids)):
        raise ValidationError({"cover_artists": [_("Choose existing artists.")]})
    return [found[artist_id] for artist_id in dict.fromkeys(ids)]


def _edited_cover_artists(current: list[BatchCredit], dropped: set[str], added: list[Artist]) -> list[BatchCredit]:
    """`current` (a credit list) without the credits whose name key is in `dropped`, then the
    `added` artists as chosen credits. A kept credit with the name of an added artist, or that is
    certainly one of them, gives way to it, so the list names each artist once."""
    added_keys = {name_key(artist.name) for artist in added}
    added_ids = {artist.pk for artist in added}
    kept = [
        credit
        for credit in current
        if name_key(credit["name"]) not in dropped | added_keys
        and (needs_resolution(credit) or credit["artist_id"] not in added_ids)
    ]
    return kept + [
        {"name": artist.name, "artist_id": artist.pk, "uncertain": False, "chosen": True} for artist in added
    ]
