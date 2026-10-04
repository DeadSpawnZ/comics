import datetime
import io

from django.core.files import File
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils.dateparse import parse_date
from PIL import Image, ImageOps


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


def _flatten_to_rgb(img: Image.Image) -> Image.Image:
    """RGB version of `img`; transparent areas become white (JPEG has no alpha channel)."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background
    return img if img.mode == "RGB" else img.convert("RGB")


def _jpeg_upload(img: Image.Image, filename: str, quality: int) -> InMemoryUploadedFile:
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return InMemoryUploadedFile(buffer, "ImageField", filename, "image/jpeg", buffer.getbuffer().nbytes, None)


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
    return _jpeg_upload(img, f"{base_name}.jpg", quality=95)


def square_jpeg(base_name: str, image: File, size: int) -> InMemoryUploadedFile:
    """`image` as a `size` x `size` JPEG: rotated as its EXIF says, centre-cropped to a square
    and resized. Re-encoding also drops the original metadata (EXIF location and so on).

    The picture is shrunk right after opening, before any copy (rotation, flattening, cropping):
    JPEGs are decoded at a reduced scale (draft) and anything still larger is cut down to a
    `size * 4` box, so a big upload never sits in memory at full size several times."""
    image.seek(0)
    with Image.open(image) as source:
        if source.format == "JPEG":
            source.draft("RGB", (size * 2, size * 2))
        source.thumbnail((size * 4, size * 4), Image.Resampling.LANCZOS)
        img = _flatten_to_rgb(ImageOps.exif_transpose(source))
        img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS)
    return _jpeg_upload(img, f"{base_name}.jpg", quality=90)
