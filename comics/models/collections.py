"""What collectors own and follow: purchases and sales, signatures, reading arcs, connectings, collectables."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Self

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BooleanField,
    CharField,
    DateField,
    DecimalField,
    ForeignKey,
    ImageField,
    ManyToManyField,
    Model,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
    UniqueConstraint,
)
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from ..helper import generate_image_jpeg
from .catalog import Artist, Edition, Issue


class Dealer(Model):
    name = CharField(max_length=100, unique=True)
    real_name = CharField(max_length=100, blank=True)
    fb = CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CollectionQuerySet(QuerySet):
    def owned_by(self, user: User) -> Self:
        """Purchases by `user` that have not been sold (they are not the previous_trade of a sale)."""
        selling = self.model.TradeChoices.SELLING
        sold_ids = self.model.objects.filter(
            collector=user, trade_type=selling, previous_trade__isnull=False
        ).values_list("previous_trade_id", flat=True)
        return self.filter(collector=user).exclude(Q(trade_type=selling) | Q(id__in=sold_ids))


class Collection(Model):
    class TradeChoices(TextChoices):
        BUYING = "buying", _("Buying")
        SELLING = "selling", _("Selling")

    collector = ForeignKey(User, on_delete=PROTECT)
    edition = ForeignKey(Edition, on_delete=PROTECT)
    amount = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    trade_date = DateField(default=datetime.now)
    trade_type = CharField(max_length=50, choices=TradeChoices.choices, default=TradeChoices.BUYING)
    participant = ForeignKey(Dealer, on_delete=PROTECT, null=True, blank=True)
    valuation = DecimalField(max_digits=4, decimal_places=2, default=0.00)
    signatures = ManyToManyField(Artist, blank=True, through="Signature")
    previous_trade = ForeignKey("self", on_delete=SET_NULL, null=True, blank=True, related_name="next_trades")
    notes = TextField(max_length=500, blank=True)

    objects = CollectionQuerySet.as_manager()

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["edition", "trade_date", "amount", "trade_type", "participant"],
                name="unique_collection_edition_date_amount_type_participant",
            ),
            # MySQL does not allow a CHECK constraint to reference an
            # auto-increment column (error 3818), so "cannot be its own
            # previous_trade" can only be validated in Python (see
            # validate_previous_trade), not at the database level.
        ]

    def __str__(self) -> str:
        return self.edition.__str__()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.validate_previous_trade()
        self.validate_duplicate()

        super().save(*args, **kwargs)

    def validate_previous_trade(self) -> None:
        if self.pk and self.previous_trade_id == self.pk:
            raise ValidationError("A collection can't be its own previous trade.")

    def validate_duplicate(self) -> None:
        coincidences = (
            Collection.objects.filter(
                edition__publishing__publishing_title__exact=self.edition.publishing.publishing_title
            )
            .filter(trade_date=self.trade_date)
            .filter(amount=self.amount)
            .filter(trade_type=self.trade_type)
            .filter(participant=self.participant)
        )

        if hasattr(self, "id"):
            coincidences = coincidences.exclude(id=self.id)

        current_edition_str = str(self.edition).strip()
        for collectable in coincidences:
            if str(collectable.edition).strip() == current_edition_str:
                raise ValidationError("Duplicated collectable")


class Signature(Model):
    artist = ForeignKey(Artist, on_delete=PROTECT)
    collectable = ForeignKey(Collection, on_delete=PROTECT)
    date = DateField(default=datetime.now)
    price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    has_coa = BooleanField(default=False)

    def __str__(self) -> str:
        return (
            self.collectable.collector.__str__()
            + " - "
            + self.collectable.edition.__str__()
            + " - "
            + self.artist.__str__()
        )


class ReadingArc(Model):
    """An ordered sequence of issues to read, possibly across different titles and publishings."""

    name = CharField(max_length=100, unique=True)
    notes = TextField(max_length=500, blank=True)
    issues = ManyToManyField(Issue, through="ReadingArcEntry", related_name="reading_arcs")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def clean_issue_ids(self, issue_ids: Iterable[Any]) -> list[int]:
        """Validate an ordered list of issue ids and return it as ints."""
        try:
            cleaned = [int(issue_id) for issue_id in issue_ids]
        except (TypeError, ValueError) as err:
            raise ValidationError(gettext("The list of issues is not valid.")) from err
        errors = []
        if len(cleaned) != len(set(cleaned)):
            errors.append(gettext("An issue is repeated in the reading arc."))
        if Issue.objects.filter(pk__in=cleaned).count() != len(set(cleaned)):
            errors.append(gettext("One of the issues no longer exists."))
        if errors:
            raise ValidationError(errors)
        return cleaned

    def set_issues(self, issue_ids: Iterable[int]) -> None:
        """Replace all entries. They are deleted and inserted again because updating them
        one by one violates the UniqueConstraints when entries are reordered."""
        self.entries.all().delete()
        ReadingArcEntry.objects.bulk_create(
            ReadingArcEntry(arc=self, issue_id=issue_id, order=order)
            for order, issue_id in enumerate(issue_ids, start=1)
        )

    def entries_with_covers(self) -> list[ReadingArcEntry]:
        """Entries in reading order, each with the cover of its earliest edition (or None)."""
        entries = list(self.entries.select_related("issue__publishing"))
        covers = {}
        editions = Edition.objects.filter(issue_id__in=[entry.issue_id for entry in entries]).exclude(thumbnail="")
        for edition in editions.order_by("release_date", "id"):
            covers.setdefault(edition.issue_id, edition.thumbnail.url)
        for entry in entries:
            entry.cover_url = covers.get(entry.issue_id)
        return entries

    def entries_with_ownership(self, user: User) -> list[ReadingArcEntry]:
        """entries_with_covers() flagged with whether `user` owns each issue.
        An issue counts as owned if the user owns any edition of it (any variant, printing
        or country) or a compilation that collects it."""
        entries = self.entries_with_covers()
        issue_ids = [entry.issue_id for entry in entries]
        owned = Collection.objects.owned_by(user)
        direct = set(owned.filter(edition__issue_id__in=issue_ids).values_list("edition__issue_id", flat=True))
        collected = set(
            owned.filter(edition__collected_entries__issue_id__in=issue_ids).values_list(
                "edition__collected_entries__issue_id", flat=True
            )
        )
        for entry in entries:
            entry.owned = entry.issue_id in direct or entry.issue_id in collected
            entry.owned_in_compilation = entry.issue_id not in direct and entry.issue_id in collected
        return entries


class ReadingArcEntry(Model):
    arc = ForeignKey(ReadingArc, on_delete=CASCADE, related_name="entries")
    issue = ForeignKey(Issue, on_delete=PROTECT, related_name="arc_entries")
    order = PositiveSmallIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["order"]
        constraints = [
            UniqueConstraint(fields=["arc", "issue"], name="unique_reading_arc_issue"),
            UniqueConstraint(fields=["arc", "order"], name="unique_reading_arc_order"),
        ]

    def __str__(self) -> str:
        return f"{self.arc} #{self.order}: {self.issue}"


class Connecting(Model):
    """A group of covers that together form a larger image."""

    name = CharField(max_length=100, unique=True)
    rows = PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    columns = PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    notes = TextField(max_length=500, blank=True)
    editions = ManyToManyField(Edition, through="ConnectingPiece", related_name="connectings")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def layout(self) -> str:
        return f"{self.rows}×{self.columns}"

    def clean_placements(self, placements: Iterable[Any]) -> list[tuple[int, int, int]]:
        """Validate [{"row", "column", "edition"}, ...] against the grid and return (row, column, edition_id) tuples."""
        errors = []
        cleaned = []
        for index, placement in enumerate(placements, start=1):
            try:
                row, column, edition_id = (int(placement[key]) for key in ("row", "column", "edition"))
            except (KeyError, TypeError, ValueError):
                errors.append(gettext("Piece %(index)s is not valid.") % {"index": index})
                continue
            if not (1 <= row <= self.rows and 1 <= column <= self.columns):
                errors.append(
                    gettext("Position (%(row)s, %(column)s) is outside the %(layout)s grid.")
                    % {"row": row, "column": column, "layout": self.layout}
                )
                continue
            cleaned.append((row, column, edition_id))

        positions = [(row, column) for row, column, _ in cleaned]
        if len(positions) != len(set(positions)):
            errors.append(gettext("Two pieces are in the same position."))
        edition_ids = [edition_id for _, _, edition_id in cleaned]
        if len(edition_ids) != len(set(edition_ids)):
            errors.append(gettext("A comic cannot appear twice in the same connecting."))
        if Edition.objects.filter(id__in=edition_ids).count() != len(set(edition_ids)):
            errors.append(gettext("One of the comics no longer exists."))

        if errors:
            raise ValidationError(errors)
        return cleaned

    def set_pieces(self, cleaned_placements: Iterable[tuple[int, int, int]]) -> None:
        """Replace all pieces. They are deleted and inserted again because updating
        them one by one violates the UniqueConstraints when pieces are swapped."""
        self.pieces.all().delete()
        ConnectingPiece.objects.bulk_create(
            ConnectingPiece(connecting=self, row=row, column=column, edition_id=edition_id)
            for row, column, edition_id in cleaned_placements
        )

    def grid(self) -> list[list[ConnectingPiece | None]]:
        """rows x columns matrix with the piece at each position (or None)."""
        by_position = {(piece.row, piece.column): piece for piece in self.pieces.select_related("edition__publishing")}
        return [
            [by_position.get((row, column)) for column in range(1, self.columns + 1)] for row in range(1, self.rows + 1)
        ]

    def ownership_grid(self, user: User) -> list[list[ConnectingPiece | None]]:
        """grid() with each piece flagged with whether `user` owns its edition."""
        grid = self.grid()
        pieces = [piece for row in grid for piece in row if piece]
        owned_edition_ids = set(
            Collection.objects.owned_by(user)
            .filter(edition_id__in=[piece.edition_id for piece in pieces])
            .values_list("edition_id", flat=True)
        )
        for piece in pieces:
            piece.owned = piece.edition_id in owned_edition_ids
        return grid


class ConnectingPiece(Model):
    connecting = ForeignKey(Connecting, on_delete=CASCADE, related_name="pieces")
    edition = ForeignKey(Edition, on_delete=PROTECT, related_name="connecting_pieces")
    row = PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)], help_text="1 = fila de arriba")
    column = PositiveSmallIntegerField(validators=[MinValueValidator(1)], help_text="1 = columna izquierda")

    class Meta:
        ordering = ["row", "column"]
        constraints = [
            UniqueConstraint(fields=["connecting", "row", "column"], name="unique_connecting_position"),
            UniqueConstraint(fields=["connecting", "edition"], name="unique_connecting_edition"),
        ]

    def __str__(self) -> str:
        return f"{self.connecting} ({self.row}, {self.column})"

    def clean(self) -> None:
        # In the admin, when a new connecting is created, the piece already has its
        # (unsaved) parent with its rows/columns, so it can still be validated.
        try:
            connecting = self.connecting
        except Connecting.DoesNotExist:
            return
        errors = {}
        if self.row and self.row > connecting.rows:
            errors["row"] = ngettext(
                "The connecting only has %(count)d row.", "The connecting only has %(count)d rows.", connecting.rows
            ) % {"count": connecting.rows}
        if self.column and self.column > connecting.columns:
            errors["column"] = ngettext(
                "The connecting only has %(count)d column.",
                "The connecting only has %(count)d columns.",
                connecting.columns,
            ) % {"count": connecting.columns}
        if errors:
            raise ValidationError(errors)


class GeekCollectable(Model):
    name = CharField(max_length=100)
    description = TextField(max_length=500, blank=True)
    amount = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    trade_date = DateField(default=datetime.now)
    participant = ForeignKey(Dealer, on_delete=PROTECT, default=1)
    image = ImageField(upload_to="collectables/", null=True, blank=True)

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.process_image()

        super().save(*args, **kwargs)

    def process_image(self) -> None:
        """Convert a newly uploaded image to JPEG. An image that did not change was already converted
        when it was uploaded, so it is left as is (same rule as Edition.process_image)."""
        if self.pk:
            old_image = GeekCollectable.objects.filter(pk=self.pk).values_list("image", flat=True).first()
            if self.image == old_image:
                return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.name}_{timestamp}".replace(" ", "_")
        self.image = generate_image_jpeg(base_name, self.image)
