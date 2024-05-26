from datetime import datetime
from django.contrib import admin
from django.db.models.functions import Concat
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import gettext_lazy as _
from django.db.models import (
    CharField,
    DateField,
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
    name = CharField(max_length=30, unique=True)
    country = CharField(max_length=3, choices=CountryAbbr)
    # titles = ManyToManyField(Person, through="Membership")

    def __str__(self):
        return self.name

class Title(Model):
    name = CharField(max_length=50, unique=True)

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
    title = ForeignKey(Title, on_delete=SET_NULL, null=True)
    publishing_title = CharField(max_length=50)
    serie = CharField(max_length=20, default='1st')
    printing = ForeignKey(Printing, on_delete=SET_NULL, null=True)
    language = CharField(max_length=5, choices=LangAbbr)
    editorials = ManyToManyField(Editorial)
    year = IntegerField(_('year'), validators=[MinValueValidator(1984), max_value_current_year])

    def __str__(self):
        return str(self.publishing_title)+' ('+str(self.year)+') ' + self.printing.name + ' Print'

    def save(self, *args, **kwargs):
        self.publishing_title = self.publishing_title.strip()
        print(self.printing)
        coincidences = Publishing.objects.filter(publishing_title=self.publishing_title).filter(year=self.year).filter(printing__name__contains=self.printing)
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
    variant = CharField(max_length=30, default='A')
    price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    release_date = DateField(default=datetime.now)
    artists = ManyToManyField(Artist, blank=True)

    def __str__(self):
        return self.publishing.publishing_title + ' #' + str(self.number) + ' ' + self.variant

    def save(self, *args, **kwargs):
        self.variant = self.variant.upper().strip()

        coincidences = Comic.objects.filter(publishing__name__contains=self.publishing).filter(number=self.number).filter(variant=self.variant)
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

class Collection(Model):
    collector_id = ForeignKey(Collector, on_delete=SET_NULL, null=True)
    comic_id = ForeignKey(Comic, on_delete=SET_NULL, null=True)
    purchase_price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    acquisition_date = DateField(default=datetime.now)
    dealer = CharField(max_length=100, blank=True)