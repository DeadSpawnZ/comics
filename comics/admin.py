from __future__ import annotations

import datetime
from typing import Any

from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.db.models import Count, QuerySet, Sum
from django.db.models.functions import TruncMonth
from django.forms import ModelForm
from django.forms.models import BaseModelFormSet
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.templatetags.static import static
from django.urls import URLPattern, path
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .forms import CollectionForm, EditionForm, save_formset_reinserting
from .models import (
    Achievement,
    AchievementCategory,
    AchievementTier,
    Announcement,
    Article,
    Artist,
    ChangeRequest,
    Collectable,
    CollectedIssue,
    Collection,
    Connecting,
    ConnectingPiece,
    Dealer,
    Edition,
    EditionCoverArtist,
    Editorial,
    EditorialAlias,
    Issue,
    IssueCreator,
    Notification,
    Profile,
    Publishing,
    ReadingArc,
    ReadingArcEntry,
    Signature,
    Title,
    UserAchievement,
)

admin.site.register(Signature)


class AchievementTierInline(admin.TabularInline):
    model = AchievementTier
    extra = 0


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    # Day-to-day editing happens in Gestión; the admin is the superusers' fallback.
    list_display = ("code", "rule", "name_en", "name_es", "category", "is_secret", "is_enabled")
    list_filter = ("category", "rule", "is_secret", "is_enabled")
    inlines = [AchievementTierInline]

    def save_formset(self, request: HttpRequest, form: ModelForm, formset: BaseModelFormSet, change: bool) -> None:
        save_formset_reinserting(formset)


@admin.register(AchievementCategory)
class AchievementCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name_en", "name_es", "display_order", "is_enabled")


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ("user", "achievement", "level", "awarded_at", "notified")
    list_filter = ("achievement",)
    list_select_related = ("user", "achievement")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    # Created in Gestión only: creating one there is what sends its notifications.
    list_display = ("title_en", "title_es", "audience", "created_by", "created_at")
    list_select_related = ("created_by",)
    # The audience is fixed once the notifications were sent.
    readonly_fields = ("audience", "roles", "created_by", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "achievement", "announcement", "level", "count", "created_at", "read_at")
    list_filter = ("kind",)
    list_select_related = ("recipient", "achievement", "announcement")
    search_fields = ("recipient__username",)
    raw_id_fields = ("recipient", "achievement", "announcement")

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Notifications are only created by the app (achievements, announcements).
        return False


@admin.register(ChangeRequest)
class ChangeRequestAdmin(admin.ModelAdmin):
    """Read-only: requests move only through their steps (the review area), which notify the
    author and apply approved changes through the history."""

    list_display = ("id", "kind", "action", "target_label", "status", "author", "created_at", "decided_at")
    list_filter = ("status", "kind", "action")
    list_select_related = ("author",)
    search_fields = ("target_label", "author__username", "reason")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: ChangeRequest | None = None) -> bool:
        return False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_public", "phone")
    list_filter = ("is_public",)
    list_select_related = ("user",)
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)


@admin.register(Dealer)
class DealerAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ["name"]
    ordering = ["name"]
    search_fields = ["name"]


class EditorialAliasInline(admin.TabularInline):
    """Other names of the editorial in the external catalogues (see EditorialAlias)."""

    model = EditorialAlias
    fields = ["name"]
    extra = 0


@admin.register(Editorial)
class EditorialAdmin(admin.ModelAdmin):
    list_display = ["name", "country"]
    ordering = ["name"]
    search_fields = ["name", "aliases__name"]
    inlines = [EditorialAliasInline]


@admin.register(Publishing)
class PublishingAdmin(admin.ModelAdmin):
    list_display = ("publishing_title", "title", "get_editorials", "serie", "year", "language")
    ordering = ["publishing_title"]
    search_fields = ["publishing_title"]
    filter_horizontal = ("editorials",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Publishing]:
        qs = super().get_queryset(request)
        return qs.prefetch_related("editorials")

    def get_editorials(self, obj: Publishing) -> str:
        return " / ".join(e.name for e in obj.editorials.all())

    get_editorials.short_description = "Editorials"


