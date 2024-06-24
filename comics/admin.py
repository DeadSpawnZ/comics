from django.contrib import admin

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
    Dealer
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
    list_display = ["__str__", "title", "year", "serie", "language"]
    ordering = ["publishing_title"]
    search_fields = ["publishing_title"]
    filter_horizontal = ('editorials',)

@admin.register(Comic)
class ComicAdmin(admin.ModelAdmin):
    list_display = ["__str__", "number", "variant", "get_printing", "get_year", "get_serie", "ratio", "release_date"]
    ordering = ["publishing__publishing_title", "number", "variant"]
    search_fields = ["publishing__publishing_title"]
    filter_horizontal = ('artists',)

    @admin.display(ordering='publishing__printing', description='printing')
    def get_printing(self, obj):
        return obj.publishing.printing
    
    @admin.display(ordering='publishing__year', description='year')
    def get_year(self, obj):
        return obj.publishing.year

    @admin.display(ordering='publishing__serie', description='serie')
    def get_serie(self, obj):
        return obj.publishing.serie

@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ["__str__", "get_number", "purchase_price", "acquisition", "dealer", "selled"]
    ordering = ["comic__publishing__publishing_title", "comic__number", "comic__variant", "acquisition_date"]
    search_fields = ["comic__publishing__publishing_title"]

    def acquisition(self, obj):
        return obj.acquisition_date.strftime("%d %B %Y / %A")

    @admin.display(ordering='comic__number', description='number')
    def get_number(self, obj):
        return obj.comic.number