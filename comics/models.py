from datetime import datetime
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
    language = CharField(max_length=5, choices=LangAbbr)
    publishing_title = CharField(max_length=50)
    editorials = ManyToManyField(Editorial)
    printing = ForeignKey(Printing, on_delete=SET_NULL, null=True)
    title = ForeignKey(Title, on_delete=SET_NULL, null=True)
    year = IntegerField(_('year'), validators=[MinValueValidator(1984), max_value_current_year])

    def __str__(self):
        return str(self.publishing_title)+' ('+str(self.year)+')'

class Artist(Model):
    name = CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

class Comic(Model):
    number = IntegerField()
    serie = CharField(max_length=20, default='1st')
    variant = CharField(max_length=30, default='A')
    price = DecimalField(max_digits=6, decimal_places=2, default=0.00)
    release_date = DateField(default=datetime.now)
    publishing = ForeignKey(Publishing, on_delete=SET_NULL, null=True)
    artists = ManyToManyField(Artist)

    def __str__(self):
        return self.publishing.publishing_title + ' #' + str(self.number) + self.variant
