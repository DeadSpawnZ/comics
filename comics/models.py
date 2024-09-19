from datetime import datetime, date as datedate
from django.contrib import admin
from django.db.models.functions import Concat
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now
from django.db.models import (
    CharField,
    DateField,
    TextField,
    IntegerField,
    ManyToManyField,
    ForeignKey,
    IntegerField,
    F,
    Model,
    TextChoices,
    SET_NULL,
    SET_DEFAULT,
    PROTECT,
    DecimalField,
    BooleanField,
    ImageField,
)


# Create your models here.
class Printing(Model):
    name = CharField(max_length=10)

    def __str__(self):
        return self.name


class Editorial(Model):
    class CountryAbbr(TextChoices):
        MX = "MX"
        US = "US"
        DE = "DE"
        ES = "ES"

    name = CharField(max_length=30, unique=True)
    country = CharField(max_length=3, choices=CountryAbbr)
    # titles = ManyToManyField(Person, through="Membership")

    def __str__(self):
        return self.name


class Title(Model):
    name = CharField(max_length=70, unique=True)

    def __str__(self):
        return self.name


def current_year():
    return datetime.now().year


class Publishing(Model):
    def max_value_current_year(value):
        return MaxValueValidator(current_year())(value)

    class LangAbbr(TextChoices):
        EN = "en"
        ES = "es"
        DE = "de"

    title = ForeignKey(Title, on_delete=PROTECT, null=True)
    publishing_title = CharField(max_length=70)
    serie = CharField(max_length=20, default="1st")
    printing = ForeignKey(Printing, on_delete=PROTECT, null=True)
    language = CharField(max_length=5, choices=LangAbbr)
    editorials = ManyToManyField(Editorial)
    date = DateField(default=datetime.now)
    year = IntegerField(
        _("year"), validators=[MinValueValidator(1970), max_value_current_year]
    )

    def __str__(self):
        editorials = Editorial.objects.filter(publishing=self)
        first_editorial = editorials[0] if editorials else None
        country_code = first_editorial.country.upper() if first_editorial else ""
        return (
            str(self.publishing_title)
            + " ("
            + str(self.year)
            + ") "
            + self.serie
            + " series "
            + self.printing.name
            + " Print"
            + " "
            + country_code
            + "-"
            + self.language.upper()
        )

    def save(self, *args, **kwargs):
        self.publishing_title = self.publishing_title.strip()

        coincidences = (
            Publishing.objects.filter(publishing_title=self.publishing_title)
            .filter(year=self.year)
            .filter(serie=self.serie)
            .filter(printing__name__contains=self.printing)
            .filter(language=self.language)
        )

        if hasattr(self, "id"):
            coincidences = coincidences.exclude(id=self.id)
        if coincidences.exists():
            msg = "Duplicated publishing"
            raise Exception(msg)
        super(Publishing, self).save(*args, **kwargs)


class Artist(Model):
    name = CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Comic(Model):
    publishing = ForeignKey(Publishing, on_delete=PROTECT, null=True)
    image = ImageField(upload_to="images/", null=True, blank=True)
    number = IntegerField()
    variant = CharField(max_length=30, default="A", blank=True)
    ratio = CharField(
        max_length=10,
        blank=True,
        validators=[
            RegexValidator(
                regex="^[0-9]{1,3}+[\:][0-9]{1,3}$",
                message="Ratio must be a valid relation Example: (1:100)",
                code="invalid_ratio",
            ),
        ],
    )
    limited_to = CharField(
        max_length=10,
        blank=True,
        validators=[
            RegexValidator(
                regex="^[0-9]{0,10}+$",
                message="Not a valid number",
                code="invalid_limit",
            ),
        ],
    )
    price = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    release_date = DateField(default=datetime.now)
    details = TextField(max_length=500, blank=True)
    artists = ManyToManyField(Artist, blank=True)

    def __str__(self):
        editorials = Editorial.objects.filter(publishing=self.publishing)
        first_editorial = editorials[0] if editorials else None
        country_code = first_editorial.country.upper() if first_editorial else ""

        return """{publishing_title} #{number} {variant} {serie} {printing} {country}-{language} {year}""".format(
            publishing_title=self.publishing.publishing_title,
            number=str(self.number),
            variant=self.variant,
            serie=self.publishing.serie,
            printing=self.publishing.printing.name,
            country=country_code,
            language=self.publishing.language.upper(),
            year=str(self.publishing.year),
        )

    def save(self, *args, **kwargs):
        self.validate_duplicate()
        self.process_image()

        super(Comic, self).save(*args, **kwargs)

    def validate_duplicate(self):
        self.variant = self.variant.upper().strip()

        coincidences = (
            Comic.objects.filter(
                publishing__publishing_title__exact=self.publishing.publishing_title
            )
            .filter(number=self.number)
            .filter(variant=self.variant)
            .filter(publishing__serie__exact=self.publishing.serie)
            .filter(publishing__printing__exact=self.publishing.printing)
            .filter(publishing__year__exact=self.publishing.year)
        )

        if hasattr(self, "id"):
            coincidences = coincidences.exclude(id=self.id)
        if coincidences.exists():
            msg = "Duplicated comic"
            raise Exception(msg)

    def process_image(self) -> None:
        if not self.image:
            return

        import io
        from PIL import Image
        from django.core.files.uploadedfile import InMemoryUploadedFile
        import time, datetime

        temp_image = Image.open(self.image)
        width, height = temp_image.size
        new_size: tuple[int, int] = (1080, int(1080 * height / width))
        new_img = temp_image.resize(new_size, Image.Resampling.LANCZOS)

        img_io = io.BytesIO()
        new_img.save(img_io, format="JPEG")
        img_io.seek(0)

        ts = time.time()
        timestamp = datetime.datetime.fromtimestamp(ts).strftime("%d-%m-%Y %H:%M:%S")
        new_image_name = f"{self.publishing.publishing_title}_{self.number}_{self.variant}_{timestamp}.jpg"
        new_image = InMemoryUploadedFile(
            img_io,
            "ImageField",
            new_image_name,
            "image/jpeg",
            img_io.getbuffer().nbytes,
            None,
        )
        self.image = new_image


class Collector(Model):
    name = CharField(max_length=100, unique=True)
    comics = ManyToManyField(Comic, through="Collection")

    def __str__(self):
        return self.name


class Dealer(Model):
    name = CharField(max_length=100)
    real_name = CharField(max_length=100, blank=True)
    fb = CharField(max_length=150, blank=True)

    def __str__(self):
        return self.name


class Collection(Model):
    TITLE_CHOICES = [
        ("buying", "Buying"),
        ("selling", "Selling"),
    ]
    collector = ForeignKey(Collector, on_delete=PROTECT, null=True)
    comic = ForeignKey(Comic, on_delete=PROTECT, null=True)
    amount = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    trade_date = DateField(default=datetime.now)
    trade_type = CharField(max_length=50, choices=TITLE_CHOICES, default="buying")
    participant = ForeignKey(Dealer, on_delete=PROTECT, null=True, blank=True)
    selled = BooleanField(default=False)
    buyer = CharField(max_length=100, blank=True)
    sale_date = DateField(blank=True, null=True)
    sale_price = DecimalField(max_digits=6, decimal_places=2, default=0.00)

    def __str__(self):
        return self.comic.publishing.publishing_title


class StoryArc(Model):
    name = CharField(max_length=100, unique=True)
    publishings = ManyToManyField(Publishing)
    order = IntegerField()