class EditionInline(admin.TabularInline):
    """Editions of an issue (read-only; they are edited from each edition)."""

    model = Edition
    fk_name = "issue"
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name = "edición"
    verbose_name_plural = "ediciones"
    fields = ["publishing", "number", "variant", "printing", "format", "release_date"]
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Issue | None = None) -> bool:
        return False


class IssueCreatorInline(admin.TabularInline):
    # The admin cannot show a many-to-many field with its own through model as a widget: the
    # creators are edited as rows instead.
    model = IssueCreator
    extra = 0
    autocomplete_fields = ["artist"]
    verbose_name = "creador"
    verbose_name_plural = "creadores"

    def get_queryset(self, request: HttpRequest) -> QuerySet[IssueCreator]:
        return super().get_queryset(request).select_related("artist")


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ["__str__", "publishing", "number", "edition_count", "first_release"]
    list_select_related = ["publishing"]
    ordering = ["publishing__publishing_title", "number"]
    search_fields = ["publishing__publishing_title", "number"]
    autocomplete_fields = ["publishing"]
    fields = ["publishing", "number", "synopsis"]
    inlines = [IssueCreatorInline, EditionInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Issue]:
        return super().get_queryset(request).with_first_release().annotate(edition_count=Count("editions"))

    @admin.display(description="Ediciones", ordering="edition_count")
    def edition_count(self, obj: Issue) -> int:
        return obj.edition_count

    @admin.display(description="1.ª impresión", ordering="first_release")
    def first_release(self, obj: Issue) -> datetime.date | str:
        return obj.first_release or "-"


class CollectedIssueInline(admin.TabularInline):
    model = CollectedIssue
    extra = 0
    fields = ["order", "issue"]
    autocomplete_fields = ["issue"]
    verbose_name = "issue recopilado"
    verbose_name_plural = "Compilación: issues que recopila (déjalo vacío si no es compilación)"

    def get_queryset(self, request: HttpRequest) -> QuerySet[CollectedIssue]:
        # Each row shows its issue (and its publishing) in the autocomplete and in __str__.
        return super().get_queryset(request).select_related("issue__publishing")


