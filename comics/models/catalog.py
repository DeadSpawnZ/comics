"""The catalogue: editorials, titles, publishings, artists, issues and their editions."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

import simple_history
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db.models import (
    CASCADE,
    PROTECT,
    CharField,
    DateField,
    DecimalField,
    F,
    ForeignKey,
    ImageField,
    IntegerField,
    Manager,
    ManyToManyField,
    Min,
    Model,
    PositiveIntegerField,
    PositiveSmallIntegerField,
    Q,
    QuerySet,
    TextChoices,
    TextField,
    UniqueConstraint,
)
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from PIL import Image

from ..helper import name_key
from ..history import TrackedHistory, record_change

logger = logging.getLogger(__name__)


class EditorialQuerySet(QuerySet["Editorial"]):
    def matching(self, name: str) -> Editorial | None:
        """The editorial called `name`, or known by it as an alias (EditorialAlias), comparing names
        ignoring case, accents and repeated spaces (how a catalogue's publisher is matched). A name
        wins over an alias. At most two queries: editorials are few, so their names are compared here."""
        key = name_key(name)
        if not key:
            return None
        for editorial in self.order_by("pk"):
            if name_key(editorial.name) == key:
                return editorial
        alias = EditorialAlias.objects.filter(key=key, editorial__in=self).select_related("editorial").first()
        return alias.editorial if alias else None


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

    objects = EditorialQuerySet.as_manager()

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """A name can not be an alias too (of this or another editorial): the catalogue import would
        not know which editorial is meant."""
        key = name_key(self.name or "")
        alias = EditorialAlias.objects.filter(key=key).select_related("editorial").first() if key else None
        if alias is not None:
            raise ValidationError(
                {
                    "name": ValidationError(
                        _("“%(name)s” is an alias of %(editorial)s: delete the alias first."),
                        code="alias",
                        params={"name": alias.name, "editorial": alias.editorial.name},
                    )
                }
            )


class EditorialAliasManager(Manager["EditorialAlias"]):
    def get_by_natural_key(self, name: str) -> EditorialAlias:
        return self.get(key=name_key(name))

    def remember(self, editorial: Editorial, name: str) -> tuple[EditorialAlias, bool]:
        """(the alias `name` of `editorial`, whether it was created now). Remembering it again is not an
        error; any other conflict (the alias of another editorial, an editorial's name) raises the
        ValidationError of EditorialAlias.clean(), keyed by field. Call inside a transaction."""
        existing = self.filter(key=name_key(name)).first()
        if existing is not None and existing.editorial_id == editorial.pk:
            return existing, False
        alias = EditorialAlias(editorial=editorial, name=name)
        alias.full_clean(exclude=["key"])  # clean() reports a taken key on `name`
        alias.save()
        return alias, True


class EditorialAlias(Model):
    """Another name of an editorial in the external catalogues (e.g. Metron's "Marvel" is "Marvel
    Comics" in Comi): the catalogue import matches a publisher by the editorial's name or by one of
    its aliases. Aliases are unique and none is an editorial's name, compared ignoring case, accents
    and repeated spaces. That comparison lives in `key` (name_key of the name, set by clean() and
    save()) under a unique index: a functional index (Lower) would miss accents and spaces, and a
    plain column behaves the same on MySQL and SQLite."""

    editorial = ForeignKey(Editorial, on_delete=CASCADE, related_name="aliases")
    name = CharField(_("Alias"), max_length=100)
    key = CharField(max_length=255, unique=True, editable=False)

    objects = EditorialAliasManager()

    class Meta:
        ordering = ["name"]
        verbose_name = _("editorial alias")
        verbose_name_plural = _("editorial aliases")

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = " ".join(self.name.split())
        self.key = name_key(self.name)[:255]
        super().save(*args, **kwargs)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    natural_key.dependencies = ["comics.editorial"]

    def clean(self) -> None:
        self.name = " ".join((self.name or "").split())
        self.key = name_key(self.name)[:255]
        if not self.key:
            return  # the field reports the missing name
        taken = EditorialAlias.objects.filter(key=self.key).exclude(pk=self.pk).select_related("editorial").first()
        if taken is not None:
            raise ValidationError(
                {
                    "name": ValidationError(
                        _("“%(name)s” is already an alias of %(editorial)s."),
                        code="duplicate",
                        params={"name": taken.name, "editorial": taken.editorial.name},
                    )
                }
            )
        # Its own editorial by the name it is being saved with (e.g. renamed in the same admin form).
        try:
            own: Editorial | None = self.editorial
        except Editorial.DoesNotExist:
            own = None
        others = Editorial.objects.exclude(pk=own.pk) if own is not None and own.pk else Editorial.objects.all()
        names = [*others.values_list("name", flat=True), *([own.name] if own is not None else [])]
        for editorial_name in names:
            if name_key(editorial_name or "") == self.key:
                raise ValidationError(
                    {
                        "name": ValidationError(
                            _("“%(name)s” is the name of an editorial: it needs no alias."),
                            code="editorial_name",
                            params={"name": editorial_name},
                        )
                    }
                )


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
    # The series in external catalogues. Metron and Comic Vine have one record per series (volume),
    # so their ids are unique when set; GCD's is only indexed, as on Issue.
    metron_series_id = PositiveIntegerField("Metron series ID", null=True, blank=True, unique=True)
    comicvine_volume_id = PositiveIntegerField("Comic Vine volume ID", null=True, blank=True, unique=True)
    gcd_series_id = PositiveIntegerField("GCD series ID", null=True, blank=True, db_index=True)

    IDENTIFIER_FIELDS = ("metron_series_id", "comicvine_volume_id", "gcd_series_id")

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

    @property
    def select_label(self) -> str:
        """Label for publishing selects and lists: title (year) series · language · editorials. The
        editorials tell apart publishings with the same title; prefetch them to avoid one query per
        publishing."""
        year = f" ({self.year})" if self.year else ""
        editorials = "/".join(editorial.name for editorial in self.editorials.all())
        label = f"{self.publishing_title}{year} {self.serie} · {self.language.upper()}"
        return f"{label} · {editorials}" if editorials else label

    def identification_numbers(self) -> list[ExternalIdentifier]:
        """The series ids that are set, in a fixed order (Metron, Comic Vine, GCD), with the series
        pages built from the integers alone (see Issue.identification_numbers)."""
        identifiers = []
        if self.metron_series_id:
            identifiers.append(ExternalIdentifier("Metron", self.metron_series_id, ""))
        if self.comicvine_volume_id:
            url = f"https://comicvine.gamespot.com/volume/4050-{int(self.comicvine_volume_id)}/"
            identifiers.append(ExternalIdentifier("Comic Vine", self.comicvine_volume_id, url))
        if self.gcd_series_id:
            url = f"https://www.comics.org/series/{int(self.gcd_series_id)}/"
            identifiers.append(ExternalIdentifier("GCD", self.gcd_series_id, url))
        return identifiers

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


class ArtistQuerySet(QuerySet["Artist"]):
    def ensure_named(self, names: Iterable[str]) -> list[tuple[Artist, bool]]:
        """(artist, created) for each name, in order: the artist that already has the name, compared
        ignoring case, accents and repeated spaces (like the catalogue import), else a new one. A name
        repeated in `names` is created once. Call inside a transaction."""
        known: dict[str, Artist] = {}
        for artist in self.order_by("pk").only("pk", "name"):
            known.setdefault(name_key(artist.name), artist)
        result: list[tuple[Artist, bool]] = []
        for name in names:
            key = name_key(name)
            artist = known.get(key)
            if artist is not None:
                result.append((artist, False))
                continue
            artist = self.create(name=" ".join(name.split()))
            known[key] = artist
            result.append((artist, True))
        return result


class Artist(Model):
    name = CharField(max_length=100, unique=True)
    photo = ImageField(upload_to="artists/photos/", null=True, blank=True)

    objects = ArtistQuerySet.as_manager()

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


@dataclass(frozen=True)
class ExternalIdentifier:
    """An issue's number in an external catalogue, as the "Identification numbers" panel lists it."""

    source: str
    value: int
    url: str  # its page in that catalogue; empty when the catalogue has no public page per id


class Issue(Model):
    """The content (the story) of an issue. Its editions are the physical printings:
    variants, reprints, foreign or anniversary editions, which may belong to another publishing.

    The external catalogue ids identify the story too, not a variant or a foreign edition. Metron and
    Comic Vine have one record per story, so their ids are unique when set (a second issue with the
    same id is a duplicate). GCD has one record per printing (each variant and reprint is its own
    issue there), so its id is only indexed: two Comi issues may legitimately point to related GCD
    records, and a uniqueness rule on data we do not control would only block saves."""

    publishing = ForeignKey(Publishing, on_delete=PROTECT, related_name="issues", help_text="Serie original")
    number = CharField(max_length=5)
    synopsis = TextField(blank=True)
    creators = ManyToManyField(Artist, through="IssueCreator", blank=True, related_name="issues")
    metron_id = PositiveIntegerField("Metron ID", null=True, blank=True, unique=True)
    comicvine_id = PositiveIntegerField("Comic Vine ID", null=True, blank=True, unique=True)
    gcd_id = PositiveIntegerField("GCD ID", null=True, blank=True, db_index=True)

    objects = IssueQuerySet.as_manager()
    # `history` (its versions) is registered after IssueCreator, see below.

    IDENTIFIER_FIELDS = ("metron_id", "comicvine_id", "gcd_id")

    class Meta:
        ordering = ["publishing__publishing_title", "number"]
        constraints = [
            UniqueConstraint(fields=["publishing", "number"], name="unique_issue_publishing_number"),
        ]

    def __str__(self) -> str:
        return f"{self.publishing.publishing_title} #{self.number}"

    def identification_numbers(self) -> list[ExternalIdentifier]:
        """The external ids that are set, in a fixed order (Metron, Comic Vine, GCD). The URLs are
        built from the integers alone, so no stored text ever ends up in a link."""
        identifiers = []
        if self.metron_id:
            identifiers.append(ExternalIdentifier("Metron", self.metron_id, ""))
        if self.comicvine_id:
            url = f"https://comicvine.gamespot.com/issue/4000-{int(self.comicvine_id)}/"
            identifiers.append(ExternalIdentifier("Comic Vine", self.comicvine_id, url))
        if self.gcd_id:
            url = f"https://www.comics.org/issue/{int(self.gcd_id)}/"
            identifiers.append(ExternalIdentifier("GCD", self.gcd_id, url))
        return identifiers


class IssueCreator(Model):
    """An artist credited on an issue (the rows of Issue.creators). An artist cannot be deleted
    while it is credited anywhere (PROTECT); deleting the issue removes its credits.

    It took over the table of the former auto-created relation (migration 0112), so it keeps that
    table name and its unique (issue, artist) pair as `unique_together`, which is how the database
    has it."""

    issue = ForeignKey(Issue, on_delete=CASCADE, related_name="creator_entries")
    artist = ForeignKey(Artist, on_delete=PROTECT, related_name="issue_credits")

    class Meta:
        db_table = "comics_issue_creators"
        unique_together = [("issue", "artist")]

    def __str__(self) -> str:
        return f"{self.issue} – {self.artist}"


# Versions of every saved state of an issue, with its creators; see comics.history. Registered here
# because the history of the creators needs their through model (IssueCreator) to exist.
simple_history.register(Issue, records_class=TrackedHistory, m2m_fields=["creators"])


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
    cover_artists = ManyToManyField(Artist, through="EditionCoverArtist", blank=True, related_name="covers")
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


class EditionCoverArtist(Model):
    """An artist of an edition's cover (the rows of Edition.cover_artists). An artist cannot be
    deleted while it is credited anywhere (PROTECT); deleting the edition removes its credits.

    It took over the table of the former auto-created relation (migration 0112), so it keeps that
    table name and its unique (edition, artist) pair as `unique_together`, which is how the
    database has it."""

    edition = ForeignKey(Edition, on_delete=CASCADE, related_name="cover_artist_entries")
    artist = ForeignKey(Artist, on_delete=PROTECT, related_name="cover_credits")

    class Meta:
        db_table = "comics_edition_cover_artists"
        unique_together = [("edition", "artist")]

    def __str__(self) -> str:
        return f"{self.edition.short_name} – {self.artist}"


# Versions of every saved state of an edition, with its cover artists and collected issues (and their
# order); see comics.history. Registered here because the history of these relations needs their
# through models (EditionCoverArtist, CollectedIssue) to exist.
simple_history.register(Edition, records_class=TrackedHistory, m2m_fields=["cover_artists", "collected_issues"])
