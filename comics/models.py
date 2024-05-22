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
    GeneratedField
)

# Create your models here.

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

class Publishing(Model):
    class LangAbbr(TextChoices):
        EN = 'en'
        ES = 'es'
    language = CharField(max_length=5, choices=LangAbbr)
    publishing_title = CharField(max_length=50)
    printing = CharField(max_length=10)
    editorials = ManyToManyField(Editorial)
    title = ForeignKey(Title, on_delete=SET_NULL, null=True)

    def __str__(self):
        return str(self.title.name)+' ('+str(self.printing)+') '

class Artist(Model):
    name = CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

class Comic(Model):
    name = GeneratedField(
        expression=F('number'),
        output_field=CharField(max_length=100),
        db_persist=True)
    # name = CharField(max_length=100, default='')
    tag_name = CharField(max_length=100)
    serie = CharField(max_length=20)
    number = IntegerField(primary_key=True)
    variant = CharField(max_length=30)
    price = IntegerField()
    release_date = DateField()
    publishing = ForeignKey(Publishing, on_delete=SET_NULL, null=True)
    artists = ManyToManyField(Artist)

    def __str__(self):
        return self.publishing.publishing_title + '#' + str(self.number)