"""Gestión of the MyComicShop import batches (comics.models.ImportBatch): the list of batches (which
opens the series dialog to start a new one), the API that creates a batch from the variants picked
in that dialog, and the page of one batch, where its credit names are resolved and its variants
created, linked to an edition Comi already has, or left as they are until the batch is closed.

The batch page only reads: every change is its own POST action, which redirects back to the page.
The rules live in comics.catalog_sources.batches and batch_creation and in comics.import_batch_values;
these views map their errors to messages and write the LogEntry of what they created or changed."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from functools import wraps
from typing import Any, Concatenate, ParamSpec, cast

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Count, Prefetch, Q
from django.forms import Form
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from comics.catalog_sources import mycomicshop
from comics.catalog_sources.batch_creation import RETRIED, RowFormValues, create_rows, link_row, row_form_values
from comics.catalog_sources.batches import (
    HAS_EDITION,
    WARNINGS,
    CreditStatus,
    NameAction,
    PendingName,
    clear_name_resolution,
    close_batch,
    create_batch,
    credit_statuses,
    effective_cover_artists,
    effective_cover_url,
    pending_names,
    resolve_name,
    set_publishing,
    set_selection,
)
from comics.catalog_sources.errors import CatalogError
from comics.decorators import management_required
from comics.forms import (
    BATCH_ROW_FIELDS,
    BatchCreateForm,
    BatchDefaultsForm,
    BatchNameForm,
    BatchNameResolutionForm,
    BatchPublishingForm,
    BatchRowForm,
    BatchRowLinkForm,
    BatchSelectionForm,
    EditionManageForm,
    IssueSectionForm,
    publishing_choices,
)
from comics.import_batch_values import EDITED_KEYS, clear_row_overrides, set_defaults, set_row_overrides
from comics.models import Artist, Edition, ImportBatch, ImportBatchRow, Publishing
from comics.views.manage_editions import catalog_answer, series_dialog_data
from comics.views.utils import log_action

PAGE_SIZE = 25
# MySQL's ER_LOCK_WAIT_TIMEOUT: a statement waited for a row lock longer than innodb_lock_wait_timeout.
# A batch action waits for the batch lock while a creation holds it (see batch_creation.create_rows).
MYSQL_LOCK_WAIT_TIMEOUT = 1205

P = ParamSpec("P")

# The edition values a row shows as its summary, in the edition form's order.
SUMMARY_FIELDS = (
    "variant",
    "printing",
    "format",
    "rarity",
    "release_date",
    "cover_price",
    "ratio",
    "limited_to",
    "retailer_exclusive",
    "event_exclusive",
    "notes",
)

# ---------- List ----------


@management_required
def batch_list(request: HttpRequest) -> HttpResponse:
    """Every import batch, newest first, with how many of its rows are in each status, and the
    "New batch" box, which opens a MyComicShop series in the series dialog to pick its variants."""
    counts = {
        f"{status}_count": Count("rows", filter=Q(rows__status=status)) for status in ImportBatchRow.Status.values
    }
    batches = (
        ImportBatch.objects.select_related("publishing", "created_by")
        .annotate(row_count=Count("rows"), **counts)
        .order_by("-created_at", "-pk")
    )
    page_obj = Paginator(batches, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "manage/import_batch_list.html",
        {
            "page_obj": page_obj,
            "elided_page_range": page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
            "series_dialog": series_dialog_data(),
        },
    )


@management_required
@require_POST
def batch_new(request: HttpRequest) -> JsonResponse:
    """Create an import batch with the variants `ivids` of the MyComicShop series `tid`, as picked in
    the series dialog (batches.create_batch). Answers {"url": the batch page}, or {"error": message}
    with the form's first error (400) or the CatalogError's message and status: an invalid
    selection (400), a variant the series does not list (404), one an edition already has (409),
    or MyComicShop's own errors. Nothing is logged: a batch has no history of its own."""
    user = cast(User, request.user)  # management_required: signed in

    def create(data: dict[str, Any]) -> dict[str, Any]:
        return {"url": _batch_url(create_batch(data["tid"], data["ivids"], user))}

    return catalog_answer(BatchCreateForm(request.POST), create)


