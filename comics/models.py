from __future__ import annotations

import io
import logging
import re
import secrets
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Self

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator, URLValidator
from django.db import transaction
from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BooleanField,
    CharField,
    CheckConstraint,
    DateField,
    DateTimeField,
    DecimalField,
    F,
    ForeignKey,
    ImageField,
    Index,
    IntegerField,
    JSONField,
    Manager,
    ManyToManyField,
    Min,
    Model,
    OneToOneField,
    PositiveBigIntegerField,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
    TimeField,
    UniqueConstraint,
    URLField,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language, gettext, ngettext
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
import simple_history
from PIL import Image

from .achievements.windows import CalendarWindow, clear_unused, window_errors
from .helper import generate_image_jpeg, square_jpeg, square_png
from .history import TrackedHistory, record_change
from .roles import APPROVE_CHANGE_REQUESTS, VALIDATE_CHANGE_REQUESTS

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
    # Versions of every saved state, with its creators (see comics.history).
    history = TrackedHistory(m2m_fields=["creators"])

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

    class RarityChoices(TextChoices):
        COMMON = "common", _("Common")
        RARE = "rare", _("Rare")
        VERY_RARE = "very_rare", _("Very rare")
        LEGENDARY = "legendary", _("Legendary")

    # "Great rarity": what the Infinity Gauntlet achievement counts.
    GREAT_RARITY = (RarityChoices.VERY_RARE, RarityChoices.LEGENDARY)

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
    # How hard the edition is to find, set in Gestión (not computed from ratio or print run).
    rarity = CharField(max_length=20, choices=RarityChoices, default=RarityChoices.COMMON)
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
    # `history` (its versions) is registered after CollectedIssue, see below.

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

    def save(self, *args: Any, restoring: bool = False, **kwargs: Any) -> None:
        """`restoring`: the values come from a saved version (comics.versions.revert_to_version), so
        its image paths and its issue are kept as they were, not reprocessed or reassigned."""
        self.process_variant()
        self.validate()
        if not restoring:
            self.process_image()
        old_issue_id = (
            Edition.objects.filter(pk=self.pk).values_list("issue_id", flat=True).first() if self.pk else None
        )
        if not restoring:
            self.assign_default_issue()

        super().save(*args, **kwargs)

        # Issue change (automatic or manual link): the previous one is deleted if left without editions.
        if old_issue_id and old_issue_id != self.issue_id:
            Issue.objects.filter(pk=old_issue_id).delete_orphans()

    @classmethod
    def from_db(cls, db: str, field_names: Iterable[str], values: Iterable[Any]) -> Self:
        instance = super().from_db(db, field_names, values)
        # The rarity as loaded, to tell after a save whether it changed (see comics.signals).
        instance.loaded_rarity = instance.__dict__.get("rarity")
        return instance

    @property
    def is_notable_rarity(self) -> bool:
        """Rare or above: collectors see it with a badge (common editions show nothing)."""
        return self.rarity != self.RarityChoices.COMMON

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
            record_change(Edition, self.pk)  # update() saves no version by itself
            Issue.objects.filter(pk=previous_id).delete_orphans()
        elif self.issue_id is None:
            self.issue, _ = Issue.objects.get_or_create(publishing_id=self.publishing_id, number=self.number.strip())
            Edition.objects.filter(pk=self.pk).update(issue=self.issue)
            record_change(Edition, self.pk)

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
        """Convert a newly uploaded cover to JPEG and generate its thumbnail.

        Nothing is processed when the cover did not change (it was converted when it was uploaded)
        or when there is none. When the cover is removed, its thumbnail goes too: it is derived from
        the cover, and keeping it would go on showing the old cover wherever thumbnails are listed.
        Only the references are cleared; the files stay (versions may still point to them)."""
        old_instance = Edition.objects.filter(pk=self.pk).first() if self.pk else None
        if not self.image:
            if old_instance is not None and old_instance.image:
                self.thumbnail = None
            return
        if old_instance is not None and self.image == old_instance.image:
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


# Versions of every saved state of an edition, with its cover artists and collected issues (and their
# order); see comics.history. Registered here because the history of the collected issues needs
# their through model (CollectedIssue) to exist.
simple_history.register(Edition, records_class=TrackedHistory, m2m_fields=["cover_artists", "collected_issues"])


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


