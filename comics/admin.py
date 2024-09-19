from django.contrib import admin
from django.utils.html import format_html

# Register your models here.

from .models import (
    Comic,
    Editorial,
    Title,
    Publishing,
    Artist,
    Printing,
    Collector,
    Collection,
    Dealer,
)

admin.site.register(Artist)
admin.site.register(Printing)
admin.site.register(Collector)


@admin.register(Dealer)
class TitleAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ["name"]
    ordering = ["name"]
    search_fields = ["name"]


@admin.register(Editorial)
class EditorialAdmin(admin.ModelAdmin):
    list_display = ["name", "country"]
    ordering = ["name"]


@admin.register(Publishing)
class PublishingAdmin(admin.ModelAdmin):
    list_display = [
        "publishing_title",
        "title",
        "serie",
        "get_printing",
        "year",
        "language",
    ]
    ordering = ["publishing_title"]
    search_fields = ["publishing_title"]
    filter_horizontal = ("editorials",)

    @admin.display(ordering="printing", description="printing")
    def get_printing(self, obj):
        return obj.printing


@admin.register(Comic)
class ComicAdmin(admin.ModelAdmin):
    list_display = [
        "get_comic",
        "number",
        "variant",
        "get_serie",
        "get_printing",
        "get_year",
        "ratio",
        "country",
    ]
    ordering = ["publishing__publishing_title", "number", "variant"]
    search_fields = ["publishing__publishing_title"]
    filter_horizontal = ("artists",)
    readonly_fields = ["country"]

    @admin.display(ordering="publishing__printing", description="printing")
    def get_printing(self, obj):
        return obj.publishing.printing

    @admin.display(ordering="publishing__year", description="year")
    def get_year(self, obj):
        return obj.publishing.year

    @admin.display(ordering="publishing__serie", description="serie")
    def get_serie(self, obj):
        return obj.publishing.serie

    @admin.display(ordering="publishing__publishing_title", description="comic")
    def get_comic(self, obj):
        return obj.publishing.publishing_title

    def country(self, obj):
        publishing = obj.publishing
        editorials = Editorial.objects.filter(publishing=publishing)
        first_editorial = editorials[0] if editorials else None
        if first_editorial:
            country_code = first_editorial.country.lower()
            icon_url = "/static/" + country_code + ".png"
            return format_html('<img src="{}" style="width:18px">', icon_url)
        else:
            None


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = [
        "__str__",
        "get_number",
        "get_variant",
        "amount",
        "get_acquisition",
        "participant",
        "get_serie",
    ]
    ordering = [
        "comic__publishing__publishing_title",
        "comic__number",
        "comic__variant",
        "trade_date",
    ]
    search_fields = ["comic__publishing__publishing_title"]

    @admin.display(ordering="trade_date", description="acquisition")
    def get_acquisition(self, obj):
        return obj.trade_date.strftime("%d %B %Y / %A")

    @admin.display(ordering="comic__number", description="number")
    def get_number(self, obj):
        return obj.comic.number

    @admin.display(ordering="comic__variant", description="variant")
    def get_variant(self, obj):
        return obj.comic.variant

    @admin.display(ordering="comic__publishing__serie", description="serie")
    def get_serie(self, obj):
        return obj.comic.publishing.serie
