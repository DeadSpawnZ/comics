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
    Collection
)

admin.site.register(Artist)
admin.site.register(Printing)
admin.site.register(Collector)
admin.site.register(Collection)

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
    list_display = ["__str__", "variant", "get_printing", "get_year", "release_date"]
    ordering = ["publishing__publishing_title", "number", "variant"]
    search_fields = ["__str__"]
    filter_horizontal = ('artists',)

    @admin.display(ordering='publishing__printing', description='printing')
    def get_printing(self, obj):
        return obj.publishing.printing
    
    @admin.display(ordering='publishing__year', description='year')
    def get_year(self, obj):
        return obj.publishing.year