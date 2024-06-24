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
    DecimalField,
    BooleanField
)

# Create your models here.
class Printing(Model):
    name = CharField(max_length=10)

    def __str__(self):
        return self.name

class Editorial(Model):
    class CountryAbbr(TextChoices):
        MX = 'MX'
        US = 'US'
        DE = 'DE'
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
        EN = 'en'
        ES = 'es'
        DE = 'de'
    title = ForeignKey(Title, on_delete=SET_NULL, null=True)
    publishing_title = CharField(max_length=70)
    serie = CharField(max_length=20, default='1st')
    printing = ForeignKey(Printing, on_delete=SET_NULL, null=True)
    language = CharField(max_length=5, choices=LangAbbr)
    editorials = ManyToManyField(Editorial)
    date = DateField(default=now().date())
    year = IntegerField(_('year'), validators=[MinValueValidator(1970), max_value_current_year])

    def __str__(self):
        return str(self.publishing_title)+' ('+str(self.year)+') ' + self.serie + ' series ' + self.printing.name + ' Print' + ' ' + self.language.upper()

    def save(self, *args, **kwargs):
        self.publishing_title = self.publishing_title.strip()
        print(self.printing)
        coincidences = Publishing.objects.filter(
            publishing_title=self.publishing_title
        ).filter(
            year=self.year
        ).filter(
            serie=self.serie
        ).filter(
            printing__name__contains=self.printing
        ).filter(
            language=self.language
        )
        if hasattr(self, 'id'):
            coincidences = coincidences.exclude(id=self.id)
        if coincidences.exists():
            msg = 'Duplicated publishing'
            raise Exception(msg)
        super(Publishing, self).save(*args, **kwargs)

class Artist(Model):
    name = CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

class Comic(Model):
    publishing = ForeignKey(Publishing, on_delete=SET_NULL, null=True)
    number = IntegerField()
    variant = CharField(max_length=30, default='A', blank=True)
    ratio = CharField(max_length=10, blank=True, validators=[
        RegexValidator(
            regex='^[0-9]{1,3}+[\:][0-9]{1,3}$',
            message='Ratio must be a valid relation Example: (1:100)',
            code='invalid_ratio'
        ),
    ])
    limited_to = CharField(max_length=10, blank=True, validators=[
        RegexValidator(
            regex='^[0-9]{0,10}+$',
            message='Not a valid number',
            code='invalid_limit'
        ),
    ])
    price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    release_date = DateField(default=datetime.now)
    details = TextField(max_length=500, blank=True)
    artists = ManyToManyField(Artist, blank=True)

    def __str__(self):
        return self.publishing.publishing_title + ' #' + str(self.number) + ' ' + self.variant + ' ' + self.publishing.serie + ' ' + self.publishing.printing.name + ' ' + self.publishing.language.upper()

    def save(self, *args, **kwargs):
        self.variant = self.variant.upper().strip()

        coincidences = Comic.objects.filter(publishing__publishing_title__exact=self.publishing.publishing_title).filter(number=self.number).filter(variant=self.variant)

        if hasattr(self, 'id'):
            coincidences = coincidences.exclude(id=self.id)
        if coincidences.exists():
            msg = 'Duplicated comic'
            raise Exception(msg)
        super(Comic, self).save(*args, **kwargs)

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
    collector = ForeignKey(Collector, on_delete=SET_NULL, null=True)
    comic = ForeignKey(Comic, on_delete=SET_NULL, null=True)
    purchase_price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    acquisition_date = DateField(default=datetime.now)
    dealer = ForeignKey(Dealer, on_delete=SET_NULL, null=True, blank=True)
    selled = BooleanField(default=False)
    sale_price = DecimalField(max_digits=6, decimal_places=2, default=0.00)

    def __str__(self):
        return self.comic.__str__()

class StoryArc(Model):
    name = CharField(max_length=100, unique=True)
    publishings = ManyToManyField(Publishing)
    order = IntegerField()