# ---------- Batch page ----------


@dataclass(frozen=True)
class Chip:
    """A credit as the batch page shows it: `label` (the artist's name, the name it will be created
    with, or the catalogue's name), its CreditState value and, when they differ, the catalogue's name."""

    label: str
    state: str
    source_name: str


@dataclass(frozen=True)
class SummaryValue:
    """An edition value of a row as the batch page shows it."""

    label: str
    value: str
    note: str = ""  # e.g. "preselected": the value create_rows fills in when no layer gives one
    missing: bool = False  # the catalogue's value was refused and nothing replaces it


@dataclass(frozen=True)
class ResolvedView:
    """A name of the batch already resolved: its `action` (a NameAction value) and what it becomes
    (the artist's name, or the name of the artist to create; "" when discarded)."""

    name: str
    action: str
    target: str


@dataclass(frozen=True)
class PendingView:
    """A name still to resolve (batches.PendingName) with the name of the artist it was guessed as."""

    name: str
    artist_id: int | None
    artist: str
    numbers: int
    rows: int


@management_required
def batch_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """The page of an import batch: its header (with its publishing, chosen or changed here while no
    row has its edition), the credit names to resolve, and one block per number with its issue and
    its rows, selectable while they have no edition. A fixed number of queries, whatever its size."""
    batch = get_object_or_404(
        ImportBatch.objects.select_related("publishing", "created_by").prefetch_related("publishing__editorials"),
        pk=pk,
    )
    numbers = list(batch.numbers.select_related("issue__publishing"))
    rows = list(
        batch.rows.select_related("edition__publishing").prefetch_related(
            Prefetch("probable_editions", queryset=Edition.objects.select_related("publishing").order_by("pk"))
        )
    )
    for row in rows:
        row.batch = batch  # effective_values reads it
    pending = pending_names(batch)
    is_open = batch.status == ImportBatch.Status.OPEN
    can_change_publishing = is_open and not any(row.status in HAS_EDITION for row in rows)

    # A number with an existing issue saves no story credits: its proposed ones are not shown.
    number_statuses = {
        number.pk: credit_statuses(batch, number.creators) if number.issue_id is None else [] for number in numbers
    }
    row_statuses = {row.pk: credit_statuses(batch, effective_cover_artists(row)) for row in rows}
    artist_names = _artist_names(batch, [*number_statuses.values(), *row_statuses.values()], pending)

    rows_by_number: dict[int, list[ImportBatchRow]] = {}
    for row in rows:
        row.chips = _chips(row_statuses[row.pk], artist_names)
        row.cover_url = effective_cover_url(row)
        row.summary = _summary(row)
        row.warnings_list = row.proposal.get(WARNINGS, [])
        row.error_list = _errors(row.errors)
        row.can_link = is_open and row.match == ImportBatchRow.Match.PROBABLE and row.status in RETRIED
        row.can_select = is_open and row.status in RETRIED
        row.is_edited = any(key in EDITED_KEYS for key in row.overrides)
        if row.can_select:
            row.edit_form = _row_form(row, row_statuses[row.pk], row.chips)
        rows_by_number.setdefault(row.group_id, []).append(row)
    for number in numbers:
        number.chips = _chips(number_statuses[number.pk], artist_names)
        number.row_list = rows_by_number.get(number.pk, [])

    to_create = sum(1 for row in rows if row.selected and row.status in RETRIED)
    can_edit_rows = any(row.can_select for row in rows)
    return render(
        request,
        "manage/import_batch_detail.html",
        {
            "batch": batch,
            "is_open": is_open,
            "series_url": mycomicshop.series_url(batch.series_id),
            "numbers": numbers,
            "pending": [
                PendingView(
                    name.name, name.artist_id, artist_names.get(name.artist_id or 0, ""), name.numbers, name.rows
                )
                for name in pending
            ],
            "resolved": _resolved_names(batch, artist_names),
            "defaults": _summary_values(batch.defaults),
            "defaults_form": BatchDefaultsForm(initial=batch.defaults, auto_id="import-defaults-%s")
            if is_open
            else None,
            # The artists a name can be associated with or a row's cover can add, once for the whole
            # page (the page script fills the selects from them): only read while one can be used.
            "artist_options": [list(artist) for artist in Artist.objects.order_by("name").values_list("pk", "name")]
            if is_open and (pending or can_edit_rows)
            else [],
            "to_create": to_create,
            "can_create": is_open and batch.publishing_id is not None and to_create > 0,
            "can_change_publishing": can_change_publishing,
            "publishing_options": publishing_choices() if can_change_publishing else [],
            "can_select": can_edit_rows,
        },
    )


