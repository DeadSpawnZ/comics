"""Small helpers that create valid objects with sensible defaults (the project has no factory library)."""

import datetime
import io
import itertools
from decimal import Decimal
from typing import Any
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from comics.models import Collection, Dealer, Edition, Editorial, Publishing

_sequence = itertools.count(1)


def make_user(*, staff: bool = False, password: str = "secret-pass", **fields: Any) -> User:
    username = fields.pop("username", f"user{next(_sequence)}")
    user = User.objects.create_user(username=username, password=password, is_staff=staff, **fields)
    return user


def make_editorial(**fields: Any) -> Editorial:
    fields.setdefault("name", f"Editorial {next(_sequence)}")
    fields.setdefault("country", Editorial.CountryAbbr.US)
    return Editorial.objects.create(**fields)


def make_publishing(*, editorials: list[Editorial] | None = None, **fields: Any) -> Publishing:
    fields.setdefault("publishing_title", f"Series {next(_sequence)}")
    fields.setdefault("year", 2000)
    publishing = Publishing.objects.create(**fields)
    if editorials:
        publishing.editorials.set(editorials)
    return publishing


def make_edition(*, publishing: Publishing | None = None, **fields: Any) -> Edition:
    fields.setdefault("number", str(next(_sequence)))
    fields.setdefault("release_date", datetime.date(2000, 1, 1))
    edition = Edition(publishing=publishing or make_publishing(), **fields)
    # Fixture editions have no image: process_image would only log that it cannot open one.
    with mock.patch.object(Edition, "process_image"):
        edition.save()
    return edition


def make_dealer(**fields: Any) -> Dealer:
    fields.setdefault("name", f"Dealer {next(_sequence)}")
    return Dealer.objects.create(**fields)


def make_collection(*, collector: User, edition: Edition, **fields: Any) -> Collection:
    fields.setdefault("amount", Decimal(next(_sequence)))
    fields.setdefault("trade_date", datetime.date(2020, 1, 1))
    return Collection.objects.create(collector=collector, edition=edition, **fields)


def image_upload(name: str = "cover.png", *, mode: str = "RGBA", fmt: str = "PNG") -> SimpleUploadedFile:
    """A tiny in-memory image file, as an upload."""
    buffer = io.BytesIO()
    Image.new(mode, (4, 4), (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")
