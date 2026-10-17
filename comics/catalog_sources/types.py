"""The neutral input of the mapper: an issue as any catalogue describes it, and the parsing helpers
the clients share to build it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# Words in a credit's role that make it part of the story (Issue.creators). A cover role never
# does, even "Cover Artist": cover credits go to the cover artists (Edition.cover_artists).
STORY_ROLE_WORDS = (
    "writer",
    "script",
    "plot",
    "story",
    "artist",
    "pencil",
    "ink",
    "color",
    "colour",
    "letter",
    "paint",
    "illustrat",
    "breakdown",
    "finish",
    "layout",
)

# A variant's ratio ("1:25") and printing ("2nd Printing"), as catalogues write them in variant names
# and notes.
RATIO_PATTERN = re.compile(r"(?<![\d:])(\d{1,3}):(\d{1,3})(?![\d:])")
PRINTING_PATTERN = re.compile(r"(?i)\b(\d{1,2})\s*(?:st|nd|rd|th)\s+print")


def parse_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


@dataclass
class SourceCredit:
    name: str
    roles: list[str]
    # The name was guessed from free text (MyComicShop) and may be cut or carry extra words.
    uncertain: bool = False

    @property
    def is_cover(self) -> bool:
        return any("cover" in role.casefold() for role in self.roles)

    @property
    def is_story(self) -> bool:
        return any(
            "cover" not in role.casefold() and any(word in role.casefold() for word in STORY_ROLE_WORDS)
            for role in self.roles
        )


@dataclass
class SourceVariant:
    name: str
    image: str
    price: Decimal | None


@dataclass
class SourceIssue:
    """An issue as a catalogue describes it, whatever the catalogue. Empty strings and None mean
    the catalogue does not know (or does not have) that value."""

    source: str
    source_name: str
    source_id: int | None
    source_url: str
    publisher: str
    series_name: str
    series_volume: int | None
    series_year: int | None
    series_type: str
    language: str
    number: str
    store_date: date | None
    cover_date: date | None
    price: Decimal | None
    price_currency: str
    description: str
    image: str
    credits: list[SourceCredit]
    variants: list[SourceVariant]
    metron_id: int | None
    comicvine_id: int | None
    gcd_id: int | None
    # The series (volume) and publisher ids in the source, when its issue answer says them.
    metron_series_id: int | None = None
    comicvine_volume_id: int | None = None
    source_publisher_id: int | None = None
    # The series id in the source when its series hooks need it (MyComicShop's TID).
    source_series_id: int | None = None
    # Edition-level values, for catalogues with one record per cover and printing (MyComicShop).
    # Empty/None: the mapper proposes the main cover (A) of the first printing, as for Metron and
    # Comic Vine, whose issue is the original publication.
    variant: str = ""
    printing: int | None = None  # 2 for a 2nd printing
    ratio: str = ""  # "1:25"
    limited_to: str = ""  # the print run, digits
    retailer_exclusive: str = ""
    event_exclusive: str = ""
    mycomicshop_id: int | None = None  # the edition's id in MyComicShop (IVID)


@dataclass
class SourceSeries:
    """What a catalogue says about the series of an issue beyond the issue itself: the series ids in
    other catalogues (to match a publishing) and, to propose a new one, its first issue's dates and
    the publisher's country. Every value may be unknown (None or empty)."""

    first_issue_store_date: date | None = None
    first_issue_cover_date: date | None = None
    publisher_country: str = ""  # as the catalogue writes it (Metron: ISO 3166 alpha-2, e.g. "US")
    comicvine_volume_id: int | None = None
    gcd_series_id: int | None = None