def _artist_names(
    batch: ImportBatch, statuses: Iterable[list[CreditStatus]], pending: list[PendingName]
) -> dict[int, str]:
    """{artist id: name} of every artist the page names (credits, resolutions, guesses), in one query."""
    ids = {status.artist_id for group in statuses for status in group if status.artist_id}
    ids |= {
        resolution.get("artist_id") for resolution in batch.name_resolutions.values() if resolution.get("artist_id")
    }
    ids |= {name.artist_id for name in pending if name.artist_id}
    return dict(Artist.objects.filter(pk__in=ids).values_list("pk", "name")) if ids else {}


def _chips(statuses: list[CreditStatus], artist_names: dict[int, str]) -> list[Chip]:
    """`statuses` as the page's chips, labelled with the artist each one is or will be."""
    chips = []
    for status in statuses:
        # An artist deleted since the batch was built shows the catalogue's name.
        label = status.new_name or artist_names.get(status.artist_id or 0, status.name)
        chips.append(Chip(label, status.state.value, status.name if label != status.name else ""))
    return chips


def _resolved_names(batch: ImportBatch, artist_names: dict[int, str]) -> list[ResolvedView]:
    """The names of `batch` already resolved, alphabetically (MySQL does not keep the order of a JSON
    object's keys): their name, action and what they become (the artist's name, or the name of the
    artist to create)."""
    resolved = []
    for resolution in sorted(batch.name_resolutions.values(), key=lambda resolution: resolution["name"].casefold()):
        target = (resolution.get("new_name") or resolution["name"]) if resolution["action"] == NameAction.NEW else ""
        if resolution.get("artist_id"):
            target = artist_names.get(resolution["artist_id"], "")
        resolved.append(ResolvedView(resolution["name"], resolution["action"], target))
    return resolved


def _summary(row: ImportBatchRow) -> list[SummaryValue]:
    """The edition values `row` is created with (batch_creation.row_form_values) as label and
    readable value: the preselected ones are marked, those it cannot be created without are shown
    as missing, and those its overrides leave blank on purpose are shown as such."""
    form_values = row_form_values(row)
    to_fill = form_values.to_fill
    summary = []
    for name in SUMMARY_FIELDS:
        label = _label(name)
        value = form_values.values.get(name)
        if name in to_fill:
            summary.append(SummaryValue(label, "", missing=True))
        elif value not in (None, ""):
            note = _("preselected") if name in form_values.preselected else ""
            summary.append(SummaryValue(label, _readable(name, str(value)), note=note))
        elif row.overrides.get(name) == "":
            summary.append(SummaryValue(label, "—", note=_("left blank")))
    return summary


def _summary_values(values: dict[str, str]) -> list[SummaryValue]:
    """Stored edition values (e.g. the batch defaults) as label and readable value, in form order."""
    return [SummaryValue(_label(name), _readable(name, str(values[name]))) for name in SUMMARY_FIELDS if name in values]


def _label(name: str) -> str:
    return str(EditionManageForm.base_fields[name].label)


