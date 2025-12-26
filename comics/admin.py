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
    Collection,
    Dealer,
    Signature,
    GeekCollectable,
)
from .forms import CollectionForm


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
    list_display = ("publishing_title", "title", "get_editorials", "serie", "year", "language")
    ordering = ["publishing_title"]
    search_fields = ["publishing_title"]
    filter_horizontal = ("editorials",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("editorials")

    def get_editorials(self, obj):
        return " / ".join(e.name for e in obj.editorials.all())

    get_editorials.short_description = "Editorials"


@admin.register(Comic)
class ComicAdmin(admin.ModelAdmin):
    class Media:
        js = (
            "js/custom/fill_release_date.js",
            "js/custom/thumbnail_preview.js",
            "js/custom/force_uppercase_variant.js",
        )

    fieldsets = (
        (
            "Comic Info",
            {
                "fields": (
                    "publishing",
                    "number",
                    "variant",
                    "printing",
                    "ratio",
                    "limited_to",
                    "cover_price",
                    "format",
                    "release_date",
                )
            },
        ),
        ("Images", {"fields": ("image", "thumbnail_preview", "thumbnail")}),
        ("Extras", {"fields": ("details", "artists"), "classes": ("collapse",)}),
        ("Compilation", {"fields": ("is_compilation", "compiled_issues")}),
    )

    list_display = [
        "get_comic",
        "number",
        "variant",
        "format",
        "get_serie",
        "printing",
        "get_release_date",
        "ratio",
        "country",
    ]
    ordering = ["publishing__publishing_title", "number", "variant"]
    search_fields = ["publishing__publishing_title"]
    filter_horizontal = ("artists", "compiled_issues")
    readonly_fields = ["country", "thumbnail_preview"]
    list_select_related = ("publishing",)  # Optimize queries by selecting related publishing

    def _from_publishing(self, obj, attr):
        return getattr(obj.publishing, attr, None)

    @admin.display(ordering="publishing__publishing_title", description="comic")
    def get_comic(self, obj):
        return self._from_publishing(obj, "publishing_title")

    @admin.display(ordering="publishing__serie", description="serie")
    def get_serie(self, obj):
        return self._from_publishing(obj, "serie")

    @admin.display(description="release date")
    def get_release_date(self, obj):
        if obj.release_date:
            return obj.release_date.strftime("%b %Y")
        return "-"

    @admin.display(description="Country")
    def country(self, obj):
        editorial = Editorial.objects.filter(publishing=obj.publishing).first()
        if editorial and editorial.country:
            icon_url = f"/static/images/{editorial.country.lower()}.png"
            return format_html('<img src="{}" style="width:18px">', icon_url)
        return "-"

    @admin.display(description="Thumbnail Preview")
    def thumbnail_preview(self, obj):
        if obj.thumbnail and obj.thumbnail.url:
            return format_html('<img id="thumb-preview" src="{}" style="max-height: 200px;" />', obj.thumbnail.url)
        return format_html('<img id="thumb-preview" style="max-height: 200px; display:none;" />')


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    search_fields = ["name"]


class SignatureInline(admin.TabularInline):
    model = Signature
    extra = 0
    # autocomplete_fields = ["artist"]


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    class Media:
        js = ("js/custom/collection_form_events.js",)

    form = CollectionForm
    change_list_template = "admin/collection_change_list.html"
    list_select_related = ["comic", "comic__publishing", "participant"]

    fieldsets = (
        ("Collector Information", {"fields": ("collector",)}),
        ("Comic Info", {"fields": ("publishing", "comic")}),
        ("Trade Details", {"fields": ("amount", "trade_date", "trade_type", "participant")}),
        ("Extras", {"fields": ("valuation", "previous_trade", "notes"), "classes": ("collapse",)}),
    )
    inlines = [SignatureInline]

    list_display = [
        "get_publishing_title",
        "get_number",
        "get_variant",
        "get_format",
        "amount",
        "get_acquisition",
        "participant",
        "get_serie",
        "trade_type_colored",
    ]
    ordering = [
        "comic__publishing__publishing_title",
        "comic__number",
        "comic__variant",
        "trade_date",
    ]
    search_fields = ["comic__publishing__publishing_title"]
    list_filter = ["participant"]

    def _from_comic(self, obj, attr):
        return getattr(obj.comic, attr, None)

    def _from_publishing(self, obj, attr):
        return getattr(obj.comic.publishing, attr, None)

    @admin.display(ordering="comic__publishing__publishing_title", description="Publishing Title")
    def get_publishing_title(self, obj):
        return self._from_publishing(obj, "publishing_title")

    @admin.display(ordering="comic__number", description="number")
    def get_number(self, obj):
        return self._from_comic(obj, "number")

    @admin.display(ordering="comic__variant", description="variant")
    def get_variant(self, obj):
        return self._from_comic(obj, "variant")

    @admin.display(ordering="comic__format", description="format")
    def get_format(self, obj):
        return obj.comic.get_format_display()

    @admin.display(ordering="trade_date", description="acquisition")
    def get_acquisition(self, obj):
        return obj.trade_date.strftime("%d %B %Y / %A")

    @admin.display(ordering="comic__publishing__serie", description="serie")
    def get_serie(self, obj):
        return self._from_publishing(obj, "serie")

    @admin.display(description="Trade type", ordering="trade_type")
    def trade_type_colored(self, obj):
        if obj.trade_type == Collection.TradeChoices.SELLING and obj.previous_trade is None:
            return format_html(
                '<span style="color: purple; font-weight: bold;">{}</span>', obj.get_trade_type_display()
            )
        return obj.get_trade_type_display()

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("stats/", self.admin_site.admin_view(self.stats_view), name="collection-stats"),
        ]
        return custom_urls + urls

    def stats_view(self, request):
        excluded_ids = Collection.objects.exclude(previous_trade=None).values_list("previous_trade_id", flat=True)
        data = (
            Collection.objects.filter(trade_type=Collection.TradeChoices.BUYING)
            .exclude(id__in=excluded_ids)
            .annotate(month=TruncMonth("trade_date"))
            .values("month")
            .annotate(total=Sum("amount"))
            .order_by("month")
        )

        labels = [entry["month"].strftime("%B %Y") for entry in data]
        totals = [float(entry["total"]) for entry in data]
        total_amount = float(sum(entry["total"] or 0 for entry in data))

        context = {
            **self.admin_site.each_context(request),
            "labels": labels,
            "totals": totals,
            "total_amount": total_amount,
            "title": "Collection Stats",
        }
        return TemplateResponse(request, "admin/collection_stats.html", context)

@admin.register(GeekCollectable)
class GeekCollectableAdmin(admin.ModelAdmin):
    list_display = ["name", "amount", "trade_date", "participant"]
    ordering = ["name"]
    search_fields = ["name", "participant__name"]
    list_filter = ["trade_date", "participant"]