class EditionCoverArtistInline(admin.TabularInline):
    # Same as IssueCreatorInline: the cover artists are edited as rows.
    model = EditionCoverArtist
    extra = 0
    autocomplete_fields = ["artist"]
    verbose_name = "artista de portada"
    verbose_name_plural = "artistas de portada"

    def get_queryset(self, request: HttpRequest) -> QuerySet[EditionCoverArtist]:
        return super().get_queryset(request).select_related("artist")


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    class Media:
        js = (
            "js/custom/fill_release_date.js",
            "js/custom/thumbnail_preview.js",
            "js/custom/force_uppercase_variant.js",
        )

    form = EditionForm
    fieldsets = (
        (
            "Edition Info",
            {
                "fields": (
                    "publishing",
                    "number",
                    "variant",
                    "printing",
                    "ratio",
                    "limited_to",
                    "retailer_exclusive",
                    "event_exclusive",
                    "cover_price",
                    "format",
                    "rarity",
                    "release_date",
                )
            },
        ),
        ("Issue", {"fields": ("issue",)}),
        ("Images", {"fields": ("image", "thumbnail_preview", "thumbnail")}),
        ("Extras", {"fields": ("notes", "mycomicshop_id"), "classes": ("collapse",)}),
    )
    inlines = [EditionCoverArtistInline, CollectedIssueInline]
    autocomplete_fields = ["issue"]

    list_display = [
        "get_comic",
        "number",
        "variant",
        "format",
        "rarity",
        "get_serie",
        "printing",
        "get_release_date",
        "ratio",
        "country",
    ]
    ordering = ["publishing__publishing_title", "number", "variant"]
    search_fields = ["publishing__publishing_title"]
    readonly_fields = ["country", "thumbnail_preview"]
    list_select_related = ("publishing",)  # Optimize queries by selecting related publishing

    def save_formset(self, request: HttpRequest, form: ModelForm, formset: BaseModelFormSet, change: bool) -> None:
        if formset.model is CollectedIssue:
            save_formset_reinserting(formset)
        else:
            super().save_formset(request, form, formset, change)

    def save_related(
        self, request: HttpRequest, form: ModelForm, formsets: list[BaseModelFormSet], change: bool
    ) -> None:
        super().save_related(request, form, formsets, change)
        # Collected issues are saved after the edition: only now is it known whether it is a compilation.
        form.instance.sync_compilation_state()

    def get_queryset(self, request: HttpRequest) -> QuerySet[Edition]:
        qs = super().get_queryset(request)
        return qs.prefetch_related("publishing__editorials")

    def _from_publishing(self, obj: Edition, attr: str) -> Any:
        return getattr(obj.publishing, attr, None)

    @admin.display(ordering="publishing__publishing_title", description="comic")
    def get_comic(self, obj: Edition) -> str | None:
        return self._from_publishing(obj, "publishing_title")

    @admin.display(ordering="publishing__serie", description="serie")
    def get_serie(self, obj: Edition) -> str | None:
        return self._from_publishing(obj, "serie")

    @admin.display(description="release date")
    def get_release_date(self, obj: Edition) -> str:
        if obj.release_date:
            return obj.release_date.strftime("%b %Y")
        return "-"

    @admin.display(description="Country")
    def country(self, obj: Edition) -> str:
        # Read from the prefetch_related("publishing__editorials") cache instead of
        # running an Editorial query for every row in the list.
        editorials = obj.publishing.editorials.all()
        editorial = editorials[0] if editorials else None
        if editorial and editorial.country:
            icon_url = static(f"images/{editorial.country}.png")
            return format_html('<img src="{}" style="width:18px">', icon_url)
        return "-"

    @admin.display(description="Thumbnail Preview")
    def thumbnail_preview(self, obj: Edition) -> SafeString:
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
    list_select_related = ["edition", "edition__publishing", "participant"]

    fieldsets = (
        ("Collector Information", {"fields": ("collector",)}),
        ("Edition Info", {"fields": ("publishing", "edition")}),
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
        "edition__publishing__publishing_title",
        "edition__number",
        "edition__variant",
        "trade_date",
    ]
    search_fields = ["edition__publishing__publishing_title"]
    list_filter = ["participant"]

    def _from_edition(self, obj: Collection, attr: str) -> Any:
        return getattr(obj.edition, attr, None)

    def _from_publishing(self, obj: Collection, attr: str) -> Any:
        return getattr(obj.edition.publishing, attr, None)

    @admin.display(ordering="edition__publishing__publishing_title", description="Publishing Title")
    def get_publishing_title(self, obj: Collection) -> str | None:
        return self._from_publishing(obj, "publishing_title")

    @admin.display(ordering="edition__number", description="number")
    def get_number(self, obj: Collection) -> str | None:
        return self._from_edition(obj, "number")

    @admin.display(ordering="edition__variant", description="variant")
    def get_variant(self, obj: Collection) -> str | None:
        return self._from_edition(obj, "variant")

    @admin.display(ordering="edition__format", description="format")
    def get_format(self, obj: Collection) -> str:
        return obj.edition.get_format_display()

    @admin.display(ordering="trade_date", description="acquisition")
    def get_acquisition(self, obj: Collection) -> str:
        return obj.trade_date.strftime("%d %B %Y / %A")

    @admin.display(ordering="edition__publishing__serie", description="serie")
    def get_serie(self, obj: Collection) -> str | None:
        return self._from_publishing(obj, "serie")

    @admin.display(description="Trade type", ordering="trade_type")
    def trade_type_colored(self, obj: Collection) -> str:
        if obj.trade_type == Collection.TradeChoices.SELLING and obj.previous_trade is None:
            return format_html(
                '<span style="color: purple; font-weight: bold;">{}</span>', obj.get_trade_type_display()
            )
        return obj.get_trade_type_display()

    def get_urls(self) -> list[URLPattern]:
        urls = super().get_urls()
        custom_urls = [
            path("stats/", self.admin_site.admin_view(self.stats_view), name="collection-stats"),
        ]
        return custom_urls + urls

    def stats_view(self, request: HttpRequest) -> TemplateResponse:
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


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "limited_to", "piece_count"]
    ordering = ["name"]
    search_fields = ["name", "description"]
    list_filter = ["category"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
        return super().get_queryset(request).annotate(piece_count=Count("pieces"))

    @admin.display(description="Pieces", ordering="piece_count")
    def piece_count(self, obj: Article) -> int:
        return obj.piece_count


@admin.register(Collectable)
class CollectableAdmin(admin.ModelAdmin):
    list_display = ["article", "collector", "amount", "trade_date", "trade_type", "participant"]
    list_select_related = ["article", "collector", "participant"]
    ordering = ["article__name", "trade_date"]
    search_fields = ["article__name", "collector__username", "participant__name"]
    list_filter = ["trade_type", "article__category", "trade_date", "participant"]
    autocomplete_fields = ["article", "participant", "previous_trade"]


class ConnectingPieceInline(admin.TabularInline):
    model = ConnectingPiece
    extra = 0
    fields = ["row", "column", "edition"]
    autocomplete_fields = ["edition"]


@admin.register(Connecting)
class ConnectingAdmin(admin.ModelAdmin):
    list_display = ["name", "layout", "piece_count"]
    search_fields = ["name"]
    fields = ["name", "rows", "columns", "notes"]
    inlines = [ConnectingPieceInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Connecting]:
        return super().get_queryset(request).annotate(piece_count=Count("pieces"))

    @admin.display(description="Disposición")
    def layout(self, obj: Connecting) -> str:
        return obj.layout

    @admin.display(description="Piezas", ordering="piece_count")
    def piece_count(self, obj: Connecting) -> str:
        return f"{obj.piece_count} / {obj.rows * obj.columns}"

    def save_formset(self, request: HttpRequest, form: ModelForm, formset: BaseModelFormSet, change: bool) -> None:
        if formset.model is ConnectingPiece:
            save_formset_reinserting(formset)
        else:
            super().save_formset(request, form, formset, change)

    def change_view(
        self, request: HttpRequest, object_id: str, form_url: str = "", extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        extra_context = extra_context or {}
        connecting = self.get_object(request, unquote(object_id))
        if connecting is not None:
            grid = connecting.grid()
            extra_context["preview_grid"] = grid
            extra_context["piece_total"] = sum(1 for row in grid for cell in row if cell)
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


class ReadingArcEntryInline(admin.TabularInline):
    model = ReadingArcEntry
    extra = 0
    fields = ["order", "issue"]
    autocomplete_fields = ["issue"]


@admin.register(ReadingArc)
class ReadingArcAdmin(admin.ModelAdmin):
    list_display = ["name", "entry_count"]
    search_fields = ["name"]
    fields = ["name", "notes"]
    inlines = [ReadingArcEntryInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[ReadingArc]:
        return super().get_queryset(request).annotate(entry_count=Count("entries"))

    @admin.display(description="Issues", ordering="entry_count")
    def entry_count(self, obj: ReadingArc) -> int:
        return obj.entry_count

    def save_formset(self, request: HttpRequest, form: ModelForm, formset: BaseModelFormSet, change: bool) -> None:
        if formset.model is ReadingArcEntry:
            save_formset_reinserting(formset)
        else:
            super().save_formset(request, form, formset, change)
