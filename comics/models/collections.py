"""What collectors own and follow: purchases and sales, signatures, reading arcs, connectings, collectables,
hunted editions."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, ClassVar, Self

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import transaction
from django.db.models import (
    CASCADE,
    PROTECT,
    BooleanField,
    CharField,
    Count,
    DateField,
    DateTimeField,
    DecimalField,
    Exists,
    ForeignKey,
    ImageField,
    ManyToManyField,
    Model,
    OuterRef,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    Subquery,
    TextChoices,
    TextField,
    UniqueConstraint,
)
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.translation import gettext, ngettext, pgettext_lazy
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


class TradeStatus(TextChoices):
    """Where a piece stands, as the Gestión lists filter it (TradeQuerySet.with_status)."""

    OWNED = "owned", _("In collection")
    SOLD = "sold", _("Sold")
    UNLINKED = "unlinked", _("Sale without linked purchase")


class TradeQuerySet(QuerySet):
    """Purchases and sales of a collector (Collection for editions, Collectable for articles). The
    model needs a `TradeChoices` with BUYING/SELLING, a `collector` and a `previous_trade`."""

    def owned_by(self, user: User) -> Self:
        """Purchases by `user` that have not been sold (they are not the previous_trade of a sale)."""
        selling = self.model.TradeChoices.SELLING
        sold_ids = self.model.objects.filter(
            collector=user, trade_type=selling, previous_trade__isnull=False
        ).values_list("previous_trade_id", flat=True)
        return self.filter(collector=user).exclude(Q(trade_type=selling) | Q(id__in=sold_ids))

    def sellable_by(
        self, collector: User, before: date | None = None, current: Collection | Collectable | None = None
    ) -> Self:
        """Purchases in this queryset that `collector` still owns, made on or before `before`: the ones a
        sale can point to as its previous trade. `current` (the sale being edited) keeps the purchase it
        already points to, as long as it belongs to `collector`, and is never listed itself."""
        purchases = self.owned_by(collector).filter(trade_type=self.model.TradeChoices.BUYING)
        if current is not None and current.previous_trade_id:
            purchases = purchases | self.filter(pk=current.previous_trade_id, collector=collector)
        if before:
            purchases = purchases.filter(trade_date__lte=before)
        if current is not None and current.pk:
            purchases = purchases.exclude(pk=current.pk)
        return purchases.order_by("trade_date", "id")

    def _sales_of_outer_row(self) -> QuerySet:
        """Sales of the same collector that point to the outer row as their previous trade, for an
        Exists() over each row of this queryset."""
        return self.model.objects.filter(
            previous_trade=OuterRef("pk"),
            collector=OuterRef("collector"),
            trade_type=self.model.TradeChoices.SELLING,
        )

    def with_sold_flag(self) -> Self:
        """Annotate `is_sold`: whether a sale of the same collector points to the piece as its previous
        trade. Only a purchase can be sold (validate_previous_trade), so a sale is never flagged."""
        return self.annotate(is_sold=Exists(self._sales_of_outer_row()))

    def with_status(self, status: str) -> Self:
        """Keep the pieces in a TradeStatus: purchases still owned, purchases sold (same rule as
        with_sold_flag, checked per row, so it works across collectors) or sales that point to no
        purchase. Any other value, empty included, leaves the queryset as it is."""
        choices = self.model.TradeChoices
        sold = Exists(self._sales_of_outer_row())
        if status == TradeStatus.OWNED:
            return self.filter(~sold, trade_type=choices.BUYING)
        if status == TradeStatus.SOLD:
            return self.filter(sold, trade_type=choices.BUYING)
        if status == TradeStatus.UNLINKED:
            return self.filter(trade_type=choices.SELLING, previous_trade__isnull=True)
        return self

    def with_sale_date(self) -> Self:
        """Annotate `sold_on`: the date of the earliest trade that points to the piece as its previous
        trade (its sale), or None. Such a piece cannot be deleted (previous_trade is PROTECT)."""
        sales = self.model.objects.filter(previous_trade=OuterRef("pk")).order_by("trade_date", "pk")
        return self.annotate(sold_on=Subquery(sales.values("trade_date")[:1]))


class CollectionQuerySet(TradeQuerySet):
    """Trades of editions (Collection)."""


class PreviousTradeRules:
    """Rules of `previous_trade`, shared by Collection (editions) and Collectable (articles): only a sale
    has a previous trade, and it is another piece, a purchase of the same item (`item_field`) by the
    same collector that no other sale has sold yet. MySQL cannot check this with a CHECK constraint
    (see Collection.Meta). Checked in clean() (forms, admin) and again in save(), so code paths that
    skip form validation cannot link a wrong previous trade."""

    item_field: ClassVar[str]  # the traded item: "edition" or "article"
    wrong_purchase_message: ClassVar[str | Promise]  # the previous trade is not a purchase of that item

    def save(self, *args: Any, **kwargs: Any) -> None:
        # One transaction (a savepoint inside the caller's), so the purchase locked by
        # validate_previous_trade stays locked until this sale is written.
        with transaction.atomic():
            self.validate_previous_trade(lock=True)
            super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self.validate_previous_trade()

    def validate_previous_trade(self, *, lock: bool = False) -> None:
        """Raise a ValidationError on `previous_trade` if it breaks the rules. With `lock` (on save,
        inside a transaction) the purchase row is locked first (SELECT ... FOR UPDATE): two sales of the
        same purchase saved at the same time are then checked one after the other, and the second one
        sees the first (locking reads always read the latest committed rows)."""
        if self.previous_trade_id is None:
            return
        if self.pk and self.previous_trade_id == self.pk:
            raise ValidationError({"previous_trade": _("A piece can't be its own previous trade.")})
        if self.trade_type != self.TradeChoices.SELLING:
            raise ValidationError({"previous_trade": _("Only a sale can have a previous trade.")})
        manager = type(self)._default_manager
        purchases = manager.filter(pk=self.previous_trade_id)
        if lock:
            purchases = purchases.select_for_update()
        previous = purchases.first()
        item_id = f"{self.item_field}_id"
        if (
            previous is None
            or previous.trade_type != self.TradeChoices.BUYING
            or previous.collector_id != self.collector_id
            or getattr(previous, item_id) != getattr(self, item_id)
        ):
            raise ValidationError({"previous_trade": self.wrong_purchase_message})
        other_sales = manager.filter(previous_trade_id=self.previous_trade_id, trade_type=self.TradeChoices.SELLING)
        if self.pk:
            other_sales = other_sales.exclude(pk=self.pk)
        if lock:
            other_sales = other_sales.select_for_update()
        if other_sales.exists():
            raise ValidationError({"previous_trade": _("This purchase has already been sold.")})


class Collection(PreviousTradeRules, Model):
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
    # PROTECT: a purchase that has been sold cannot be deleted while its sale points to it.
    previous_trade = ForeignKey("self", on_delete=PROTECT, null=True, blank=True, related_name="next_trades")
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

    item_field = "edition"
    wrong_purchase_message = _("The previous trade must be a purchase of the same edition by the same collector.")

    def __str__(self) -> str:
        return self.edition.__str__()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.validate_duplicate()

        # PreviousTradeRules.save validates the previous trade too.
        super().save(*args, **kwargs)

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


class Article(Model):
    """A collectable object of the shared catalogue (figure, print, pin...), the way Edition is for
    comics; what each collector bought or sold of it is a Collectable."""

    class CategoryChoices(TextChoices):
        FIGURE = "figure", _("Figure")
        PRINT = "print", _("Print")
        PIN = "pin", _("Pin")
        CARD = "card", _("Card")
        POSTER = "poster", _("Poster")
        VIDEO = "video", _("Video")
        # "Other" alone is already translated as a plural ("Otros") elsewhere.
        OTHER = "other", pgettext_lazy("article category", "Other")

    name = CharField(max_length=100)
    description = TextField(max_length=500, blank=True)
    category = CharField(max_length=20, choices=CategoryChoices, default=CategoryChoices.OTHER)
    limited_to = PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Print run, when the article is limited."),
    )
    image = ImageField(upload_to="articles/", null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.process_image()

        super().save(*args, **kwargs)

    def process_image(self) -> None:
        """Convert a newly uploaded image to JPEG. An image that did not change was already converted
        when it was uploaded, so it is left as is (same rule as Edition.process_image)."""
        if self.pk:
            old_image = Article.objects.filter(pk=self.pk).values_list("image", flat=True).first()
            if self.image == old_image:
                return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.name}_{timestamp}".replace(" ", "_")
        self.image = generate_image_jpeg(base_name, self.image)


class CollectableQuerySet(TradeQuerySet):
    """Trades of articles (Collectable)."""

    def count_by_article(self) -> dict[int, int]:
        """{article_id: number of pieces} in this queryset, in one grouped query."""
        grouped = self.order_by().values("article_id").annotate(pieces=Count("id"))
        return {row["article_id"]: row["pieces"] for row in grouped}


class Collectable(PreviousTradeRules, Model):
    """A collector's purchase or sale of an Article, the way Collection is for editions. A sale points
    to the purchase it sells through previous_trade."""

    TradeChoices = Collection.TradeChoices

    collector = ForeignKey(User, on_delete=PROTECT)
    article = ForeignKey(Article, on_delete=PROTECT, related_name="pieces")
    amount = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    trade_date = DateField(default=datetime.now)
    trade_type = CharField(max_length=50, choices=TradeChoices.choices, default=TradeChoices.BUYING)
    participant = ForeignKey(Dealer, on_delete=PROTECT, null=True, blank=True)
    valuation = DecimalField(max_digits=4, decimal_places=2, default=0.00)
    # PROTECT: a purchase that has been sold cannot be deleted while its sale points to it.
    previous_trade = ForeignKey("self", on_delete=PROTECT, null=True, blank=True, related_name="next_trades")
    notes = TextField(max_length=500, blank=True)

    objects = CollectableQuerySet.as_manager()

    class Meta:
        constraints = [
            # A NULL participant counts as distinct in MySQL, so pieces without one are not covered.
            UniqueConstraint(
                fields=["collector", "article", "trade_date", "amount", "trade_type", "participant"],
                name="unique_collectable_per_collector_trade",
            ),
        ]

    item_field = "article"
    wrong_purchase_message = _("The previous trade must be a purchase of the same article by the same collector.")

    def __str__(self) -> str:
        return str(self.article)


class WantedEditionQuerySet(QuerySet):
    """Hunt marks (WantedEdition). An *active* mark (no `completed_at`) is what "hunting an edition"
    means everywhere; a completed one only keeps the note so the hunt can be brought back."""

    def active(self) -> Self:
        return self.filter(completed_at__isnull=True)

    def completed(self) -> Self:
        return self.filter(completed_at__isnull=False)

    def for_collector(self, user: User) -> Self:
        """The active marks of `user`, with everything Edition.__str__ reads loaded: each edition, its
        publishing and the publishing's editorials (one extra query for the editorials of all rows)."""
        return (
            self.active()
            .filter(collector=user)
            .select_related("edition__publishing")
            .prefetch_related("edition__publishing__editorials")
        )

    def edition_ids_wanted_by(self, user: User, edition_ids: Iterable[int]) -> set[int]:
        """The ids among `edition_ids` that `user` is hunting (active marks), in one query."""
        return set(
            self.active().filter(collector=user, edition_id__in=list(edition_ids)).values_list("edition_id", flat=True)
        )

    def set_wanted(self, user: User, edition: Edition, wanted: bool) -> None:
        """Make `user` hunt `edition` or stop hunting it. Idempotent: marking a marked edition keeps
        its single mark (and its note), unmarking an unmarked one does nothing.

        Marking reactivates a mark completed by a purchase, keeping its note and creation time (the
        update only applies to the row as it was read, completed). get_or_create retries the read when
        a concurrent request inserted the same mark first, so the unique constraint never surfaces as
        an error. Unmarking deletes only an active mark: a completed one keeps the note a later
        resume_hunt restores, so a repeated or forged unmark request cannot destroy it."""
        if wanted:
            mark, created = self.get_or_create(collector=user, edition=edition)
            if not created and mark.completed_at is not None:
                self.filter(pk=mark.pk, completed_at=mark.completed_at).update(completed_at=None)
        else:
            self.active().filter(collector=user, edition=edition).delete()

    def resume_hunt(self, user: User, edition: Edition) -> bool:
        """Bring back the hunt of `edition` that a purchase by `user` completed: their existing mark is
        active again, with its note and creation time. Unlike marking, it never creates a mark: when
        they have none any more (they stopped hunting the edition after bringing it back) there is
        nothing to bring back and nothing changes. Returns whether `user` hunts `edition` afterwards;
        repeating it changes nothing. A single UPDATE (MySQL reports the matched rows, so a mark that
        is already active counts too), so a mark deleted meanwhile is never recreated without its note."""
        return bool(self.filter(collector=user, edition=edition).update(completed_at=None))

    def complete_hunt(self, collector_id: int, edition_id: int) -> bool:
        """Complete the active mark of the collector for the edition (a purchase of it), keeping the
        row and its note. Returns whether there was one. A single UPDATE: of two concurrent purchases
        only one completes the mark."""
        return bool(
            self.active().filter(collector_id=collector_id, edition_id=edition_id).update(completed_at=timezone.now())
        )


