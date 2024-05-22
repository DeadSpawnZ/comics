from django.db import models

# Create your models here.

class Editorial(models.Model):
    class CountryAbbr(models.TextChoices):
        MX = 'MX'
        US = 'US'
    name = models.CharField(max_length=30)
    country = models.CharField(max_length=3, choices=CountryAbbr)
    # titles = models.ManyToManyField(Person, through="Membership")

    def __str__(self):
        return self.name

class Title(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class Publishing(models.Model):
    class LangAbbr(models.TextChoices):
        EN = 'en'
        ES = 'es'
    language = models.CharField(max_length=5, choices=LangAbbr)
    publishing_title = models.CharField(max_length=50)
    printing = models.CharField(max_length=10)
    editorial = models.ForeignKey(Editorial, on_delete=models.SET_NULL, null=True)
    title = models.ForeignKey(Title, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.editorial+'-'+self.title+'-'+self.printing

class Comic(models.Model):
    name = models.CharField(max_length=100)
    tag_name = models.CharField(max_length=100)
    serie = models.CharField(max_length=20)
    number = models.IntegerField()
    variant = models.CharField(max_length=30)
    price = models.IntegerField()
    release_date = models.DateField()
    publishing = models.ForeignKey(Publishing, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.name