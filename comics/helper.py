import datetime
import io
import re
import unicodedata

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


def name_key(name: str) -> str:
    """A name compared ignoring case, accents and repeated spaces (artists, editorials and their
    aliases are matched this way, e.g. against a catalogue's credits and publisher)."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


# A dot between letters when the next letter stands alone, as in an acronym ("WildC.A.T.S.",
# "S.H.I.E.L.D."): dropped, so the letters join. Any other dot ("Mr. Fantastic", "vs.Predator",
# "2.0") separates words.
_ACRONYM_DOT = re.compile(r"(?<=[^\W\d_])\.(?=[^\W\d_](?![^\W\d_]))")
# Punctuation that only separates words in a title. Hyphens and dashes are separators too:
# "Spider-Man" ≡ "Spider Man", "X-Men" ≡ "X Men", but "Spiderman" stays another title (letters are
# never joined or split, only punctuation is ignored). Joining instead ("Spider-Man" ≡ "Spiderman")
# would change the words of the title and "Batman - Year One" would still need a separator rule.
_TITLE_SEPARATORS = re.compile(r"[-‐-―−/\\:;,.#!?¡¿\"'`´‘-‟«»()\[\]{}|*~_]")
# Words a title writes in several ways.
_TITLE_WORDS = {"versus": "vs"}


def title_key(title: str) -> str:
    """A publishing title compared the way a catalogue may write it: as name_key (case, accents,
    spaces) and also ignoring the punctuation between words ("Grifter / Badrock" ≡ "Grifter
    Badrock", "Marvel/DC" ≡ "Marvel DC"), the dots of an acronym ("WildC.A.T.S." ≡ "WildCATS"),
    "&" ≡ "and" and "vs" ≡ "vs." ≡ "versus". Only for matching: titles are stored as printed."""
    text = _ACRONYM_DOT.sub("", name_key(title)).replace("&", " and ")
    words = _TITLE_SEPARATORS.sub(" ", text).split()
    return " ".join(_TITLE_WORDS.get(word, word) for word in words)


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


def _shrink_on_open(source: Image.Image, size: int) -> None:
    """Reduce a just-opened picture before any copy (rotation, flattening, cropping): JPEGs are
    decoded at a reduced scale (draft) and anything still larger is cut down to a `size * 4` box,
    so a big upload never sits in memory at full size several times."""
    if source.format == "JPEG":
        source.draft("RGB", (size * 2, size * 2))
    source.thumbnail((size * 4, size * 4), Image.Resampling.LANCZOS)


def square_jpeg(base_name: str, image: File, size: int) -> InMemoryUploadedFile:
    """`image` as a `size` x `size` JPEG: rotated as its EXIF says, centre-cropped to a square
    and resized. Re-encoding also drops the original metadata (EXIF location and so on)."""
    image.seek(0)
    with Image.open(image) as source:
        _shrink_on_open(source, size)
        img = _flatten_to_rgb(ImageOps.exif_transpose(source))
        img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS)
    return _jpeg_upload(img, f"{base_name}.jpg", quality=90)


def square_png(base_name: str, image: File, size: int) -> InMemoryUploadedFile:
    """Like square_jpeg, but a PNG that keeps transparency (badges and icons usually have it).
    Re-encoding keeps only the pixels: nothing of the uploaded file but the image survives."""
    image.seek(0)
    with Image.open(image) as source:
        _shrink_on_open(source, size)
        img = ImageOps.exif_transpose(source).convert("RGBA")
        img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    filename = f"{base_name}.png"
    return InMemoryUploadedFile(buffer, "ImageField", filename, "image/png", buffer.getbuffer().nbytes, None)
