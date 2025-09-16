class Series(models.Model):  # Publishing
    title = models.ForeignKey(Title, on_delete=models.CASCADE)
    volume_number = models.IntegerField()
    start_year = models.IntegerField()
    end_year = models.IntegerField(null=True, blank=True)


class Issue(models.Model):
    series = models.ForeignKey(Series, on_delete=models.CASCADE)
    issue_number = models.CharField(max_length=20)
    release_date = models.DateField()
    summary = models.TextField(blank=True)
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
    format = models.CharField(max_length=20, choices=EditionFormatChoices.choices)
    variant_cover = models.CharField(max_length=30, blank=True)
    ratio = models.CharField(max_length=10, blank=True)
    limited_to = models.CharField(max_length=10, blank=True)
    cover_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    image = models.ImageField(upload_to="images/originals/", null=True, blank=True)
    thumbnail = models.ImageField(upload_to="images/thumbnails/", null=True, blank=True)
    printing = models.IntegerField(null=True, blank=True)


class CompilationItem(models.Model):
    compilation = models.ForeignKey(
        Issue, on_delete=models.CASCADE, related_name="compilation_items"
    )  # is_compilation=True
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
