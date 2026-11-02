"""Turning the rows of an import batch (comics.models.ImportBatch) into editions: creating the
selected ones through the Gestión edition form (comics.editions.create_edition), and linking a
probable row to an edition Comi already has (service.link_mycomicshop_edition).

Every row is created on its own: a row that fails keeps its errors and never stops the others, and
running it again only retries the rows still pending or failed."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from comics.editions import create_edition
from comics.helper import name_key
from comics.models import Artist, Edition, ImportBatch, ImportBatchNumber, ImportBatchRow

from .batches import (
    REJECTED,
    BatchCredit,
    NameAction,
    ResolvedCredits,
    effective_cover_artists,
    effective_cover_url,
    effective_values,
    locked_open_batch,
    resolved_credits,
)
from .service import fill_publishing_series_id, link_mycomicshop_edition

# The statuses of a row create_rows (re)tries; CREATED and LINKED rows already have their edition.
RETRIED = (ImportBatchRow.Status.PENDING, ImportBatchRow.Status.FAILED)
# Required edition form fields the Gestión page preselects for a new edition: a row whose source
# says nothing about one gets the same value. The release date and the cover price are never
# defaulted (facts of the edition). A field whose source value Comi refused (the proposal's
# `rejected`: a 7th printing, a series type without format, a variant too long...) is never
# defaulted nor sent empty, which would save a wrong edition (e.g. the main cover "A"): the row
# fails on it until the batch defaults or the row's overrides give it (see row_form_values).
PRESELECTED_FIELDS = ("printing", "format", "rarity")


@dataclass
class BatchCreation:
    """What create_rows did, for the caller to report and log (it has the request)."""

    created: list[ImportBatchRow]  # rows now CREATED, with their edition
    failed: list[ImportBatchRow]  # rows now FAILED, with their errors
    artists: list[Artist]  # artists created for the batch's "new" resolutions (not the reused ones)
    publishing_id: int  # the publishing the editions went to (the batch's, read once it was locked)
    series_id_filled: bool  # whether that publishing got the batch's TID (Publishing has no history)
    # The batch was closed or given another publishing during the run: the rows left were not tried.
    interrupted: bool = False


class _BatchChanged(Exception):
    """The batch is no longer open with the publishing a create_rows run started with."""


@dataclass(frozen=True)
class RowLink:
    """What link_row did."""

    row: ImportBatchRow  # now LINKED to its edition
    changed: bool  # whether the edition got the IVID (False: it already had it)
    series_id_filled: bool  # whether the edition's publishing got the batch's TID


def create_rows(
    batch: ImportBatch,
    *,
    user: User | None,
    reason: str | None = None,
    on_artist_created: Callable[[Artist], None] | None = None,
    on_row_created: Callable[[ImportBatchRow], None] | None = None,
    on_series_id_filled: Callable[[int], None] | None = None,
) -> BatchCreation:
    """Create the editions of the selected rows of the open `batch` that are pending or failed, in
    position order, in the batch's publishing; their versions are written by `user` with `reason`
    (by default "Imported from MyComicShop (batch #N).").

    `on_artist_created(artist)`, `on_row_created(row)` (CREATED, with its edition) and
    `on_series_id_filled(publishing_id)` (the publishing got the batch's TID) are called inside the
    transaction that saves what they report, so whatever they record (e.g. a LogEntry) is saved or
    undone with it.

    First the artists of the "new" resolutions those rows use are created, once, in their own
    transaction (an artist already named so is reused, see Artist.objects.ensure_named), and their
    resolutions become that artist, so a later run finds them as any other artist. Then each row
    is created on its own (create_rows never raises for a row):
    - a row whose cover artists, or, for a new issue, whose number's story credits, still have
      names without a resolution fails with them listed, without trying to save it; so does a row
      with a field whose catalogue value Comi refused and no layer gives (see PRESELECTED_FIELDS);
    - its values are row_form_values(row) plus the batch's publishing, its number, its IVID, its
      cover (downloaded while it is validated) and its resolved cover artists. A number whose issue
      already existed when the batch was built never changes that issue's story credits; a new one
      gets the resolved story credits, written only by the row that creates the issue, which
      then becomes the number's issue (its other rows no longer need story resolutions);
    - CREATED with its edition, or FAILED with the form's errors (plain JSON), saved with the
      attempt, in one transaction per row. Each row's transaction first locks the batch, reads its
      name resolutions and defaults again (a change made during the run applies to the rows still
      to try) and checks that it is still open with the publishing the run started with: a change
      of the batch made meanwhile (set_publishing, close_batch) stops the run there, the rows left
      untried (`interrupted`), and one asked for while a row is tried waits for it and then sees it
      CREATED (set_publishing then refuses). The batch stays locked during the row's cover
      download (seconds): acceptable for a local app, and two runs at once take turns per row. A
      change that waits longer than MySQL's innodb_lock_wait_timeout fails with its lock-wait
      error (the batch views answer it as "the batch is busy").
      Then the row is locked and checked again, so two runs cannot both create it.
    When anything was created, the batch's TID is stored on the publishing if it has none yet. The
    batch stays open (closing it is a separate action).

    Raises ValidationError, touching no row, when the batch is closed or has no publishing, or when
    the new artists cannot be created. Any other exception (a bug, the database going away) is not
    caught: the rows tried before it keep their saved status."""
    current, artists = _create_new_artists(batch, on_artist_created)
    batch.name_resolutions, batch.updated_at = current.name_resolutions, current.updated_at
    pending = current.rows.filter(selected=True, status__in=RETRIED).order_by("position").values_list("pk", flat=True)
    if reason is None:
        reason = _("Imported from MyComicShop (batch #%(batch)s).") % {"batch": current.pk}
    publishing_id = cast(int, current.publishing_id)  # checked by _create_new_artists
    result = BatchCreation(
        created=[], failed=[], artists=artists, publishing_id=publishing_id, series_id_filled=False
    )
    for row_id in list(pending):
        try:
            row = _create_row(current, row_id, user=user, reason=reason, on_created=on_row_created)
        except _BatchChanged:
            result.interrupted = True
            break
        if row is None:
            continue  # another run took it meanwhile
        (result.created if row.status == ImportBatchRow.Status.CREATED else result.failed).append(row)
    if result.created:
        with transaction.atomic():
            result.series_id_filled = fill_publishing_series_id(publishing_id, current.series_id)
            if result.series_id_filled and on_series_id_filled is not None:
                on_series_id_filled(publishing_id)
    return result


def _create_new_artists(
    batch: ImportBatch, on_created: Callable[[Artist], None] | None
) -> tuple[ImportBatch, list[Artist]]:
    """(the batch read again, the artists created): create the artists of the "new" resolutions
    used by the rows create_rows will try (their cover artists and their new numbers' story
    credits) and store those resolutions as their artist, after checking that the batch is open
    and has a publishing; `on_created` is called for each artist created, in that transaction. An
    artist created by another request meanwhile (Artist.name is unique) makes the attempt fail: it
    is retried once, in a new transaction, which finds it."""
    with suppress(IntegrityError), transaction.atomic():
        return _create_new_artists_once(batch, on_created)
    try:
        with transaction.atomic():
            return _create_new_artists_once(batch, on_created)
    except IntegrityError:
        raise ValidationError(_("The artists could not be created. Try again.")) from None


def _create_new_artists_once(
    batch: ImportBatch, on_created: Callable[[Artist], None] | None
) -> tuple[ImportBatch, list[Artist]]:
    locked = locked_open_batch(batch)
    if locked.publishing_id is None:
        raise ValidationError(_("Choose the publishing of the batch first."))
    rows = list(locked.rows.filter(selected=True, status__in=RETRIED).select_related("group"))
    credits: list[BatchCredit] = []
    for row in rows:
        credits.extend(effective_cover_artists(row))
    for group in {row.group_id: row.group for row in rows}.values():
        if group.issue_id is None:
            credits.extend(group.creators)
    names = resolved_credits(locked, credits).new_names
    if not names:
        return locked, []
    ensured = Artist.objects.ensure_named(names)
    by_key = {name_key(name): artist for name, (artist, _created) in zip(names, ensured, strict=True)}
    for key, resolution in locked.name_resolutions.items():
        if resolution["action"] != NameAction.NEW:
            continue
        artist = by_key.get(name_key(resolution.get("new_name") or resolution["name"]))
        if artist is not None:
            locked.name_resolutions[key] = {
                "name": resolution["name"],
                "action": NameAction.ARTIST.value,
                "artist_id": artist.pk,
            }
    locked.save(update_fields=["name_resolutions", "updated_at"])
    created_artists = [artist for artist, created in ensured if created]
    if on_created is not None:
        for artist in created_artists:
            on_created(artist)
    return locked, created_artists


def _create_row(
    batch: ImportBatch,
    row_id: int,
    *,
    user: User | None,
    reason: str,
    on_created: Callable[[ImportBatchRow], None] | None = None,
) -> ImportBatchRow | None:
    """Try the row `row_id` of `batch` (as create_rows read it once locked; see create_rows) and save
    how it went, calling `on_created` in the same transaction when its edition was created; None
    when it is no longer selected, pending or failed (another run took it). Raises _BatchChanged,
    trying nothing, when the batch is no longer open with the publishing `batch` has.

    The batch is read again under its lock, and the row uses that reading: a name resolution or a
    batch default changed during the run applies to the rows still to try. The batch is locked
    before the row, in the order every change of a batch takes them (locked_open_batch, then its
    rows), so they cannot deadlock. As every change of a row goes through the batch lock, the
    row's `skip_locked` is only a defensive guard: no other transaction should hold the row once
    the batch lock is taken."""
    with transaction.atomic():
        locked = (
            ImportBatch.objects.select_for_update()
            .filter(pk=batch.pk, status=ImportBatch.Status.OPEN, publishing_id=batch.publishing_id)
            .first()
        )
        if locked is None:
            raise _BatchChanged
        row = (
            ImportBatchRow.objects.select_for_update(skip_locked=True)
            .filter(pk=row_id, selected=True, status__in=RETRIED)
            .first()
        )
        if row is None:
            return None
        batch = locked
        row.batch = batch
        # Read under the batch lock: an earlier row of this run (or another run) may have set its issue.
        group = ImportBatchNumber.objects.get(pk=row.group_id)
        cover = resolved_credits(batch, effective_cover_artists(row))
        story = resolved_credits(batch, group.creators) if group.issue_id is None else None
        values = row_form_values(row)
        errors = _rejected_errors(values) | _unresolved_errors(cover, story)
        if errors:
            row.status, row.edition, row.errors = ImportBatchRow.Status.FAILED, None, errors
        else:
            issue_data = None if story is None else {"creators": story.artist_ids}
            data = _edition_data(batch, group, row, values, cover)
            created = create_edition(data, issue_data=issue_data, user=user, reason=reason)
            if created.ok:
                row.status, row.edition, row.errors = ImportBatchRow.Status.CREATED, created.edition, {}
                if created.issue_created:
                    # The number's issue exists now: its other rows (this run or a later one) save
                    # no story credits, so they need no resolutions for them either.
                    group.issue = created.edition.issue
                    group.save(update_fields=["issue"])
            else:
                row.status, row.edition, row.errors = ImportBatchRow.Status.FAILED, None, created.errors
        row.save(update_fields=["status", "edition", "errors"])
        if on_created is not None and row.status == ImportBatchRow.Status.CREATED:
            on_created(row)
    return row


@dataclass(frozen=True)
class RowFormValues:
    """The edition form values a row is created with (see row_form_values)."""

    values: dict[str, Any]  # effective_values, plus the preselected defaults
    preselected: list[str]  # the PRESELECTED_FIELDS filled with their default (no layer gave them)
    refused: list[str]  # catalogue values Comi refused that no layer replaces: the row fails on them

    @property
    def to_fill(self) -> list[str]:
        """The fields the row cannot be created without: the refused ones, and the preselected ones
        left empty (a refused value replaced by an empty one is never defaulted)."""
        # A PRESELECTED_FIELDS field still empty here is one row_form_values did not default: the
        # catalogue's value was refused and a layer replaced it with "" (refused fields replaced by
        # a value are filled, unreplaced ones are in `refused`). This relies on the preselected
        # defaults never being empty, so a defaulted field is never mistaken for one of these.
        empty = [name for name in PRESELECTED_FIELDS if not self.values.get(name) and name not in self.refused]
        return self.refused + empty


def row_form_values(row: ImportBatchRow, *, overrides: bool = True) -> RowFormValues:
    """The edition form values of `row` as create_rows sends them (before the publishing, number,
    IVID, cover and cover artists): effective_values(row), where a PRESELECTED_FIELDS field no layer
    fills gets the value the Gestión page preselects, unless the catalogue gave a value Comi refused
    (proposal["rejected"]), which is never defaulted. A refused field neither the batch defaults nor
    the row's overrides give is `refused`; a key in one of those layers counts even when empty: the
    user chose it (an empty required field still fails in the form). With `overrides=False`, the
    values the row inherits, as if it had no overrides. Reads `row.batch`; no queries."""
    values = effective_values(row, overrides=overrides)
    rejected = row.proposal.get(REJECTED, [])
    given = row.batch.defaults.keys() | (row.overrides.keys() if overrides else set())
    preselected = []
    for name in PRESELECTED_FIELDS:
        if not values.get(name) and name not in rejected:
            values[name] = str(Edition._meta.get_field(name).default)
            preselected.append(name)
    refused = [name for name in rejected if name not in given]
    return RowFormValues(values=values, preselected=preselected, refused=refused)


def _rejected_errors(values: RowFormValues) -> dict[str, list[str]]:
    """{field: [message]} for the refused fields of a row (see row_form_values); {} when none."""
    message = _("Comi did not accept the catalogue's value: fill it in yourself.")
    return {name: [message] for name in values.refused}


def _unresolved_errors(cover: ResolvedCredits, story: ResolvedCredits | None) -> dict[str, list[str]]:
    """{field: [message]} for the credit names still without a resolution, by the form field they
    would be saved in (the edition's cover artists, the issue section's creators); {} when none.
    A "new" name is unresolved here too: its artist should exist by now (create_rows makes them)."""
    errors: dict[str, list[str]] = {}
    for field, credits in (("cover_artists", cover), ("creators", story)):
        if credits is None:
            continue
        names = credits.unresolved + credits.new_names
        if names:
            errors[field] = [_("Resolve these names first: %(names)s.") % {"names": ", ".join(names)}]
    return errors


def _edition_data(
    batch: ImportBatch, group: ImportBatchNumber, row: ImportBatchRow, values: RowFormValues, cover: ResolvedCredits
) -> dict[str, Any]:
    """The EditionManageForm values of `row` (`values`, its row_form_values), as the form posts them
    (ids for the publishing, a list of ids for the cover artists)."""
    data = dict(values.values)
    data.update(
        publishing=batch.publishing_id,
        number=group.number,
        mycomicshop_id=row.ivid,
        cover_artists=cover.artist_ids,
    )
    cover_url = effective_cover_url(row)
    if cover_url:
        data["catalog_cover_url"] = cover_url
    return data


def link_row(batch: ImportBatch, row: ImportBatchRow, edition_id: int, *, user: User) -> RowLink:
    """Link the row `row` of the open `batch` to `edition_id`, one of the editions it probably is
    (row.probable_editions), instead of creating an edition: the edition gets the row's IVID and its
    publishing the batch's TID when it has none (service.link_mycomicshop_edition), and the row is
    LINKED to it. The row and the edition change together, in one transaction.

    Raises ValidationError, changing nothing, when the batch is closed, the row is not one of its
    rows or already has its edition (CREATED or LINKED), or the edition is not one of its probable
    ones. The CatalogError of link_mycomicshop_edition (NotFound, Conflict) is raised as it is, and
    the row keeps its status and errors: linking is one action the user is answered about at once,
    so its failure is not stored on the row."""
    with transaction.atomic():
        locked_batch = locked_open_batch(batch)
        locked = ImportBatchRow.objects.select_for_update().filter(pk=row.pk, batch=locked_batch).first()
        if locked is None:
            raise ValidationError(_("The variant is not in this batch."))
        if locked.status in (ImportBatchRow.Status.CREATED, ImportBatchRow.Status.LINKED):
            raise ValidationError(_("The variant already has its edition."))
        if not locked.probable_editions.filter(pk=edition_id).exists():
            raise ValidationError(_("Choose one of the editions the variant may be."))
        edition, changed, filled = link_mycomicshop_edition(edition_id, locked.ivid, user, tid=locked_batch.series_id)
        locked.status, locked.edition, locked.errors = ImportBatchRow.Status.LINKED, edition, {}
        locked.save(update_fields=["status", "edition", "errors"])
    row.status, row.edition, row.errors = locked.status, locked.edition, locked.errors
    return RowLink(row=row, changed=changed, series_id_filled=filled)
