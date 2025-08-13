from django.contrib import admin
from django.utils.html import format_html
from django.urls import path
from django.template.response import TemplateResponse
from django.db.models.functions import TruncMonth
from django.db.models import Sum

# Register your models here.

from .models import (
    Comic,
    Editorial,
    Title,
    Publishing,
    Artist,
    Printing,
    Collection,
    Dealer,
    Signature,
)
from .forms import CollectionForm

admin.site.register(Artist)
admin.site.register(Printing)
admin.site.register(Signature)


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
    class Media:
        js = ("js/fill_release_date.js",)

    list_display = [
        "get_comic",
        "number",
        "variant",
        "get_format",
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
    list_select_related = ("publishing",)  # Optimize queries by selecting related publishing

    def _from_publishing(self, obj, attr):
        return getattr(obj.publishing, attr, None)

    @admin.display(ordering="publishing__publishing_title", description="comic")
    def get_comic(self, obj):
        return self._from_publishing(obj, "publishing_title")

    @admin.display(ordering="publishing__format", description="format")
    def get_format(self, obj):
        return obj.publishing.get_format_display()

    @admin.display(ordering="publishing__serie", description="serie")
    def get_serie(self, obj):
        return self._from_publishing(obj, "serie")

    @admin.display(ordering="publishing__printing", description="printing")
    def get_printing(self, obj):
        return self._from_publishing(obj, "printing")

    @admin.display(ordering="publishing__year", description="year")
    def get_year(self, obj):
        return self._from_publishing(obj, "year")

    @admin.display(description="Country")
    def country(self, obj):
        editorial = Editorial.objects.filter(publishing=obj.publishing).first()
        if editorial and editorial.country:
            icon_url = f"/static/{editorial.country.lower()}.png"
            return format_html('<img src="{}" style="width:18px">', icon_url)
        return "-"


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    class Media:
        js = ("js/filter_comics_by_publishing.js",)

    form = CollectionForm
    change_list_template = "admin/collection_change_list.html"

    fieldsets = (
        ("Collector Information", {"fields": ("collector",)}),
        (
            "Comic Info",
            {
                "fields": (
                    "publishing",
                    "comic",
                )
            },
        ),
        (
            "Trade Details",
            {
                "fields": (
                    "amount",
                    "trade_date",
                    "trade_type",
                    "participant",
                )
            },
        ),
        (
            "Extras",
            {
                "fields": ("valuation", "previous_trade"),
                "classes": ("collapse",),
            },
        ),
    )

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
    # filter_horizontal = ("signatures",)
    list_filter = ["participant"]

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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("stats/", self.admin_site.admin_view(self.stats_view), name="collection-stats"),
        ]
        return custom_urls + urls

    def stats_view(self, request):
        data = (
            Collection.objects.annotate(month=TruncMonth("trade_date"))
            .values("month")
            .annotate(total=Sum("amount"))
            .order_by("month")
        )

        labels = [entry["month"].strftime("%B %Y") for entry in data]
        totals = [float(entry["total"]) for entry in data]
        total_amount = Collection.objects.aggregate(total=Sum("amount"))["total"] or 0

        context = dict(
            self.admin_site.each_context(request),
            labels=labels,
            totals=totals,
            total_amount=total_amount,
            title="Collection Stats",
        )
        return TemplateResponse(request, "admin/collection_stats.html", context)
