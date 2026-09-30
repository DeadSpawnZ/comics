from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Self

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BooleanField,
    CharField,
    DateField,
    DecimalField,
    F,
    ForeignKey,
    ImageField,
    IntegerField,
    ManyToManyField,
    Min,
    Model,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
    UniqueConstraint,
)
from django.utils.functional import Promise
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _
from PIL import Image

from .helper import generate_image_jpeg

logger = logging.getLogger(__name__)

# Create your models here.


class Editorial(Model):
    class CountryAbbr(TextChoices):
        MX = "MX"
        US = "US"
        DE = "DE"
        ES = "ES"
        JP = "JP"

    name = CharField(max_length=30, unique=True)
    country = CharField(max_length=3, choices=CountryAbbr)
    # titles = ManyToManyField(Person, through="Membership")

    def __str__(self) -> str:
        return self.name


class Title(Model):
    name = CharField(max_length=100, unique=True)

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.process_name()
        super().save(*args, **kwargs)

    def process_name(self) -> None:
        self.name = self.name.strip()


def current_year() -> int:
    return datetime.now().year


class Publishing(Model):
    # Validator of `year`. It must be a plain function defined in the class body before that field,
    # and keep its name and signature: migrations reference it as
    # comics.models.Publishing.max_value_current_year. Being a method above the fields is what the
    # DJ012 (model member order) and N805 (no `self`) noqa markers in this class are about.
    def max_value_current_year(value: int) -> None:  # noqa: N805
        return MaxValueValidator(current_year())(value)

    class LangAbbr(TextChoices):
        EN = "en", _("English")
        ES = "es", _("Spanish")
        DE = "de", _("German")

    title = ForeignKey(Title, on_delete=PROTECT, null=True)  # noqa: DJ012
    publishing_title = CharField(max_length=100)
    serie = CharField(max_length=20, default="1st")
    language = CharField(max_length=5, choices=LangAbbr.choices, default=LangAbbr.EN)
    editorials = ManyToManyField(Editorial)
    date = DateField(default=datetime.now)
    year = IntegerField(
        _("year"),
        validators=[MinValueValidator(1960), max_value_current_year],
        blank=True,
        null=True,
    )

    class Meta:  # noqa: DJ012
        constraints = [
            UniqueConstraint(
                fields=["publishing_title", "year", "serie", "language"],
                name="unique_publishing_title_year_serie_language",
            ),
        ]

    def __str__(self) -> str:  # noqa: DJ012
        editorials = Editorial.objects.filter(publishing=self)
        editorials = [editorial.name for editorial in editorials]
        return (
            str(self.publishing_title)
            + " ("
            + str(self.year)
            + ") "
            + self.serie
            + " Series "
            + " "
            + "["
            + "/".join(editorials)
            + "]"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: DJ012
        self.process_year()
        self.process_publishing_title()

        self.validate_duplicates()
        super().save(*args, **kwargs)

    def process_year(self) -> None:
        if self.date and self.year is None:
            self.year = self.date.year

    def process_publishing_title(self) -> None:
        self.publishing_title = self.publishing_title.strip()

    def validate_duplicates(self) -> None:
        coincidences = (
            Publishing.objects.filter(publishing_title=self.publishing_title)
            .filter(year=self.year)
            .filter(serie=self.serie)
            .filter(language=self.language)
        )

        if hasattr(self, "id"):
            coincidences = coincidences.exclude(id=self.id)
        if coincidences.exists():
            raise ValidationError("Duplicated publishing")


class Artist(Model):
    name = CharField(max_length=100, unique=True)
    photo = ImageField(upload_to="artists/photos/", null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class IssueQuerySet(QuerySet):
    def delete_orphans(self) -> tuple[int, dict[str, int]]:
        """Delete the issues in this queryset that no longer have editions, are not in a
        compilation and are not part of any reading arc."""
        return self.filter(editions__isnull=True, collected_in__isnull=True, arc_entries__isnull=True).delete()

    def with_first_release(self) -> Self:
        """Annotate `first_release`: the earliest date among its 1st-printing editions
        (the A cover and its variants) within its original series."""
        return self.annotate(
            first_release=Min(
                "editions__release_date",
                filter=Q(editions__printing=Edition.PrintingChoices.FIRST, editions__publishing=F("publishing")),
            )
        )


class Issue(Model):
    """The content (the story) of an issue. Its editions are the physical printings:
    variants, reprints, foreign or anniversary editions, which may belong to another publishing."""

    publishing = ForeignKey(Publishing, on_delete=PROTECT, related_name="issues", help_text="Serie original")
    number = CharField(max_length=5)
    synopsis = TextField(blank=True)
    creators = ManyToManyField(Artist, blank=True, related_name="issues")

    objects = IssueQuerySet.as_manager()

    class Meta:
        ordering = ["publishing__publishing_title", "number"]
        constraints = [
            UniqueConstraint(fields=["publishing", "number"], name="unique_issue_publishing_number"),
        ]

    def __str__(self) -> str:
        return f"{self.publishing.publishing_title} #{self.number}"


class Edition(Model):
    """The physical copy (what is colloquially called a "comic"): cover/variant, printing,
    format. It belongs to a publishing and points to the issue it contains, or to several if it is a compilation."""

    class FormatChoices(TextChoices):
        SINGLE_ISSUE = "single_issue", _("Single issue")
        PRESTIGE = "prestige", _("Prestige")
        TRADE_PAPERBACK = "trade_paperback", _("TPB - Trade Paperback")
        HARDCOVER = "hardcover", _("HC - Hardcover")
        ASHCAN = "ashcan", _("Ashcan")
        DIGITAL = "digital", _("Digital")
        MAGAZINE = "magazine", _("Magazine")

    class PrintingChoices(TextChoices):
        FIRST = "1st", _("1st")
        SECOND = "2nd", _("2nd")
        THIRD = "3rd", _("3rd")
        FOURTH = "4th", _("4th")
        FIFTH = "5th", _("5th")
        SIXTH = "6th", _("6th")

    ratio_validator = [
        RegexValidator(
            regex="^[0-9]{1,3}+:[0-9]{1,3}$",
            message="Ratio must be a valid relation Example: (1:100)",
            code="invalid_ratio",
        ),
    ]
    limited_to_validator = [
        RegexValidator(
            regex="^[0-9]{0,10}+$",
            message="Not a valid number",
            code="invalid_limit",
        ),
    ]
    some_number = RegexValidator(regex=r"\d+", message="Debe contener al menos un número")

    publishing = ForeignKey(Publishing, on_delete=PROTECT, blank=True)
    number = CharField(max_length=5, validators=[some_number])
    variant = CharField(max_length=30, default="A", blank=True)
    printing = CharField(max_length=10, choices=PrintingChoices.choices, default=PrintingChoices.FIRST)
    ratio = CharField(max_length=10, blank=True, validators=ratio_validator)
    limited_to = CharField(max_length=10, blank=True, validators=limited_to_validator)
    retailer_exclusive = CharField(
        max_length=60,
        blank=True,
        help_text="Store the cover is exclusive to (retailer exclusive). Empty for regular and incentive covers.",
    )
    event_exclusive = CharField(
        max_length=60,
        blank=True,
        help_text="Event the cover is exclusive to (e.g. SDCC 2025). It can also have a store and a variant.",
    )
    cover_price = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    format = CharField(max_length=20, choices=FormatChoices, default=FormatChoices.SINGLE_ISSUE)
    release_date = DateField(default=datetime.now)
    image = ImageField(upload_to="images/originals/", null=True, blank=True)
    thumbnail = ImageField(upload_to="images/thumbnails/", null=True, blank=True)
    notes = TextField(max_length=500, blank=True)
    cover_artists = ManyToManyField(Artist, blank=True, related_name="covers")
    issue = ForeignKey(
        Issue,
        on_delete=PROTECT,
        null=True,
        blank=True,
        related_name="editions",
        help_text="Se asigna solo segun publishing y numero. Cambialo si es una edicion "
        "extranjera o de aniversario de un issue de otra serie. Vacio en compilaciones.",
    )
    collected_issues = ManyToManyField(Issue, through="CollectedIssue", blank=True, related_name="collected_in")

    class Meta:
        constraints = [
            UniqueConstraint(
                # Event and store are part of the identity: two exclusives may share the variant letter (or have none).
                fields=["publishing", "number", "variant", "printing", "retailer_exclusive", "event_exclusive"],
                name="unique_edition_publishing_number_variant_printing_exclusives",
            ),
        ]

    def __str__(self) -> str:
        # Query through the relation (instead of Editorial.objects.filter(...))
        # so a prefetch_related("publishing__editorials") done by the caller
        # is reused, avoiding N+1 when listing many comics.
        editorials = self.publishing.editorials.all()
        first_editorial = editorials[0] if editorials else None
        country_code = first_editorial.country.upper() if first_editorial else ""

        comic_name = (
            f"{self.publishing.publishing_title} #{self.number} {self.variant_label} {self.publishing.serie} "
            f"{self.printing} {country_code}-{self.publishing.language.upper()} {self.publishing.year}"
        )
        if self.is_compilation:
            comic_name += " [Compilation]"

        return comic_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.process_variant()
        self.validate()
        self.process_image()
        old_issue_id = (
            Edition.objects.filter(pk=self.pk).values_list("issue_id", flat=True).first() if self.pk else None
        )
        self.assign_default_issue()

        super().save(*args, **kwargs)

        # Issue change (automatic or manual link): the previous one is deleted if left without editions.
        if old_issue_id and old_issue_id != self.issue_id:
            Issue.objects.filter(pk=old_issue_id).delete_orphans()

    COVER_KINDS = (
        ("regular", _("Regular covers")),
        ("incentive", _("Incentive covers")),
        ("retailer_exclusive", _("Retailer exclusives")),
        ("event_exclusive", _("Event exclusives")),
    )

    @property
    def variant_label(self) -> str:
        """How the cover is named: "event store variant", leaving out the empty parts
        (e.g. "SDCC 2025 Unknown Comics B")."""
        parts = (self.event_exclusive, self.retailer_exclusive, self.variant)
        return " ".join(part.strip() for part in parts if part and part.strip())

    @property
    def short_name(self) -> str:
        """Title, number and cover, e.g. "Spawn #1 SDCC 2025 B"."""
        return f"{self.publishing.publishing_title} #{self.number} {self.variant_label}".strip()

    @property
    def cover_kind(self) -> str:
        """Event exclusive if an event is set (even with a store); retailer exclusive if a store is
        set; incentive if it has a 1:N ratio; regular otherwise."""
        if self.event_exclusive.strip():
            return "event_exclusive"
        if self.retailer_exclusive.strip():
            return "retailer_exclusive"
        if self.ratio.strip():
            return "incentive"
        return "regular"

    @classmethod
    def group_by_cover_kind(cls, editions: Iterable[Edition]) -> list[tuple[str, Promise, list[Edition]]]:
        """[(kind, label, editions), ...] in COVER_KINDS order, skipping empty groups."""
        groups = {kind: [] for kind, _label in cls.COVER_KINDS}
        for edition in editions:
            groups[edition.cover_kind].append(edition)
        return [(kind, label, groups[kind]) for kind, label in cls.COVER_KINDS if groups[kind]]

    @property
    def is_compilation(self) -> bool:
        # Compilations have no issue of their own; the DB is only queried in that case.
        return self.issue_id is None and self.pk is not None and self.collected_entries.exists()

    def sync_compilation_state(self) -> None:
        """After the collected issues are edited: if it is now a compilation it drops its issue
        (and deletes it if orphaned); if it is no longer one it gets its default issue back."""
        if self.collected_entries.exists():
            if self.issue_id is None:
                return
            previous_id = self.issue_id
            Edition.objects.filter(pk=self.pk).update(issue=None)
            self.issue = None
            Issue.objects.filter(pk=previous_id).delete_orphans()
        elif self.issue_id is None:
            self.issue, _ = Issue.objects.get_or_create(publishing_id=self.publishing_id, number=self.number.strip())
            Edition.objects.filter(pk=self.pk).update(issue=self.issue)

    def validate_duplicate(self) -> None:
        coincidences = (
            Edition.objects.filter(publishing__publishing_title__exact=self.publishing.publishing_title)
            .filter(number=self.number)
            .filter(variant=self.variant)
            .filter(retailer_exclusive=self.retailer_exclusive)
            .filter(event_exclusive=self.event_exclusive)
            .filter(publishing__serie__exact=self.publishing.serie)
            .filter(printing=self.printing)
            .filter(publishing__year__exact=self.publishing.year)
        )

        if hasattr(self, "id"):
            coincidences = coincidences.exclude(id=self.id)

        current_publishing_str = str(self.publishing).strip()
        for edition in coincidences:
            if str(edition.publishing).strip() == current_publishing_str:
                raise ValidationError("Duplicated comic")

    def validate(self) -> None:
        self.validate_duplicate()

    def process_variant(self) -> None:
        self.variant = self.variant.upper().strip()
        self.retailer_exclusive = self.retailer_exclusive.strip()
        self.event_exclusive = self.event_exclusive.strip()

    def process_image(self) -> None:
        if self.pk:
            old_instance = Edition.objects.get(pk=self.pk)
            if self.image == old_instance.image:
                logger.debug("Imagen sin cambios en la edicion %s; no se reprocesa.", self.pk)
                return

        max_thumb_width = 1080
        max_thumb_height = 1920

        try:
            # Open the original image
            img = Image.open(self.image)
            img_format = img.format or "JPEG"

            # Build the base name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{self.publishing.publishing_title}_{self.number}_{self.variant}_{timestamp}".replace(" ", "_")

            # Convert RGBA to RGB if needed
            if img_format == "PNG" and img.mode in ("RGBA", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])  # alpha channel
                img = background
                img_format = "JPEG"
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # === Save the original image ===
            original_io = io.BytesIO()
            img.save(original_io, format="JPEG", quality=95)
            original_io.seek(0)

            original_filename = f"{base_name}.jpg"
            self.image = InMemoryUploadedFile(
                original_io,
                "ImageField",
                original_filename,
                "image/jpeg",
                original_io.getbuffer().nbytes,
                None,
            )

            # === Generate the thumbnail ===
            width, height = img.size
            if width > max_thumb_width or height > max_thumb_height:
                scale = min(max_thumb_width / width, max_thumb_height / height)
                thumb_size = (int(width * scale), int(height * scale))
                thumb_img = img.resize(thumb_size, Image.Resampling.LANCZOS)
            else:
                thumb_img = img.copy()

            thumb_io = io.BytesIO()
            thumb_img.save(thumb_io, format="JPEG", quality=80, optimize=True)
            thumb_io.seek(0)

            thumb_filename = f"{base_name}_thumb.jpg"
            self.thumbnail = InMemoryUploadedFile(
                thumb_io,
                "ImageField",
                thumb_filename,
                "image/jpeg",
                thumb_io.getbuffer().nbytes,
                None,
            )

        except Exception:
            logger.exception("Error procesando imagen y thumbnail de la edicion %s", self.pk)

    def assign_default_issue(self) -> None:
        """By default an edition belongs to the issue of its publishing and number, and follows it
        if the publishing or number is corrected. If it is manually linked to another issue
        (foreign or anniversary edition) or is a compilation, that is respected."""
        if self.pk and self.collected_entries.exists():
            return
        number = self.number.strip()
        if self.issue_id:
            if self.issue.publishing_id == self.publishing_id and self.issue.number == number:
                return
            old = Edition.objects.filter(pk=self.pk).values("publishing_id", "number").first() if self.pk else None
            follows_own_series = (
                old is not None
                and self.issue.publishing_id == old["publishing_id"]
                and self.issue.number == old["number"].strip()
            )
            if not follows_own_series:
                return
        self.issue, _ = Issue.objects.get_or_create(publishing_id=self.publishing_id, number=number)


class CollectedIssue(Model):
    """Issues contained in a compilation, in order."""

    edition = ForeignKey(Edition, on_delete=CASCADE, related_name="collected_entries")
    issue = ForeignKey(Issue, on_delete=PROTECT, related_name="collected_entries")
    order = PositiveSmallIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["order"]
        constraints = [
            UniqueConstraint(fields=["edition", "issue"], name="unique_collected_issue"),
            UniqueConstraint(fields=["edition", "order"], name="unique_collected_order"),
        ]

    def __str__(self) -> str:
        return f"{self.edition.short_name} #{self.order}: {self.issue}"


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