def _row_form(row: ImportBatchRow, statuses: list[CreditStatus], chips: list[Chip]) -> BatchRowForm:
    """The form that changes `row` (a row without its edition, of an open batch): its current
    overrides as initial values, what each field inherits as its hint, and its cover artists, as
    the page labels them (`chips`, built from `statuses`), to remove. No queries."""
    initial: dict[str, object] = {}
    for name in BATCH_ROW_FIELDS:
        if row.overrides.get(name) == "":
            initial[BatchRowForm.BLANK_PREFIX + name] = True
        elif name in row.overrides:
            initial[name] = row.overrides[name]
    inherited = row_form_values(row, overrides=False)
    labels = {status.name: chip.label for status, chip in zip(statuses, chips, strict=True)}
    return BatchRowForm(
        initial=initial,
        cover_artists=list(labels.items()),
        hints={name: _inherited_hint(row, inherited, name) for name in BATCH_ROW_FIELDS},
        auto_id=f"import-row-{row.pk}-%s",
    )


def _inherited_hint(row: ImportBatchRow, inherited: RowFormValues, name: str) -> str:
    """What the field `name` of `row` gets when the row does not change it, and where it comes from."""
    if name in inherited.to_fill:
        return _("Inherits nothing: Comi did not accept the catalogue's value.")
    value = inherited.values.get(name)
    if value in (None, ""):
        return _("Inherits nothing.")
    readable = {"value": _readable(name, str(value))}
    if name in inherited.preselected:
        return _("Inherits the preselected value: %(value)s") % readable
    if name in row.batch.defaults:
        return _("Inherits the batch value: %(value)s") % readable
    return _("Inherits the catalogue value: %(value)s") % readable


def _readable(name: str, value: str) -> str:
    """A form value as the page shows it: a choice's label, a formatted date, else the value."""
    field = Edition._meta.get_field(name)
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if name == "release_date":
        try:
            return date_format(date.fromisoformat(value))
        except ValueError:
            return value
    return value


