class Publishing(models.Model):  # Publishing
    title = models.ForeignKey(Title, on_delete=models.CASCADE)
    serie = models.CharField(max_length=200)
    start_year = models.IntegerField()
    end_year = models.IntegerField(null=True, blank=True)


class Issue(models.Model):
    series = models.ForeignKey(Series, on_delete=models.CASCADE)
    number = IntegerField()
    release_date = models.DateField()
    artists = models.ManyToManyField(Artist, blank=True)
    is_compilation = models.BooleanField(default=False)


class EditionFormatChoices(models.TextChoices):
    SINGLE_ISSUE = "single_issue", "Grapa"
    PRESTIGE = "prestige", "Prestige"
    TRADE_PAPERBACK = "trade_paperback", "TPB"
    HARDCOVER = "hardcover", "HC"
    ASHCAN = "ashcan", "Ashcan"
    DIGITAL = "digital", "Digital"


class Edition(models.Model):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="editions")
    format = CharField(max_length=20, choices=FormatChoices, default=FormatChoices.SINGLE_ISSUE)
    variant = CharField(max_length=30, default="A", blank=True)
    ratio = CharField(max_length=10, blank=True, validators=ratio_validator)
    limited_to = CharField(max_length=10, blank=True, validators=limited_to_validator)
    cover_price = DecimalField(max_digits=8, decimal_places=2, default=0.00)
    image = ImageField(upload_to="images/originals/", null=True, blank=True)
    thumbnail = ImageField(upload_to="images/thumbnails/", null=True, blank=True)
    printing = CharField(max_length=10, choices=PrintingChoices.choices, default=PrintingChoices.FIRST)


class CompilationItem(models.Model):
    compilation = models.ForeignKey(
        Issue, on_delete=models.CASCADE, related_name="compilation_items"
    )  # is_compilation=True
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
