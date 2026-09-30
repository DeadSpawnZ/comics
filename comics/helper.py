import datetime
import io

from django.core.files import File
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils.dateparse import parse_date
from PIL import Image


def parse_id_or_none(value: object) -> int | None:
    """Parse a primary key from user input (or a model instance); None when it is empty or not a number."""
    value = getattr(value, "pk", value)
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_date_or_none(value: str | None) -> datetime.date | None:
    """Parse a YYYY-MM-DD date from user input; None when it is empty, malformed or impossible
    (parse_date returns None for a bad format but raises ValueError for e.g. 2024-02-30)."""
    try:
        return parse_date(value) if value else None
    except ValueError:
        return None


def generate_image_jpeg(base_name: str, image: File | None) -> InMemoryUploadedFile | None:
    if not image:
        return

    img = Image.open(image)

    # Convert RGBA to RGB if necessary
    if img.format == "PNG" and img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Save new JPEG image as original
    original_io = io.BytesIO()
    img.save(original_io, format="JPEG", quality=95)
    original_io.seek(0)

    original_filename = f"{base_name}.jpg"
    return InMemoryUploadedFile(
        original_io,
        "ImageField",
        original_filename,
        "image/jpeg",
        original_io.getbuffer().nbytes,
        None,
    )