class AreaAccess(Model):
    """Holder of the permissions that open a whole area of the site instead of acting on one model.

    Django ties every permission to a content type, so area permissions need a model to hang from.
    It has no default add/change/delete/view permissions: it exists only so `comics.access_management`
    is declared in code, created by Django's own post_migrate handler on every database, and listed
    in the admin like any other permission. Its table stays empty; it is a real (managed) table so
    every model Django iterates (dumpdata, flush) exists in the database. See comics/roles.py for
    the groups that use these permissions."""

    class Meta:
        default_permissions = ()
        permissions = [("access_management", "Can access the management area (Gestión)")]
        verbose_name = "area access"
        verbose_name_plural = "area access"

    def __str__(self) -> str:
        return str(self._meta.verbose_name)


PHONE_ALLOWED = re.compile(r"^\+?[0-9 ().-]+$")


def validate_phone(value: str) -> None:
    """Light phone check (no library): digits with the usual separators, an optional leading +,
    and 7 to 15 digits (15 is the international maximum, E.164)."""
    digits = sum(char.isdigit() for char in value)
    if not PHONE_ALLOWED.match(value) or not 7 <= digits <= 15:
        raise ValidationError(_("Enter a valid phone number."), code="invalid_phone")


class PictureModel(Model):
    """Base for models with one uploaded picture that is re-encoded when it changes.

    Subclasses name the field (PICTURE_FIELD) and implement normalize_picture(). Saving re-encodes
    a new picture and, once the save is committed, deletes the file it replaced. A save whose
    update_fields leave the picture out does neither: a stale in-memory value must not be
    re-encoded nor replace (and delete) the picture stored now."""

    PICTURE_FIELD = ""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.PICTURE_FIELD not in update_fields:
            super().save(*args, **kwargs)
            return
        old_name = self._stored_picture()
        picture = getattr(self, self.PICTURE_FIELD)
        if picture and picture.name != old_name:
            setattr(self, self.PICTURE_FIELD, self.normalize_picture(picture))
        super().save(*args, **kwargs)
        picture = getattr(self, self.PICTURE_FIELD)
        if old_name and old_name != picture.name:
            storage = picture.storage
            transaction.on_commit(lambda: storage.delete(old_name))

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        raise NotImplementedError

    def remove_picture(self) -> None:
        """Drop the picture (save() deletes the file once committed)."""
        if getattr(self, self.PICTURE_FIELD):
            setattr(self, self.PICTURE_FIELD, "")
            self.save(update_fields=[self.PICTURE_FIELD])

    def _stored_picture(self) -> str:
        """File name currently saved in the database ("" when none or not saved yet)."""
        if not self.pk:
            return ""
        stored = type(self)._default_manager.filter(pk=self.pk).values_list(self.PICTURE_FIELD, flat=True)
        return stored.first() or ""


def _random_name(prefix: str) -> str:
    # Random file names: media files are served without a session check.
    return f"{prefix}_{secrets.token_hex(8)}"


class ProfileManager(Manager["Profile"]):
    def for_user(self, user: User) -> Profile:
        """The user's profile, created on the spot when missing. New users get one from a signal
        and existing ones from a migration, but loaddata skips signals (an old dump restores users
        without profiles), so reads go through here and never fail."""
        profile, _created = self.get_or_create(user=user)
        return profile

    def get_by_natural_key(self, username: str) -> Profile:
        return self.get(user__username=username)


class Profile(PictureModel):
    """What a user adds about themselves. Email and phone are private: only the owner sees them."""

    AVATAR_SIZE = 512  # pixels, square
    PICTURE_FIELD = "avatar"

    user = OneToOneField(User, on_delete=CASCADE, related_name="profile")
    avatar = ImageField(upload_to="avatars/", blank=True)
    phone = CharField(max_length=30, blank=True, validators=[validate_phone])
    is_public = BooleanField(default=True)  # other signed-in users can see the profile

    objects = ProfileManager()

    def __str__(self) -> str:
        return self.user.username

    def natural_key(self) -> tuple[str]:
        # Dumps refer to the profile by its user's username, like the user itself (see dump.py).
        return self.user.natural_key()

    natural_key.dependencies = ["auth.user"]

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_jpeg(_random_name(f"user{self.user_id}"), upload, self.AVATAR_SIZE)

    def remove_avatar(self) -> None:
        self.remove_picture()


