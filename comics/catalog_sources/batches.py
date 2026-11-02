"""MyComicShop import batches (comics.models.ImportBatch): building one from the variants picked on a
series page (its TID), the values a row's edition gets, and the credit names resolved once for the
whole batch.

Building a batch makes one request to MyComicShop (the series page, cached) and a fixed number of
queries, whatever the number of variants. Nothing is created in the catalogue here: the rows become
editions later, through the edition form (comics.catalog_sources.batch_creation)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from comics.helper import name_key
from comics.models import Artist, Edition, ImportBatch, ImportBatchNumber, ImportBatchRow, Issue, Publishing

from . import mycomicshop
from .errors import Conflict, InvalidSelection, NotFound
from .mapper import ArtistMatcher, allowed_cover_url, edition_values
from .service import CoverKey, mycomicshop_publishing, probable_editions
from .types import SourceCredit

# The cover of every row is downloaded while its edition is created, in the same request.
MAX_BATCH_ROWS = 30
# The keys of a row's proposal (and overrides) that are not edition form values.
# REJECTED: the form fields the catalogue gave a value for that Comi refused (mapper.edition_values).
# COVER_KEY: the cover as the catalogue gives it, for the probable-match rule (service.CoverKey
# without its number, which is the row's number): {"letter", "printing", "exclusive"}. The form
# values cannot stand for it: an unstated printing is proposed as "1st" but matches any printing.
COVER_URL, COVER_ARTISTS, WARNINGS, REJECTED = "cover_url", "cover_artists", "warnings", "rejected"
COVER_KEY = "cover_key"
NON_FORM_KEYS = frozenset({COVER_URL, COVER_ARTISTS, WARNINGS, REJECTED, COVER_KEY})
# The statuses of a row that already has its edition: it no longer changes.
HAS_EDITION = (ImportBatchRow.Status.CREATED, ImportBatchRow.Status.LINKED)


class BatchCredit(TypedDict):
    """A credit of a batch credit list (ImportBatchNumber.creators, a row's cover artists)."""

    name: str  # as the catalogue writes it
    artist_id: int | None  # the matched artist, None when the name matched none
    uncertain: bool  # guessed from the catalogue's free text
    # An artist the user added to a row's cover artists (comics.import_batch_values.set_row_overrides): `name` is
    # the artist's name and the credit is that artist, whatever the batch's name resolutions say.
    chosen: NotRequired[bool]


class NameAction(StrEnum):
    """How a credit name of a batch is resolved (ImportBatch.name_resolutions)."""

    ARTIST = "artist"  # it is an existing artist (`artist_id`)
    NEW = "new"  # create an artist with that name
    DISCARD = "discard"  # drop the credit


# ---------- Building a batch ----------


def create_batch(tid: int, ivids: Sequence[int], user: User) -> ImportBatch:
    """A new batch with the variants `ivids` of MyComicShop's series `tid`, created by `user`.

    One number per issue number and one row per variant, both in page order. Every number keeps the
    story credits its variants propose (`creators`), but they are only used while its issue is new:
    a number whose issue already exists in the matched publishing keeps that issue's credits. A row
    that looks like editions Comi already has (service.probable_editions) is PROBABLE and starts
    unselected.

    Raises InvalidSelection for no variants, more than MAX_BATCH_ROWS or a number longer than
    Edition.number allows; NotFound for an IVID the series does not list; Conflict when an edition
    already has one of the IVIDs; and any CatalogError of fetching the series page."""
    wanted = set(ivids)
    if not wanted:
        raise InvalidSelection(_("Select at least one variant."))
    if len(wanted) > MAX_BATCH_ROWS:
        raise InvalidSelection(_("A batch can have at most %(count)s variants.") % {"count": MAX_BATCH_ROWS})
    series = mycomicshop.fetch_series(tid)
    variants = _selected_variants(series, ivids, wanted)
    with transaction.atomic():
        owner = Edition.objects.filter(mycomicshop_id__in=wanted).select_related("publishing").order_by("pk").first()
        if owner is not None:
            raise Conflict(_("%(edition)s already has this MyComicShop ID.") % {"edition": owner.short_name})
        warnings: list[str] = []
        publishing = mycomicshop_publishing(series, warnings)
        covers = {variant.ivid: CoverKey.of(variant) for variant in variants}
        probable = probable_editions(publishing, covers)
        artists = ArtistMatcher()
        batch = ImportBatch.objects.create(
            created_by=user,
            source=mycomicshop.KEY,
            series_id=series.tid or tid,
            series_title=_cut(series.title, ImportBatch, "series_title"),
            series_year=series.year,
            series_publisher=_cut(series.publisher or series.publisher_short, ImportBatch, "series_publisher"),
            publishing=publishing,
            warnings=warnings,
        )
        by_number: dict[str, list[mycomicshop.McsVariant]] = {}
        for variant in variants:
            by_number.setdefault(variant.number.strip(), []).append(variant)
        _create_numbers(batch, by_number, _existing_issues(publishing, by_number), artists)
        number_ids = dict(batch.numbers.values_list("number", "pk"))
        ImportBatchRow.objects.bulk_create(
            _row(
                batch,
                number_ids[variant.number.strip()],
                position,
                series,
                variant,
                covers[variant.ivid],
                probable,
                artists,
            )
            for position, variant in enumerate(variants)
        )
        if probable:
            # bulk_create gives no ids on MySQL, so the rows are read back for their links.
            row_ids = dict(batch.rows.values_list("ivid", "pk"))
            through = ImportBatchRow.probable_editions.through
            through.objects.bulk_create(
                through(importbatchrow_id=row_ids[ivid], edition_id=edition.pk)
                for ivid, editions in probable.items()
                for edition in editions
            )
    return batch


def _selected_variants(
    series: mycomicshop.McsSeries, ivids: Sequence[int], wanted: set[int]
) -> list[mycomicshop.McsVariant]:
    """The variants of `series` with the IVIDs `ivids` (`wanted` is set(ivids)), in page order (a
    repeated IVID counts once)."""
    variants = [variant for variant in series.variants if variant.ivid in wanted]
    listed = {variant.ivid for variant in variants}
    missing = next((ivid for ivid in ivids if ivid not in listed), None)
    if missing is not None:
        raise NotFound(
            _("%(source)s has no issue with the id %(id)s in the series %(series)s.")
            % {"source": mycomicshop.NAME, "id": missing, "series": series.tid}
        )
    max_length = Edition._meta.get_field("number").max_length
    for variant in variants:
        if len(variant.number.strip()) > max_length:
            raise InvalidSelection(
                _("The number “%(number)s” is longer than Comi allows.") % {"number": variant.number.strip()}
            )
    return variants


def _existing_issues(publishing: Publishing | None, numbers: Iterable[str]) -> dict[str, Issue]:
    """{number: the issue of `publishing` with it}, in one query. Numbers are compared as
    Edition.assign_default_issue looks the issue up (publishing and stripped number); the keys are
    casefolded, as MySQL compares them, and stripped: a PAD SPACE collation would also return a
    stored number with trailing spaces (Comi's utf8mb4_0900_ai_ci is NO PAD, so it returns none)."""
    if publishing is None:
        return {}
    issues = Issue.objects.filter(publishing=publishing, number__in=list(numbers))
    return {issue.number.strip().casefold(): issue for issue in issues}


def _create_numbers(
    batch: ImportBatch,
    by_number: dict[str, list[mycomicshop.McsVariant]],
    issues: dict[str, Issue],
    artists: ArtistMatcher,
) -> None:
    numbers = []
    for position, (number, variants) in enumerate(by_number.items()):
        story = (credit for variant in variants for credit in variant.details.credits if credit.is_story)
        numbers.append(
            ImportBatchNumber(
                batch=batch,
                number=number,
                position=position,
                issue=issues.get(number.casefold()),
                # Kept even for an existing issue: changing the publishing (set_publishing) may make it new.
                creators=credit_list(story, artists),
            )
        )
    ImportBatchNumber.objects.bulk_create(numbers)


def _row(
    batch: ImportBatch,
    group_id: int,
    position: int,
    series: mycomicshop.McsSeries,
    variant: mycomicshop.McsVariant,
    cover: CoverKey,
    probable: dict[int, list[Edition]],
    artists: ArtistMatcher,
) -> ImportBatchRow:
    warnings: list[str] = []
    rejected: list[str] = []
    source_issue = mycomicshop.to_source_issue(series, variant)
    proposal: dict[str, Any] = dict(edition_values(source_issue, warnings, rejected))
    cover_url = allowed_cover_url(variant.cover_url, warnings)
    if cover_url:
        proposal[COVER_URL] = cover_url
    proposal[COVER_ARTISTS] = credit_list((credit for credit in variant.details.credits if credit.is_cover), artists)
    # Stored as text, in the language active when the batch was built.
    proposal[WARNINGS] = warnings
    proposal[REJECTED] = rejected
    proposal[COVER_KEY] = {"letter": cover.letter, "printing": cover.printing, "exclusive": cover.exclusive}
    is_probable = variant.ivid in probable
    return ImportBatchRow(
        batch=batch,
        group_id=group_id,
        position=position,
        ivid=variant.ivid,
        label=_cut(variant.label, ImportBatchRow, "label"),
        source_text=variant.details.text,
        proposal=proposal,
        match=ImportBatchRow.Match.PROBABLE if is_probable else ImportBatchRow.Match.NONE,
        selected=not is_probable,
    )


def _cut(text: str, model: type[ImportBatch | ImportBatchRow], field: str) -> str:
    """`text` cut to the max_length of `model.field` (catalogue text Comi only shows)."""
    return text[: model._meta.get_field(field).max_length]


def credit_list(credits: Iterable[SourceCredit], artists: ArtistMatcher) -> list[BatchCredit]:
    """`credits` as a batch credit list (see BatchCredit and comics.models.imports), once per name
    (comics.helper.name_key) in the given order. A name read in several credits is uncertain only
    when every one of them is: any certain reading confirms it."""
    by_key: dict[str, BatchCredit] = {}
    for credit in credits:
        key = name_key(credit.name)
        if not key:
            continue
        if key in by_key:
            by_key[key]["uncertain"] = by_key[key]["uncertain"] and credit.uncertain
            continue
        found = artists.find(credit.name)
        by_key[key] = {"name": credit.name, "artist_id": found.id if found else None, "uncertain": credit.uncertain}
    return list(by_key.values())


# ---------- A row's values ----------


def effective_values(row: ImportBatchRow, *, overrides: bool = True) -> dict[str, Any]:
    """The edition form values of `row`: its proposal, overridden by the batch defaults, overridden
    by the row's overrides (left out with `overrides=False`: what the row inherits). Only form
    values: the cover is in effective_cover_url and effective_cover_artists. Reads `row.batch`
    (select_related it for several rows)."""
    values: dict[str, Any] = {}
    layers = (row.proposal, row.batch.defaults, row.overrides) if overrides else (row.proposal, row.batch.defaults)
    for layer in layers:
        values.update((key, value) for key, value in layer.items() if key not in NON_FORM_KEYS)
    return values


def effective_cover_url(row: ImportBatchRow) -> str:
    """The cover to download for `row`: its override (possibly "", no cover), else the proposal's."""
    return row.overrides.get(COVER_URL, row.proposal.get(COVER_URL, ""))


def effective_cover_artists(row: ImportBatchRow) -> list[BatchCredit]:
    """The cover artists credit list of `row`: its override (the proposal's credits the user kept,
    then the artists added, which are `chosen`), else the proposal's."""
    return row.overrides.get(COVER_ARTISTS, row.proposal.get(COVER_ARTISTS, []))


# ---------- Credit names ----------


def needs_resolution(credit: BatchCredit) -> bool:
    """Whether a credit's name must be resolved before it is saved: it matched no artist, or was guessed."""
    return credit.get("artist_id") is None or bool(credit.get("uncertain"))


@dataclass
class PendingName:
    """A credit name of a batch still to be resolved, with where it is used."""

    name: str  # as it first appears in the batch
    artist_id: int | None  # the artist a guessed name matched, None when it matched none
    numbers: int  # how many numbers with a new issue propose it among their story credits
    rows: int  # how many rows have it among their cover artists


@dataclass
class _NameUse:
    """A credit name of a batch and where it is used, whether or not it needs a resolution."""

    name: str
    artist_id: int | None = None
    numbers: int = 0
    rows: int = 0
    pending: bool = False  # one of its credits needs a resolution


def _batch_names(batch: ImportBatch) -> dict[str, _NameUse]:
    """{name_key: a credit name of the batch and its uses}, in page order (each number's story
    credits, then the cover artists of its rows). A number whose issue exists saves no story
    credits, so its proposed ones are not names of the batch. Two queries."""
    rows_by_group: dict[int, list[ImportBatchRow]] = {}
    for row in batch.rows.only("batch_id", "group_id", "proposal", "overrides"):
        rows_by_group.setdefault(row.group_id, []).append(row)
    names: dict[str, _NameUse] = {}

    def add(credits: list[BatchCredit], *, in_number: bool) -> None:
        seen: set[str] = set()  # a list names each artist once; an edited one might not
        for credit in credits:
            key = name_key(credit.get("name", ""))
            # A chosen artist is no name to resolve: the batch's resolutions never apply to it.
            if not key or key in seen or credit.get("chosen"):
                continue
            seen.add(key)
            use = names.setdefault(key, _NameUse(credit["name"]))
            if in_number:
                use.numbers += 1
            else:
                use.rows += 1
            if needs_resolution(credit) and not use.pending:
                use.pending = True
                use.artist_id = credit.get("artist_id")

    for number_id, issue_id, creators in batch.numbers.values_list("pk", "issue_id", "creators"):
        if issue_id is None:
            add(creators, in_number=True)
        for row in rows_by_group.get(number_id, []):
            add(effective_cover_artists(row), in_number=False)
    return names


def pending_names(batch: ImportBatch) -> list[PendingName]:
    """The credit names of `batch` that need a resolution (see needs_resolution) and have none yet,
    once per name (comics.helper.name_key) in first-appearance order. Their uses count every credit
    with that name, as a resolution applies to all of them. Two queries."""
    return [
        PendingName(use.name, use.artist_id, use.numbers, use.rows)
        for key, use in _batch_names(batch).items()
        if use.pending and key not in batch.name_resolutions
    ]


def resolve_name(
    batch: ImportBatch, name: str, action: str, artist_id: int | None = None, new_name: str | None = None
) -> None:
    """Resolve the credit name `name` for the whole open `batch`: it is the artist `artist_id`
    (action "artist"), a new artist to create ("new") or dropped ("discard"); see NameAction. Any
    name of the batch can be resolved, also one that matched an artist (to correct or drop it).

    A new artist is created (by batch_creation.create_rows) with `new_name`, so a name the
    catalogue's text cut or extended can be corrected first; by default the name as the batch first
    spells it. Its spaces are collapsed. When an artist already has that name (comics.helper.name_key)
    the resolution is stored as that artist instead, so the batch shows what will happen and no
    duplicate is created; create_rows still reuses an artist named so after this call.

    Updates `batch.name_resolutions`; raises ValidationError when the action, the artist, the new
    name or the name is not valid, or the batch is closed."""
    try:
        chosen = NameAction(action)
    except ValueError:
        raise ValidationError(_("Choose what to do with the name.")) from None
    if chosen is NameAction.ARTIST:
        if artist_id is None or not Artist.objects.filter(pk=artist_id).exists():
            raise ValidationError(_("Choose an existing artist."))
    elif artist_id is not None:
        raise ValidationError(_("An artist is only given to associate the name with it."))
    if new_name is not None and chosen is not NameAction.NEW:
        raise ValidationError(_("A new name is only given to create an artist."))
    with transaction.atomic():
        locked = locked_open_batch(batch)
        use = _batch_names(locked).get(name_key(name))
        if use is None:
            raise ValidationError(_("“%(name)s” is not a name of this batch.") % {"name": name})
        resolution: dict[str, Any] = {"name": use.name, "action": chosen.value, "artist_id": artist_id}
        if chosen is NameAction.NEW:
            artist_name = _new_artist_name(use.name if new_name is None else new_name)
            existing = ArtistMatcher().find(artist_name)
            if existing is None:
                resolution["new_name"] = artist_name
            else:
                resolution.update(action=NameAction.ARTIST.value, artist_id=existing.id)
        locked.name_resolutions[name_key(name)] = resolution
        locked.save(update_fields=["name_resolutions", "updated_at"])
    batch.name_resolutions, batch.updated_at = locked.name_resolutions, locked.updated_at


def _new_artist_name(name: str) -> str:
    """`name` with its spaces collapsed, as Artist.objects.ensure_named saves it; ValidationError
    when it is empty or longer than Artist.name allows."""
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValidationError(_("Enter a name."))
    limit = Artist._meta.get_field("name").max_length
    if len(cleaned) > limit:
        raise ValidationError(
            _("“%(name)s” is longer than %(limit)d characters.") % {"name": cleaned, "limit": limit}
        )
    return cleaned


def clear_name_resolution(batch: ImportBatch, name: str) -> None:
    """Forget how `name` was resolved in the open `batch` (nothing happens when it was not). Raises
    ValidationError when the batch is closed."""
    with transaction.atomic():
        locked = locked_open_batch(batch)
        if locked.name_resolutions.pop(name_key(name), None) is None:
            return
        locked.save(update_fields=["name_resolutions", "updated_at"])
    batch.name_resolutions, batch.updated_at = locked.name_resolutions, locked.updated_at


def locked_open_batch(batch: ImportBatch) -> ImportBatch:
    """`batch` read again and locked until the transaction ends, so two changes of the batch saved at
    once (resolutions, creating its rows) do not overwrite each other; ValidationError when it is
    closed. Also used by comics.catalog_sources.batch_creation and comics.import_batch_values."""
    locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if locked.status != ImportBatch.Status.OPEN:
        raise ValidationError(_("The batch is closed."))
    return locked


@dataclass
class ResolvedCredits:
    """A credit list with the batch's resolutions applied. Credits in `unresolved` cannot be saved
    yet: whoever saves them must refuse (a row with unresolved names is not created)."""

    artist_ids: list[int]  # existing artists, each once
    new_names: list[str]  # artists to create (their new_name), each once (comics.helper.name_key)
    unresolved: list[str]  # names that need a resolution and have none


def resolved_credits(batch: ImportBatch, credits: Iterable[BatchCredit]) -> ResolvedCredits:
    """`credits` (a batch credit list) as their batch resolutions say: a resolved name becomes its
    artist, a new artist or nothing (discarded); an unresolved one keeps its matched artist unless
    it needs a resolution (see needs_resolution), then it is reported in `unresolved`. A `chosen`
    credit is its artist, whatever the resolutions. No queries: an artist deleted since it was
    chosen is for the form saving the credits to reject."""
    result = ResolvedCredits([], [], [])
    new_keys: set[str] = set()
    for credit in credits:
        key = name_key(credit.get("name", ""))
        if not key:
            continue
        resolution = batch.name_resolutions.get(key)
        if credit.get("chosen"):
            if credit["artist_id"] not in result.artist_ids:
                result.artist_ids.append(credit["artist_id"])
        elif resolution is None:
            if needs_resolution(credit):
                result.unresolved.append(credit["name"])
            elif credit["artist_id"] not in result.artist_ids:
                result.artist_ids.append(credit["artist_id"])
        elif resolution["action"] == NameAction.ARTIST:
            if resolution["artist_id"] not in result.artist_ids:
                result.artist_ids.append(resolution["artist_id"])
        elif resolution["action"] == NameAction.NEW:
            # Two names can be corrected into the same one: it is still one artist.
            new_name = resolution.get("new_name") or resolution["name"]
            if name_key(new_name) not in new_keys:
                new_keys.add(name_key(new_name))
                result.new_names.append(new_name)
    return result


class CreditState(StrEnum):
    """Where a credit of a batch stands, as the batch page shows it."""

    MATCHED = "matched"  # the name is an artist and was read with certainty: nothing to resolve
    GUESSED = "guessed"  # guessed from the catalogue's free text; it matched an artist to confirm
    UNMATCHED = "unmatched"  # it matched no artist: to resolve
    RESOLVED = "resolved"  # resolved as an existing artist
    NEW = "new"  # resolved as an artist to create
    DISCARDED = "discarded"  # resolved as dropped


@dataclass(frozen=True)
class CreditStatus:
    """A credit of a batch credit list with its state (see credit_statuses)."""

    name: str  # as the catalogue writes it
    state: CreditState
    artist_id: int | None  # the artist it is (matched, guessed or resolved as); None otherwise
    new_name: str = ""  # NEW: the name the artist will be created with


def credit_statuses(batch: ImportBatch, credits: Iterable[BatchCredit]) -> list[CreditStatus]:
    """`credits` (a batch credit list) with where each stands given the resolutions of `batch`, once
    per name (comics.helper.name_key) in the given order. Follows resolved_credits; no queries."""
    statuses: list[CreditStatus] = []
    seen: set[str] = set()
    for credit in credits:
        key = name_key(credit.get("name", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        name, artist_id = credit["name"], credit.get("artist_id")
        resolution = batch.name_resolutions.get(key)
        if credit.get("chosen"):
            statuses.append(CreditStatus(name, CreditState.MATCHED, artist_id))
        elif resolution is None:
            if artist_id is None:
                statuses.append(CreditStatus(name, CreditState.UNMATCHED, None))
            else:
                state = CreditState.GUESSED if credit.get("uncertain") else CreditState.MATCHED
                statuses.append(CreditStatus(name, state, artist_id))
        elif resolution["action"] == NameAction.ARTIST:
            statuses.append(CreditStatus(name, CreditState.RESOLVED, resolution["artist_id"]))
        elif resolution["action"] == NameAction.NEW:
            new_name = resolution.get("new_name") or resolution["name"]
            statuses.append(CreditStatus(name, CreditState.NEW, None, new_name))
        else:
            statuses.append(CreditStatus(name, CreditState.DISCARDED, None))
    return statuses


# ---------- Publishing ----------


def set_publishing(batch: ImportBatch, publishing: Publishing) -> bool:
    """Make `publishing` the publishing of the open `batch` (the user chose it: none matched, or the
    match was wrong) and look again, without asking MyComicShop, at what depended on it:
    - each number's issue: the issue of `publishing` with its number (as create_batch finds it), or
      None. The story credits a number keeps (`creators`) are only used while it has none;
    - each row's probable editions (service.probable_editions on the cover its proposal stores,
      COVER_KEY): a row that becomes PROBABLE is unselected and one that stops being PROBABLE is
      selected, as create_batch would leave them; any other row keeps its selection. A row without
      a stored cover key is never probable;
    - FAILED rows go back to PENDING without their errors: they were about the other publishing,
      and the next creation tries them again anyway;
    - `batch.warnings` are dropped: they were about the automatic publishing match.
    Choosing the publishing the batch already has recomputes nothing, but still drops the warnings
    (the user confirmed the match). Returns whether the publishing changed.

    One transaction and a fixed number of queries, whatever the size of the batch. Raises
    ValidationError when the batch is closed, or when a row already has its edition (CREATED or
    LINKED): the editions already in the old publishing would no longer match the batch. That
    check sees the rows a create_rows run is creating right now: each one is created holding the
    batch lock, which this waits for."""
    with transaction.atomic():
        locked = locked_open_batch(batch)
        if locked.publishing_id == publishing.pk:
            if locked.warnings:
                locked.warnings = []
                locked.save(update_fields=["warnings", "updated_at"])
            changed = False
        else:
            if locked.rows.filter(status__in=HAS_EDITION).exists():
                raise ValidationError(
                    _("The publishing can no longer change: some variants of the batch already have their edition.")
                )
            _recompute_for_publishing(locked, publishing)
            locked.publishing, locked.warnings = publishing, []
            locked.save(update_fields=["publishing", "warnings", "updated_at"])
            changed = True
    batch.publishing, batch.warnings, batch.updated_at = locked.publishing, locked.warnings, locked.updated_at
    return changed


def _recompute_for_publishing(batch: ImportBatch, publishing: Publishing) -> None:
    """The numbers' issues and the rows' probable editions of `batch` (locked, its rows without
    editions) for `publishing`; see set_publishing. A fixed number of queries."""
    numbers = list(batch.numbers.all())
    issues = _existing_issues(publishing, [number.number for number in numbers])
    for number in numbers:
        number.issue = issues.get(number.number.casefold())
    ImportBatchNumber.objects.bulk_update(numbers, ["issue"])

    number_of = {number.pk: number.number for number in numbers}
    rows = list(batch.rows.only("pk", "batch_id", "group_id", "proposal", "match"))
    covers = {
        row.pk: CoverKey(number_of[row.group_id], **row.proposal[COVER_KEY])
        for row in rows
        if COVER_KEY in row.proposal
    }
    probable = probable_editions(publishing, covers)
    through = ImportBatchRow.probable_editions.through
    through.objects.filter(importbatchrow_id__in=[row.pk for row in rows]).delete()
    through.objects.bulk_create(
        through(importbatchrow_id=row_id, edition_id=edition.pk)
        for row_id, editions in probable.items()
        for edition in editions
    )
    newly_probable = [row.pk for row in rows if row.pk in probable and row.match != ImportBatchRow.Match.PROBABLE]
    batch.rows.filter(pk__in=newly_probable).update(match=ImportBatchRow.Match.PROBABLE, selected=False)
    batch.rows.filter(match=ImportBatchRow.Match.PROBABLE).exclude(pk__in=list(probable)).update(
        match=ImportBatchRow.Match.NONE, selected=True
    )
    batch.rows.filter(status=ImportBatchRow.Status.FAILED).update(status=ImportBatchRow.Status.PENDING, errors={})


# ---------- Selection ----------


def set_selection(batch: ImportBatch, selected: Iterable[int] | None) -> int:
    """Select the rows of the open `batch` whose ids are in `selected` and unselect its other rows
    (None: select them all); only the rows without their edition yet change (PENDING, FAILED), so ids
    of other rows (created, linked, of another batch) are ignored. Returns how many rows changed.
    Raises ValidationError when the batch is closed. A fixed number of queries."""
    with transaction.atomic():
        locked = locked_open_batch(batch)
        rows = locked.rows.exclude(status__in=HAS_EDITION)
        if selected is None:
            return rows.filter(selected=False).update(selected=True)
        ids = set(selected)
        chosen = rows.filter(pk__in=ids, selected=False).update(selected=True)
        dropped = rows.filter(selected=True).exclude(pk__in=ids).update(selected=False)
    return chosen + dropped


# ---------- Closing a batch ----------


def close_batch(batch: ImportBatch) -> None:
    """Close the open `batch`: from then on it is read-only, as every change of a batch (here and in
    batch_creation) refuses a closed one. Raises ValidationError when it is closed already."""
    with transaction.atomic():
        locked = locked_open_batch(batch)
        locked.status = ImportBatch.Status.CLOSED
        locked.save(update_fields=["status", "updated_at"])
    batch.status, batch.updated_at = locked.status, locked.updated_at
