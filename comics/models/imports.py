"""Import batches: several editions of one external catalogue series, reviewed before they are created."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import (
    CASCADE,
    SET_NULL,
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKey,
    JSONField,
    ManyToManyField,
    Model,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    TextChoices,
    TextField,
    UniqueConstraint,
)
from django.utils.translation import gettext, pgettext_lazy

from comics.helper import MYCOMICSHOP_KEY

from .catalog import Edition, Issue, Publishing

# ---------- Import batches ----------
# Gestión picks several variants of one MyComicShop series page (its TID) and reviews them in a
# batch before any edition is created. The batch is stored, so a review survives reloads and can be
# left half done. Its rows are created one by one through the edition form (EditionManageForm), so
# the catalogue history records them as any other edition; the batch itself has no history.
# The JSON fields hold form values by EditionManageForm field name. Credits are lists of
# {"name": str, "artist_id": int | None, "uncertain": bool}: `artist_id` is the matched artist (None
# when the name matched none) and `uncertain` marks names guessed from the catalogue's free text. A
# row's overridden cover artists may also hold {"chosen": True} credits: artists the user added.


class ImportBatch(Model):
    """A batch of variants of one external catalogue series (MyComicShop's TID), grouped by number.

    `defaults` holds the batch-level edition values every row inherits (format, rarity, release
    date, notes...). `name_resolutions` records how each credit name is resolved once for the
    whole batch, keyed by comics.helper.name_key of the name:
    {"name": str, "action": "artist" | "new" | "discard", "artist_id": int | None}, plus
    "new_name" (the name the artist is created with, possibly corrected) for "new". `warnings` are
    what the user should check about the automatic publishing match (e.g. a publishing matched by a
    title written otherwise), as text in the language active when the batch was built; choosing the
    publishing (batches.set_publishing) drops them."""

    class Status(TextChoices):
        OPEN = "open", pgettext_lazy("import batch", "Open")
        CLOSED = "closed", pgettext_lazy("import batch", "Closed")

    created_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="import_batches")
    # The catalogue the batch comes from; only MyComicShop so far.
    source = CharField(max_length=20, default=MYCOMICSHOP_KEY)
    # The series as the catalogue names it, kept so the batch reads the same if the page changes.
    series_id = PositiveIntegerField()
    series_title = CharField(max_length=200)
    series_year = PositiveSmallIntegerField(null=True, blank=True)
    series_publisher = CharField(max_length=200, blank=True)
    # The publishing the editions go to: the one matched by the series id, or the one chosen.
    publishing = ForeignKey(Publishing, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    defaults = JSONField(default=dict, blank=True)
    name_resolutions = JSONField(default=dict, blank=True)
    warnings = JSONField(default=list, blank=True)
    status = CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        year = f" ({self.series_year})" if self.series_year else ""
        return f"#{self.pk} {self.series_title}{year}"


class ImportBatchNumber(Model):
    """The variants of a batch that share an issue number.

    `issue` is the issue of the batch's publishing with that number when the batch was built or its
    publishing chosen (None: it is created with the first row, which then sets it). The batch never
    changes an existing issue's story credits: `creators` (a credit list) are the story credits the
    catalogue proposes, kept whatever the issue, as choosing another publishing can make it new;
    they are only used (and their names only resolved) while `issue` is None."""

    batch = ForeignKey(ImportBatch, on_delete=CASCADE, related_name="numbers")
    number = CharField(max_length=5)  # as Edition.number
    position = PositiveSmallIntegerField()
    issue = ForeignKey(Issue, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    creators = JSONField(default=list, blank=True)

    class Meta:
        ordering = ["batch_id", "position"]
        constraints = [
            UniqueConstraint(fields=["batch", "number"], name="unique_import_batch_number"),
        ]

    def __str__(self) -> str:
        return f"#{self.number} (batch #{self.batch_id})"


class ImportBatchRow(Model):
    """One catalogue variant (MyComicShop's IVID) of a batch: the edition it would create.

    `proposal` holds the values read from the catalogue (EditionManageForm field names, plus
    `cover_url`, `cover_artists`, a credit list, `warnings`, `rejected`, the fields whose
    catalogue value Comi refused, and `cover_key`, the cover as the probable-match rule reads it);
    `overrides` the user's changes to this row, with
    the same keys. A row's values are its overrides, then the batch defaults, then the proposal.
    Variants whose IVID an edition already has are never added; a PROBABLE row looks like the
    `probable_editions` already in Comi, so it starts unselected and can be linked or created.
    `errors` keeps the form errors of the last attempt, `edition` the edition created or linked."""

    class Match(TextChoices):
        NONE = "none", pgettext_lazy("import row", "No match")
        PROBABLE = "probable", pgettext_lazy("import row", "Possible match")

    class Status(TextChoices):
        PENDING = "pending", pgettext_lazy("import row", "Pending")
        CREATED = "created", pgettext_lazy("import row", "Created")
        LINKED = "linked", pgettext_lazy("import row", "Linked")
        FAILED = "failed", pgettext_lazy("import row", "Failed")

    batch = ForeignKey(ImportBatch, on_delete=CASCADE, related_name="rows")
    # Must belong to the same batch; checked in clean() only, as MySQL has no cross-table CHECK.
    group = ForeignKey(ImportBatchNumber, on_delete=CASCADE, related_name="rows")
    position = PositiveSmallIntegerField()
    ivid = PositiveIntegerField()
    label = CharField(max_length=30)  # the catalogue's label, e.g. "1A" or "1RI.A"
    source_text = TextField(blank=True)  # the variant's original text, shown next to what was read
    proposal = JSONField(default=dict, blank=True)
    overrides = JSONField(default=dict, blank=True)
    match = CharField(max_length=10, choices=Match.choices, default=Match.NONE)
    probable_editions = ManyToManyField(Edition, blank=True, related_name="+")
    selected = BooleanField(default=True)
    status = CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    errors = JSONField(default=dict, blank=True)
    edition = ForeignKey(Edition, on_delete=SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["batch_id", "position"]
        constraints = [
            UniqueConstraint(fields=["batch", "ivid"], name="unique_import_batch_ivid"),
        ]

    def __str__(self) -> str:
        return f"{self.label} (IVID {self.ivid})"

    def clean(self) -> None:
        if self.batch_id and self.group_id and self.group.batch_id != self.batch_id:
            raise ValidationError({"group": gettext("The number belongs to another batch.")})