# ---------- Achievements ----------
# What an achievement is called, looks like, when it can be earned and how its levels are set
# lives here (editable in Gestión); how it is earned lives in code (comics/achievements), tied by
# the achievement's `rule`.


def localized(obj: object, field: str) -> str:
    """`obj.<field>_<language>` in the active language, falling back to the other languages."""
    active = (get_language() or settings.LANGUAGE_CODE).split("-")[0]
    codes = [active] + [code for code, _name in settings.LANGUAGES if code != active]
    for code in codes:
        value = getattr(obj, f"{field}_{code}", "")
        if value:
            return value
    return ""


# Codes generated in Gestión start with this, so they never collide with (nor pick up the bundled
# artwork of) a built-in code a future data migration seeds. Built-in codes never use it.
GENERATED_CODE_PREFIX = "g_"


def unique_code(name: str, taken: Iterable[str], *, max_length: int = 50) -> str:
    """A code made from `name` (lowercase ASCII letters, digits and _, after GENERATED_CODE_PREFIX)
    that is not in `taken`: "Noche de brujas" -> "g_noche_de_brujas", then "g_noche_de_brujas_2"...
    Codes are identifiers (natural keys, bundled artwork names), so they never change once given."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    room = max_length - len(GENERATED_CODE_PREFIX)
    base = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:room].strip("_") or "item"
    taken = set(taken)
    code, number = f"{GENERATED_CODE_PREFIX}{base}", 1
    while code in taken:
        number += 1
        suffix = f"_{number}"
        code = f"{GENERATED_CODE_PREFIX}{base[: room - len(suffix)].rstrip('_')}{suffix}"
    return code


class AchievementCategoryManager(Manager["AchievementCategory"]):
    def get_by_natural_key(self, code: str) -> AchievementCategory:
        return self.get(code=code)


class AchievementCategory(PictureModel):
    """A group of achievements, managed in Gestión: its name, order and medallion. A disabled
    category hides (and stops awarding) all its achievements. It cannot be deleted while it has
    achievements (PROTECT)."""

    IMAGE_SIZE = 256  # pixels, square
    PICTURE_FIELD = "image"

    code = CharField(max_length=30, unique=True)  # natural key; generated from the name, never changed
    name_en = CharField(max_length=80)
    name_es = CharField(max_length=80)
    description_en = CharField(max_length=255, blank=True)
    description_es = CharField(max_length=255, blank=True)
    display_order = PositiveSmallIntegerField(default=0)
    is_enabled = BooleanField(default=True)
    image = ImageField(upload_to="achievement_categories/", blank=True)  # else the bundled medallion

    objects = AchievementCategoryManager()

    class Meta:
        ordering = ["display_order", "code"]
        verbose_name_plural = "achievement categories"

    def __str__(self) -> str:
        return self.name or self.code

    def natural_key(self) -> tuple[str]:
        return (self.code,)

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_png(_random_name(f"category_{self.code}"), upload, self.IMAGE_SIZE)

    @property
    def name(self) -> str:
        return localized(self, "name")

    @property
    def description(self) -> str:
        return localized(self, "description")


class AchievementQuerySet(QuerySet["Achievement"]):
    def active(self) -> AchievementQuerySet:
        """Shown and awarded: enabled, in an enabled category."""
        return self.filter(is_enabled=True, category__is_enabled=True)


class AchievementManager(Manager.from_queryset(AchievementQuerySet)):  # type: ignore[misc]
    def get_by_natural_key(self, code: str) -> Achievement:
        return self.get(code=code)


def _day_validators() -> list[Any]:
    return [MinValueValidator(1), MaxValueValidator(31)]


def _month_validators() -> list[Any]:
    return [MinValueValidator(1), MaxValueValidator(12)]


class Achievement(PictureModel):
    """An achievement as users see it. `code` identifies the row (natural key, bundled artwork);
    `rule` names the rule in code that awards it, so several rows can share one rule. Built-in
    rows come from data migrations; Gestión creates rows only for generic rules, which read their
    parameters (the time windows) from the row."""

    ICON_SIZE = 256  # pixels, square
    PICTURE_FIELD = "icon_image"

    code = CharField(max_length=50, unique=True)  # never changed; generated from the name in Gestión
    rule = CharField(max_length=50)  # registry key (comics/achievements/rules.py); fixed once created
    name_en = CharField(max_length=80)
    name_es = CharField(max_length=80)
    description_en = CharField(max_length=255, blank=True)
    description_es = CharField(max_length=255, blank=True)
    # Shown instead of the description while a secret achievement is not earned (optional).
    hint_en = CharField(max_length=255, blank=True)
    hint_es = CharField(max_length=255, blank=True)
    category = ForeignKey(AchievementCategory, on_delete=PROTECT, related_name="achievements")
    # Replaces the bundled artwork (static/achievements/<code>.svg, else the generic medal) when set.
    icon_image = ImageField(upload_to="achievements/", blank=True)
    is_secret = BooleanField(default=False)
    is_enabled = BooleanField(default=True)
    display_order = PositiveSmallIntegerField(default=0)
    # Time windows (comics/achievements/windows.py), read by windowed rules, in the project time zone.
    calendar_window = CharField(max_length=10, choices=CalendarWindow.choices, default=CalendarWindow.NONE)
    window_start_day = PositiveSmallIntegerField(null=True, blank=True, validators=_day_validators())
    window_start_month = PositiveSmallIntegerField(null=True, blank=True, validators=_month_validators())
    window_end_day = PositiveSmallIntegerField(null=True, blank=True, validators=_day_validators())
    window_end_month = PositiveSmallIntegerField(null=True, blank=True, validators=_month_validators())
    window_start_date = DateField(null=True, blank=True)
    window_end_date = DateField(null=True, blank=True)
    window_start_time = TimeField(null=True, blank=True)
    window_end_time = TimeField(null=True, blank=True)

    objects = AchievementManager()

    class Meta:
        # "category" follows the category's own ordering (display_order, code).
        ordering = ["category", "display_order", "code"]

    def __str__(self) -> str:
        return self.name or self.code

    def natural_key(self) -> tuple[str]:
        return (self.code,)

    natural_key.dependencies = ["comics.achievementcategory"]

    def clean(self) -> None:
        super().clean()
        clear_unused(self)
        errors = window_errors(self)
        if errors:
            raise ValidationError(errors)

    def normalize_picture(self, upload: Any) -> InMemoryUploadedFile:
        return square_png(_random_name(f"achievement_{self.code}"), upload, self.ICON_SIZE)

    @property
    def name(self) -> str:
        return localized(self, "name")

    @property
    def description(self) -> str:
        return localized(self, "description")

    @property
    def hint(self) -> str:
        return localized(self, "hint")

    @property
    def is_active(self) -> bool:
        return self.is_enabled and self.category.is_enabled


class AchievementTierManager(Manager["AchievementTier"]):
    def get_by_natural_key(self, code: str, level: int) -> AchievementTier:
        return self.get(achievement__code=code, level=level)


class AchievementTier(Model):
    """A level of a count-based achievement: reached when the count gets to `threshold`."""

    achievement = ForeignKey(Achievement, on_delete=CASCADE, related_name="tiers")
    level = PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    threshold = PositiveIntegerField(validators=[MinValueValidator(1)])
    label_en = CharField(max_length=40, blank=True)
    label_es = CharField(max_length=40, blank=True)

    objects = AchievementTierManager()

    class Meta:
        ordering = ["achievement", "level"]
        constraints = [UniqueConstraint(fields=["achievement", "level"], name="unique_achievement_tier_level")]

    def __str__(self) -> str:
        return f"{self.achievement.code} x{self.level} ({self.threshold})"

    def natural_key(self) -> tuple[str, int]:
        return (*self.achievement.natural_key(), self.level)

    natural_key.dependencies = ["comics.achievement"]

    @property
    def label(self) -> str:
        return localized(self, "label")


class UserAchievementManager(Manager["UserAchievement"]):
    def get_by_natural_key(self, username: str, code: str) -> UserAchievement:
        return self.get(user__username=username, achievement__code=code)

    def award(self, user: User, achievement: Achievement, level: int, *, bulk: bool = False) -> bool:
        """Give `user` `level` of `achievement` unless they already have it or a higher one.
        Safe under concurrency: one row per user and achievement (unique constraint, get_or_create)
        and the level only moves up (conditional UPDATE). True when something changed."""
        now = timezone.now()
        values = {"level": level, "level_reached_at": now, "notified": False, "bulk_awarded": bulk}
        award, created = self.get_or_create(user=user, achievement=achievement, defaults={**values, "awarded_at": now})
        if created:
            return True
        return bool(self.filter(pk=award.pk, level__lt=level).update(**values))


class UserAchievement(Model):
    """An achievement a user earned and the highest level reached. Never revoked: losing what
    earned it (e.g. selling comics) does not take it away."""

    user = ForeignKey(User, on_delete=CASCADE, related_name="achievements")
    achievement = ForeignKey(Achievement, on_delete=PROTECT, related_name="awards")
    level = PositiveSmallIntegerField(default=1)
    awarded_at = DateTimeField()  # first earned
    level_reached_at = DateTimeField()  # current level reached
    # Pending "unlocked" notice. Retroactive awards (bulk_awarded) are announced together in one.
    notified = BooleanField(default=False)
    bulk_awarded = BooleanField(default=False)

    objects = UserAchievementManager()

    class Meta:
        constraints = [UniqueConstraint(fields=["user", "achievement"], name="unique_user_achievement")]
        indexes = [Index(fields=["user", "notified"], name="user_achievement_pending")]

    def __str__(self) -> str:
        return f"{self.user} - {self.achievement.code} x{self.level}"

    def natural_key(self) -> tuple[str, str]:
        return (*self.user.natural_key(), *self.achievement.natural_key())

    natural_key.dependencies = ["auth.user", "comics.achievement"]


# ---------- Change requests ----------
# Collectors propose new issues and editions, or corrections to existing ones; Reviewers validate
# the proposals and Administrators approve them (see comics.change_requests). The history of the
# catalogue (comics.history) knows nothing about requests: an approved request is applied through
# comics.versions and points to the version it produced, so auditing requests is querying this table.


class ChangeRequestQuerySet(QuerySet["ChangeRequest"]):
    def open(self) -> ChangeRequestQuerySet:
        """Waiting for a decision: pending or validated."""
        return self.filter(status__in=ChangeRequest.OPEN_STATUSES)


class ChangeRequest(Model):
    """A proposal to create an issue or an edition, or to change some fields of an existing one.

    Rows are referenced by id (`target_pk`, and the versions by their `history_id`), not by foreign
    keys, as the versions themselves reference them: a request is an audit record that outlives its
    target (an edition deleted since keeps its requests, which then cannot be approved; restoring it
    from its history, with the same id, makes them applicable again).

    `changes` maps field names to proposed values as JSON (foreign keys and many-to-many fields by
    id, dates in ISO format, decimals as strings): for UPDATE only the fields that differ from the
    values when it was submitted, for CREATE every field of the proposed object. `base_history_id`
    is the latest version of the target when it was submitted: what reviewers compare against to
    tell whether the target changed since (conflicts). Each step keeps who took it, when and an
    optional note: the validation (`validated_*`) and the outcome (`decided_*`: approved, rejected,
    or withdrawn by its author)."""

    class Kind(TextChoices):
        EDITION = "edition", _("Edition")
        ISSUE = "issue", _("Issue")

    class Action(TextChoices):
        CREATE = "create", pgettext_lazy("change request", "New")
        UPDATE = "update", _("Correction")

    class Status(TextChoices):
        PENDING = "pending", _("Pending")
        VALIDATED = "validated", _("Validated")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    OPEN_STATUSES = (Status.PENDING, Status.VALIDATED)

    kind = CharField(max_length=10, choices=Kind.choices)
    action = CharField(max_length=10, choices=Action.choices)
    status = CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # The issue or edition: the one to change (UPDATE), or the one created on approval (CREATE).
    target_pk = PositiveBigIntegerField(null=True, blank=True)
    # Its name when the request was submitted (and when it was approved), so lists need no lookups
    # and still name a target deleted since.
    target_label = CharField(max_length=200, blank=True)
    changes = JSONField(default=dict)
    base_history_id = PositiveBigIntegerField(null=True, blank=True)
    applied_history_id = PositiveBigIntegerField(null=True, blank=True)  # the version the approval wrote
    reason = TextField(max_length=1000)
    evidence_url = URLField(max_length=500, blank=True, validators=[URLValidator(schemes=["http", "https"])])
    author = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="change_requests")
    created_at = DateTimeField(default=timezone.now)
    validated_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    validated_at = DateTimeField(null=True, blank=True)
    validation_note = TextField(max_length=1000, blank=True)
    decided_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="+")
    decided_at = DateTimeField(null=True, blank=True)
    decision_note = TextField(max_length=1000, blank=True)

    objects = ChangeRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [
            ("validate_changerequest", "Can validate or reject pending change requests"),
            ("approve_changerequest", "Can approve or reject change requests"),
        ]
        indexes = [
            Index(fields=["status", "-created_at"], name="change_request_status"),
            Index(fields=["author", "-created_at"], name="change_request_author"),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(action="create") | Q(target_pk__isnull=False),
                name="change_request_update_has_target",
            ),
            CheckConstraint(
                condition=~Q(status="approved") | Q(target_pk__isnull=False, applied_history_id__isnull=False),
                name="change_request_approved_has_version",
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.title}"

    @property
    def model(self) -> type[Model]:
        return Edition if self.kind == self.Kind.EDITION else Issue

    @property
    def title(self) -> str:
        """What the request is about, e.g. "Correction to the edition Spawn #1 A"."""
        titles = {
            (self.Action.UPDATE, self.Kind.EDITION): gettext("Correction to the edition %(name)s"),
            (self.Action.UPDATE, self.Kind.ISSUE): gettext("Correction to the issue %(name)s"),
            (self.Action.CREATE, self.Kind.EDITION): gettext("New edition %(name)s"),
            (self.Action.CREATE, self.Kind.ISSUE): gettext("New issue %(name)s"),
        }
        return titles[(self.action, self.kind)] % {"name": self.target_label}

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    def target(self) -> Model | None:
        """The issue or edition it is about; None if it does not exist (yet, or any more)."""
        if self.target_pk is None:
            return None
        return self.model._default_manager.filter(pk=self.target_pk).first()

    def allowed_actions(self, user: User | AnonymousUser) -> frozenset[str]:
        """What `user` may do with it now: the state machine of a request.

        pending -> validated (validate permission) -> approved or rejected (approve permission). An
        approver can also approve or reject straight from pending, and a validator can reject a
        pending request. Only its author can withdraw it, while it is pending. Nobody reviews their
        own request (it would let them award themselves the Reportero achievement)."""
        if not user.is_authenticated:
            return frozenset()
        actions: set[str] = set()
        own = self.author_id is not None and self.author_id == user.pk
        if own:
            if self.status == self.Status.PENDING:
                actions.add("withdraw")
            return frozenset(actions)
        can_validate = user.has_perm(VALIDATE_CHANGE_REQUESTS)
        can_approve = user.has_perm(APPROVE_CHANGE_REQUESTS)
        if self.status == self.Status.PENDING:
            if can_validate:
                actions |= {"validate", "reject"}
            if can_approve:
                actions |= {"approve", "reject"}
        elif self.status == self.Status.VALIDATED and can_approve:
            actions |= {"approve", "reject"}
        return frozenset(actions)


# ---------- Notifications ----------
# What users are told about (achievements, announcements from Gestión, the steps of their change
# requests), shown under the navbar bell. A notification stores references, never texts: it is
# rendered in the viewer's language from what it points to, so later edits show up in it.


def validate_site_path(value: str) -> None:
    """Links stay on this site: a path starting with a single "/" (no scheme, no host)."""
    if not value.startswith("/") or not url_has_allowed_host_and_scheme(value, allowed_hosts=None):
        raise ValidationError(
            _("Enter a path on this site that starts with “/”, e.g. /comics/."), code="external_link"
        )


class Announcement(Model):
    """A message from Gestión to everyone or to some roles. Publishing it creates one notification
    per active user in the audience at that moment; editing it changes what those notifications
    show and deleting it removes them."""

    class Audience(TextChoices):
        EVERYONE = "everyone", _("Everyone")
        ROLES = "roles", _("Some roles")

    title_en = CharField(max_length=120)
    title_es = CharField(max_length=120)
    body_en = TextField(max_length=2000, blank=True)
    body_es = TextField(max_length=2000, blank=True)
    link = CharField(max_length=300, blank=True, validators=[validate_site_path])
    # Chosen when it is created (the notifications are created then) and never changed afterwards.
    audience = CharField(max_length=10, choices=Audience.choices, default=Audience.EVERYONE)
    roles = ManyToManyField(Group, blank=True, related_name="announcements")
    created_by = ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="announcements_created")
    created_at = DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.title

    @property
    def title(self) -> str:
        return localized(self, "title")

    @property
    def body(self) -> str:
        return localized(self, "body")

    def recipients(self) -> QuerySet[User]:
        """Active users in the audience now (for ROLES, members of any of its roles)."""
        users = User.objects.filter(is_active=True)
        if self.audience == self.Audience.ROLES:
            users = users.filter(groups__in=self.roles.all()).distinct()
        return users

    def publish(self) -> int:
        """Notify every recipient once; returns how many. Call it once, right after creating the
        announcement (and its roles), inside the same transaction."""
        recipient_ids = list(self.recipients().values_list("pk", flat=True))
        Notification.objects.bulk_create(
            [
                Notification(recipient_id=pk, kind=Notification.Kind.ANNOUNCEMENT, announcement=self)
                for pk in recipient_ids
            ],
            batch_size=500,
        )
        return len(recipient_ids)


class NotificationQuerySet(QuerySet["Notification"]):
    def for_display(self) -> NotificationQuerySet:
        """With what rendering them reads (achievement and its category, announcement, request)."""
        return self.select_related("achievement__category", "announcement", "change_request")

    def unread(self) -> NotificationQuerySet:
        return self.filter(read_at__isnull=True)

    def mark_read(self) -> int:
        """Mark the unread ones read now; returns how many."""
        return self.unread().update(read_at=timezone.now())


class NotificationManager(Manager.from_queryset(NotificationQuerySet)):  # type: ignore[misc]
    def unread_count(self, user: User, *, limit: int) -> int:
        """Unread notifications of `user`, counted up to `limit` (the badge caps the number
        anyway): one query on the (recipient, read_at) index that never counts more rows."""
        # order_by() drops Meta.ordering, so MySQL does not sort every unread row before the LIMIT.
        return self.filter(recipient=user, read_at__isnull=True).order_by()[:limit].count()

    def achievement_unlocked(self, user: User, achievement: Achievement, level: int) -> Notification:
        return self.create(recipient=user, kind=Notification.Kind.ACHIEVEMENT, achievement=achievement, level=level)

    def achievements_unlocked(self, user: User, count: int) -> Notification:
        """The summary of a retroactive run (one per user and run instead of one per award)."""
        return self.create(recipient=user, kind=Notification.Kind.ACHIEVEMENT_SUMMARY, count=count)

    def add_to_summary(self, pk: int, count: int = 1) -> None:
        self.filter(pk=pk).update(count=F("count") + count)

    def change_request_step(self, change_request: ChangeRequest) -> Notification | None:
        """Tell the author of `change_request` about the step it just took (validated, approved or
        rejected: its current status). None when the request has no author any more."""
        if change_request.author_id is None:
            return None
        kind = Notification.CHANGE_REQUEST_KINDS[change_request.status]
        return self.create(recipient_id=change_request.author_id, kind=kind, change_request=change_request)


class Notification(Model):
    """Something a user is told about. `kind` says which references it uses: an achievement and
    the level reached, an announcement, a number of achievements (summaries) or a change request."""

    class Kind(TextChoices):
        ACHIEVEMENT = "achievement"  # a live award or level-up
        ACHIEVEMENT_SUMMARY = "achievement_summary"  # a retroactive run: "you unlocked N"
        ACHIEVEMENTS_HELD = "achievements_held"  # what users held when notifications arrived (migration)
        ANNOUNCEMENT = "announcement"
        CHANGE_REQUEST_VALIDATED = "change_request_validated"  # the author's request steps
        CHANGE_REQUEST_APPROVED = "change_request_approved"
        CHANGE_REQUEST_REJECTED = "change_request_rejected"

    # The kind telling a request's author about each status their request can reach.
    CHANGE_REQUEST_KINDS = {
        ChangeRequest.Status.VALIDATED: Kind.CHANGE_REQUEST_VALIDATED,
        ChangeRequest.Status.APPROVED: Kind.CHANGE_REQUEST_APPROVED,
        ChangeRequest.Status.REJECTED: Kind.CHANGE_REQUEST_REJECTED,
    }

    recipient = ForeignKey(User, on_delete=CASCADE, related_name="notifications")
    kind = CharField(max_length=30, choices=Kind.choices)
    # Achievements with awards cannot be deleted (UserAchievement PROTECTs them), so in practice
    # this cascade never fires; it keeps a hand-made deletion from failing on notifications.
    achievement = ForeignKey(Achievement, on_delete=CASCADE, null=True, blank=True, related_name="notifications")
    announcement = ForeignKey(Announcement, on_delete=CASCADE, null=True, blank=True, related_name="notifications")
    change_request = ForeignKey(
        ChangeRequest, on_delete=CASCADE, null=True, blank=True, related_name="notifications"
    )
    level = PositiveSmallIntegerField(null=True, blank=True)
    count = PositiveIntegerField(null=True, blank=True)
    created_at = DateTimeField(default=timezone.now)
    read_at = DateTimeField(null=True, blank=True)

    objects = NotificationManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            Index(fields=["recipient", "read_at"], name="notification_unread"),
            Index(fields=["recipient", "-created_at"], name="notification_recent"),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(kind="achievement", achievement__isnull=False, level__isnull=False)
                | Q(kind__in=["achievement_summary", "achievements_held"], count__isnull=False)
                | Q(kind="announcement", announcement__isnull=False)
                | Q(
                    kind__in=["change_request_validated", "change_request_approved", "change_request_rejected"],
                    change_request__isnull=False,
                ),
                name="notification_kind_references",
            )
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id} - {self.kind}"

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    @property
    def icon(self) -> str:
        """Material Symbols name."""
        if self.kind == self.Kind.ANNOUNCEMENT:
            return "campaign"
        if self.kind == self.Kind.CHANGE_REQUEST_VALIDATED:
            return "fact_check"
        if self.kind == self.Kind.CHANGE_REQUEST_APPROVED:
            return "task_alt"
        if self.kind == self.Kind.CHANGE_REQUEST_REJECTED:
            return "cancel"
        if self.kind == self.Kind.ACHIEVEMENT:
            return "emoji_events"
        return "military_tech"

    @property
    def text(self) -> str:
        """The headline, in the active language (plain text: templates escape it)."""
        if self.kind == self.Kind.ACHIEVEMENT:
            achievement = self.achievement
            # Disabled achievements are hidden everywhere else: do not name them here either.
            if achievement is None or not achievement.is_active:
                return gettext("An achievement you unlocked is no longer available.")
            if self.level and self.level > 1:
                return gettext("Achievement unlocked: %(name)s (level %(level)s)") % {
                    "name": achievement.name,
                    "level": self.level,
                }
            return gettext("Achievement unlocked: %(name)s") % {"name": achievement.name}
        if self.kind == self.Kind.ACHIEVEMENT_SUMMARY:
            count = self.count or 0
            return ngettext(
                "You unlocked %(count)d achievement: see it on your profile.",
                "You unlocked %(count)d achievements: see them on your profile.",
                count,
            ) % {"count": count}
        if self.kind == self.Kind.ACHIEVEMENTS_HELD:
            count = self.count or 0
            return ngettext(
                "You have %(count)d achievement: see it on your profile.",
                "You have %(count)d achievements: see them on your profile.",
                count,
            ) % {"count": count}
        if self.change_request is not None:
            texts = {
                self.Kind.CHANGE_REQUEST_VALIDATED: gettext("Your request was validated: %(title)s"),
                self.Kind.CHANGE_REQUEST_APPROVED: gettext("Your request was approved: %(title)s"),
                self.Kind.CHANGE_REQUEST_REJECTED: gettext("Your request was rejected: %(title)s"),
            }
            return texts[self.kind] % {"title": self.change_request.title}
        if self.announcement is not None:
            return self.announcement.title
        return ""

    @property
    def detail(self) -> str:
        """Longer text shown on the notifications page (an announcement's body)."""
        if self.kind == self.Kind.ANNOUNCEMENT and self.announcement is not None:
            return self.announcement.body
        if self.change_request is not None:
            # The note of the step it tells about.
            if self.kind == self.Kind.CHANGE_REQUEST_VALIDATED:
                return self.change_request.validation_note
            return self.change_request.decision_note
        return ""

    @property
    def target_url(self) -> str | None:
        """Where opening it leads (a path on this site), or None."""
        if self.kind == self.Kind.ANNOUNCEMENT:
            return (self.announcement.link or None) if self.announcement is not None else None
        if self.change_request_id is not None:
            return reverse("comics_request_detail", args=[self.change_request_id])
        return f"{reverse('profile')}#achievements"

    def mark_read(self) -> None:
        """Mark it read (once: a notification already read keeps its first read time)."""
        if self.read_at is None:
            self.read_at = timezone.now()
            Notification.objects.filter(pk=self.pk, read_at__isnull=True).update(read_at=self.read_at)