def _errors(errors: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    """Errors by edition form field (e.g. those of a row's last attempt) as (field label, messages);
    "" for the non-field ones."""
    fields = EditionManageForm.base_fields | {"creators": IssueSectionForm.base_fields["creators"]}
    return [(str(fields[name].label) if name in fields else "", texts) for name, texts in errors.items()]


# ---------- Actions ----------


def _batch_url(batch: ImportBatch, anchor: str = "") -> str:
    """The batch page, scrolled to `anchor` when given."""
    url = reverse("manage_import_batch", args=[batch.pk])
    return f"{url}#{anchor}" if anchor else url


def _error_message(exc: ValidationError | CatalogError) -> str:
    return exc.message if isinstance(exc, CatalogError) else " ".join(exc.messages)


def _first_form_error(form: Form) -> str:
    return next(iter(form.errors.values()))[0]


def _field_errors_message(exc: ValidationError) -> str:
    """A ValidationError by edition form field (import_batch_values) as one message: "Label: message"."""
    if not hasattr(exc, "error_dict"):
        return _error_message(exc)
    return " ".join(
        f"{label}: {text}" if label else text for label, texts in _errors(exc.message_dict) for text in texts
    )


def is_lock_wait_timeout(exc: OperationalError) -> bool:
    """Whether `exc` is MySQL's lock wait timeout (MYSQL_LOCK_WAIT_TIMEOUT). Django's database errors
    keep the driver's arguments, the MySQL error code first."""
    return bool(exc.args) and exc.args[0] == MYSQL_LOCK_WAIT_TIMEOUT


def answers_busy_batch(
    view: Callable[Concatenate[HttpRequest, int, P], HttpResponseRedirect],
) -> Callable[Concatenate[HttpRequest, int, P], HttpResponseRedirect]:
    """A batch action that answers "the batch is busy" when it waited for the batch's lock longer
    than MySQL allows (a creation holds it while each row's cover is downloaded): the transaction
    that waited is rolled back, so the action can be sent again. A creation run stopped so keeps
    the rows it created before (each row has its own transaction). Any other database error is
    raised as it is."""

    @wraps(view)
    def wrapper(request: HttpRequest, pk: int, *args: P.args, **kwargs: P.kwargs) -> HttpResponseRedirect:
        try:
            return view(request, pk, *args, **kwargs)
        except OperationalError as exc:
            if not is_lock_wait_timeout(exc):
                raise
            messages.error(request, _("The batch is busy (it is creating editions): try again in a moment."))
            return redirect(reverse("manage_import_batch", args=[pk]))

    return wrapper


@management_required
@require_POST
@answers_busy_batch
def batch_resolve_name(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Resolve a credit name of the batch for all its rows (batches.resolve_name)."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    form = BatchNameResolutionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect(_batch_url(batch, "import-names"))
    data = form.cleaned_data
    try:
        resolve_name(batch, data["name"], data["action"], artist_id=data["artist"], new_name=data["new_name"])
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(request, _("Name resolved: %(name)s") % {"name": data["name"]})
    return redirect(_batch_url(batch, "import-names"))


@management_required
@require_POST
@answers_busy_batch
def batch_clear_name(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Undo the resolution of a credit name of the batch (batches.clear_name_resolution)."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    form = BatchNameForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect(_batch_url(batch, "import-names"))
    name = form.cleaned_data["name"]
    try:
        clear_name_resolution(batch, name)
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(request, _("Resolution undone: %(name)s") % {"name": name})
    return redirect(_batch_url(batch, "import-names"))


@management_required
@require_POST
@answers_busy_batch
def batch_create(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Create the editions of the selected rows still pending or failed (batch_creation.create_rows),
    then record in the history what was created: the new artists, the editions and the TID stored
    on the publishing."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    user = cast(User, request.user)  # management_required: signed in
    origin = f"desde Gestión (lote de importación #{batch.pk})"

    def log_artist(artist: Artist) -> None:
        log_action(request, artist, ADDITION, f"Creado {origin}.")

    def log_row(row: ImportBatchRow) -> None:
        log_action(request, cast(Edition, row.edition), ADDITION, f"Creada {origin}, MyComicShop IVID {row.ivid}.")

    def log_series_id(publishing_id: int) -> None:
        # Publishing has no history of its own: the TID stored on it is recorded here. The id is the
        # publishing create_rows used, which may differ from the one this request read.
        log_action(
            request,
            Publishing.objects.get(pk=publishing_id),
            CHANGE,
            f"Serie de MyComicShop (TID {batch.series_id}) guardada al importar el lote #{batch.pk} desde Gestión.",
        )

    try:
        # The history entries are written inside the transactions that create what they record.
        result = create_rows(
            batch,
            user=user,
            on_artist_created=log_artist,
            on_row_created=log_row,
            on_series_id_filled=log_series_id,
        )
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
        return redirect(_batch_url(batch))
    created, failed = len(result.created), len(result.failed)
    if not created and not failed and not result.interrupted:
        messages.error(request, _("There was nothing to create: no selected variant is pending."))
        return redirect(_batch_url(batch))
    parts = []
    if created:
        parts.append(
            ngettext("%(count)d edition created.", "%(count)d editions created.", created) % {"count": created}
        )
    if result.artists:
        count = len(result.artists)
        parts.append(ngettext("%(count)d artist created.", "%(count)d artists created.", count) % {"count": count})
    if failed:
        parts.append(
            ngettext(
                "%(count)d variant could not be created: see its errors.",
                "%(count)d variants could not be created: see their errors.",
                failed,
            )
            % {"count": failed}
        )
    if result.interrupted:
        parts.append(
            _("The batch changed meanwhile (closed or given another publishing): the other variants were not tried.")
        )
    (messages.error if failed or result.interrupted else messages.success)(request, " ".join(parts))
    return redirect(_batch_url(batch))


@management_required
@require_POST
@answers_busy_batch
def batch_link_row(request: HttpRequest, pk: int, row_pk: int) -> HttpResponseRedirect:
    """Link a probable row to one of the editions it may be (batch_creation.link_row), recording the
    edition's new IVID and the publishing's new TID in the history, like the variants modal does."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    row = get_object_or_404(ImportBatchRow, pk=row_pk, batch=batch)
    anchor = f"import-row-{row.pk}"
    form = BatchRowLinkForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect(_batch_url(batch, anchor))
    user = cast(User, request.user)  # management_required: signed in
    try:
        with transaction.atomic():
            link = link_row(batch, row, form.cleaned_data["edition"], user=user)
            edition = cast(Edition, link.row.edition)
            if link.changed:
                log_action(
                    request,
                    edition,
                    CHANGE,
                    f"Vinculada a MyComicShop (IVID {row.ivid}) desde Gestión (lote de importación #{batch.pk}).",
                )
            if link.series_id_filled:
                log_action(
                    request,
                    edition.publishing,
                    CHANGE,
                    f"Serie de MyComicShop (TID {batch.series_id}) guardada al vincular {edition.short_name} "
                    "desde Gestión.",
                )
    except (ValidationError, CatalogError) as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(
            request, _("%(variant)s linked to %(edition)s.") % {"variant": row.label, "edition": edition.short_name}
        )
    return redirect(_batch_url(batch, anchor))


@management_required
@require_POST
@answers_busy_batch
def batch_set_publishing(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Choose or change the publishing of the batch (batches.set_publishing), which looks again at
    the numbers' existing issues and the variants' possible matches. The batch has no history: the
    message is the only record."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    form = BatchPublishingForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect(_batch_url(batch))
    publishing = form.cleaned_data["publishing"]
    try:
        changed = set_publishing(batch, publishing)
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        label = {"publishing": publishing.select_label}
        if changed:
            messages.success(request, _("The batch now goes to %(publishing)s.") % label)
        else:
            messages.info(request, _("The batch already goes to %(publishing)s.") % label)
    return redirect(_batch_url(batch))


@management_required
@require_POST
@answers_busy_batch
def batch_select_rows(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Save which variants of the batch are selected to be created (batches.set_selection): the
    checked ones, or all or none of them."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    form = BatchSelectionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect(_batch_url(batch))
    try:
        set_selection(batch, form.selection())
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(request, _("Selection saved."))
    return redirect(_batch_url(batch))


@management_required
@require_POST
@answers_busy_batch
def batch_set_defaults(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Save the values every variant of the batch inherits (import_batch_values.set_defaults)."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    try:
        set_defaults(batch, request.POST)
    except ValidationError as exc:
        messages.error(request, _field_errors_message(exc))
    else:
        messages.success(request, _("Values for every variant saved."))
    return redirect(_batch_url(batch, "import-defaults"))


@management_required
@require_POST
@answers_busy_batch
def batch_edit_row(request: HttpRequest, pk: int, row_pk: int) -> HttpResponseRedirect:
    """Save the changes of a variant of the batch, its values and cover artists
    (import_batch_values.set_row_overrides)."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    row = get_object_or_404(ImportBatchRow, pk=row_pk, batch=batch)
    try:
        changed = set_row_overrides(batch, row, request.POST)
    except ValidationError as exc:
        messages.error(request, _field_errors_message(exc))
    else:
        if changed:
            messages.success(request, _("%(variant)s saved.") % {"variant": row.label})
        else:
            messages.info(request, _("%(variant)s had no changes.") % {"variant": row.label})
    return redirect(_batch_url(batch, f"import-row-{row.pk}"))


@management_required
@require_POST
@answers_busy_batch
def batch_reset_row(request: HttpRequest, pk: int, row_pk: int) -> HttpResponseRedirect:
    """Drop the changes of a variant of the batch: it goes back to the proposal
    (import_batch_values.clear_row_overrides)."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    row = get_object_or_404(ImportBatchRow, pk=row_pk, batch=batch)
    try:
        clear_row_overrides(batch, row)
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(request, _("%(variant)s is back to the proposal.") % {"variant": row.label})
    return redirect(_batch_url(batch, f"import-row-{row.pk}"))


@management_required
@require_POST
@answers_busy_batch
def batch_close(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Close the batch: it stays as a read-only record of what was imported."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    try:
        close_batch(batch)
    except ValidationError as exc:
        messages.error(request, _error_message(exc))
    else:
        messages.success(request, _("Batch closed."))
    return redirect(_batch_url(batch))