class WantedEdition(Model):
    """An edition a collector is hunting (looking for). Not a trade: owning the edition does not
    prevent hunting it (a second or better copy). Registering a purchase of it completes the hunt
    (see comics.signals.complete_hunt_on_purchase): the mark is kept with `completed_at` set, so the
    collector can bring the hunt back, with its note, from the notification they get."""

    collector = ForeignKey(User, on_delete=CASCADE, related_name="wanted_editions")
    # CASCADE, unlike the PROTECT of the trades: a hunt mark must never block deleting an edition.
    edition = ForeignKey(Edition, on_delete=CASCADE, related_name="wanted_by")
    created_at = DateTimeField(auto_now_add=True)
    # What the collector is after, e.g. the better condition sought when replacing an owned copy.
    notes = TextField(max_length=500, blank=True)
    # When a purchase completed the hunt; NULL while the collector is hunting the edition.
    completed_at = DateTimeField(null=True, blank=True)

    objects = WantedEditionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            UniqueConstraint(fields=["collector", "edition"], name="unique_wanted_edition_per_collector"),
        ]

    def __str__(self) -> str:
        return f"{self.collector} - {self.edition}"

    def save_note(self) -> bool:
        """Write only `notes`, and only while the mark is still active; returns whether it was. Saving
        the whole row would also write `completed_at` as it was read, undoing a purchase that completed
        the mark in the meantime."""
        return bool(WantedEdition.objects.active().filter(pk=self.pk).update(notes=self.notes))
