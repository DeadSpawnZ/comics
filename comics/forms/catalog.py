"""Gestión catalogue forms: editions, issues, publishings, titles, editorials, artists, imports and history."""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import QuerySet, prefetch_related_objects
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _

from ..catalog_sources import service as catalog_service
from ..catalog_sources.errors import CoverRejected, series_required_message
from ..catalog_sources.http import MAX_COVER_BYTES, download_cover, is_allowed_cover_url
from ..models import Artist, Edition, Editorial, Issue, Publishing, Title
from .base import LimitedImageField, MaterialFieldsMixin, PlaceholderSelect, RequiredHintsMixin, UniqueNameMixin


class EditionForm(forms.ModelForm):
    class Meta:
        model = Edition
        # Same fields and order that "__all__" produced (every editable Edition field, M2M last).
        fields = [
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
            "image",
            "thumbnail",
            "notes",
            "mycomicshop_id",
            "cover_artists",
            "issue",
            "collected_issues",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["publishing"].queryset = Publishing.objects.order_by(
            "publishing_title",
            "year",
            "serie",
        )


def publishing_label(publishing: Publishing) -> str:
    """Publishing.select_label, as a function (label_from_instance); prefetch the editorials (see
    publishing_choices) to avoid one query per option."""
    return publishing.select_label


def publishings_for_select() -> QuerySet[Publishing]:
    return Publishing.objects.prefetch_related("editorials").order_by("publishing_title", "year", "serie")


def publishing_choices() -> list[tuple[int, str]]:
    """[(pk, label)] of every publishing, for selects."""
    return [(publishing.pk, publishing_label(publishing)) for publishing in publishings_for_select()]


RECENT_PUBLISHINGS = 8
NEWEST_PUBLISHINGS = 3


def recent_publishings() -> list[Publishing]:
    """Publishings most likely to get a new edition: the newest created ones (they are usually
    created right before adding their editions), then those of the most recently added editions."""
    recent = list(Publishing.objects.order_by("-id")[:NEWEST_PUBLISHINGS])
    seen = {publishing.pk for publishing in recent}
    for edition in Edition.objects.select_related("publishing").order_by("-id")[:200]:
        if len(recent) >= RECENT_PUBLISHINGS:
            break
        if edition.publishing_id not in seen:
            seen.add(edition.publishing_id)
            recent.append(edition.publishing)
    prefetch_related_objects(recent, "editorials")
    return recent


class EditionManageForm(MaterialFieldsMixin, forms.ModelForm):
    """Edition data for Gestion. The content (issue or collected issues) is
    handled separately in the view because they are not direct model fields."""

    # Set by the import box (comics/catalog_sources) to a catalogue's cover; downloaded on save.
    catalog_cover_url = forms.CharField(required=False, max_length=500, widget=forms.HiddenInput)

    class Meta:
        model = Edition
        fields = [
            "publishing",
            "number",
            "variant",
            "printing",
            "format",
            "rarity",
            "release_date",
            "cover_price",
            "ratio",
            "limited_to",
            "retailer_exclusive",
            "event_exclusive",
            "image",
            "cover_artists",
            "notes",
            "mycomicshop_id",
        ]
        labels = {
            "publishing": "Publishing",
            "number": _("Number"),
            "variant": _("Variant"),
            "printing": _("Printing"),
            "format": _("Format"),
            "rarity": _("Rarity"),
            "release_date": _("Release date"),
            "cover_price": _("Cover price"),
            "ratio": "Ratio",
            "limited_to": _("Limited to"),
            "retailer_exclusive": _("Store (exclusive)"),
            "event_exclusive": _("Event (exclusive)"),
            "image": _("Cover"),
            "cover_artists": _("Cover artists"),
            "notes": _("Notes"),
            "mycomicshop_id": "MyComicShop ID",
        }
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "cover_artists": forms.CheckboxSelectMultiple,
            "image": forms.FileInput(attrs={"accept": "image/*"}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _(
                    "An edition with that publishing, number, variant, printing, event and store already exists."
                ),
            },
            "mycomicshop_id": {"unique": _("Another edition already has this MyComicShop ID.")},
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        publishing = self.fields["publishing"]
        publishing.queryset = publishings_for_select()
        publishing.label_from_instance = publishing_label
        publishing.widget.attrs["data-combobox"] = ""
        # Suggest the events and stores already used, so they are typed the same way.
        self.fields["event_exclusive"].widget.attrs["list"] = "event-options"
        self.fields["retailer_exclusive"].widget.attrs["list"] = "retailer-options"
        if not self.instance.pk:
            # New editions: offer the recent publishings first (they repeat in the full list).
            publishing.widget.choices = [
                ("", "---------"),
                (_("Recent"), [(item.pk, publishing_label(item)) for item in recent_publishings()]),
                (_("All publishings"), [(item.pk, publishing_label(item)) for item in publishing.queryset]),
            ]
        self.fields["cover_artists"].queryset = self.fields["cover_artists"].queryset.order_by("name")

        self.style_fields()
        self.fields["notes"].widget.attrs["style"] = "height: 90px"

    def clean(self) -> dict[str, Any]:
        """A cover imported from a catalogue (its URL, set by the import box) is downloaded here, once
        the rest of the form is valid, unless a file was uploaded too: the uploaded file wins. It then
        goes through the same processing as an uploaded cover when the edition is saved. Only URLs
        on the catalogues' image hosts are ever requested (comics.catalog_sources.http). Its errors
        are the form's (the field is hidden)."""
        cleaned = super().clean()
        url = (cleaned.get("catalog_cover_url") or "").strip()
        if url and not is_allowed_cover_url(url):
            self.add_error(
                None, ValidationError(_("Covers can only be downloaded from the catalogues Comi imports from."))
            )
        elif url and not self.errors and not self.files.get(self.add_prefix("image")):
            try:
                cleaned["image"] = catalog_cover(url)
            except CoverRejected as exc:
                self.add_error(None, exc.message)
            except ValidationError as exc:
                self.add_error(None, exc)
        return cleaned


COVER_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def catalog_cover(url: str) -> SimpleUploadedFile:
    """The cover at `url` (an allowlisted catalogue host) as an upload, checked like an uploaded
    picture (size, format, pixels). Raises CoverRejected or ValidationError."""
    # Named .jpg only to pass the extension check; the real format is known once Pillow opened it.
    upload = CatalogCoverField(required=False).clean(SimpleUploadedFile("catalog_cover.jpg", download_cover(url)))
    upload.name = f"catalog_cover.{COVER_EXTENSIONS[upload.image.format]}"
    upload.seek(0)
    return upload


class IssueSectionForm(MaterialFieldsMixin, forms.ModelForm):
    """The issue part of the Gestión edition form: what is saved on the issue the edition ends up
    with (its own issue, or the one chosen in the content section). Issues have no Gestión page.

    It is bound to the issue whose values the page showed (posted back as SHOWN_FIELD), so its
    changed_data are the values the user changed (or the import filled in), and apply_to() writes
    only those: choosing another issue in the content section never copies the shown values onto it."""

    PREFIX = "issue_data"
    SHOWN_FIELD = f"{PREFIX}-shown"

    class Meta:
        model = Issue
        fields = ["synopsis", "creators", "metron_id", "comicvine_id", "gcd_id"]
        labels = {
            "synopsis": _("Synopsis"),
            "creators": _("Creators"),
            "metron_id": "Metron ID",
            "comicvine_id": "Comic Vine ID",
            "gcd_id": "GCD ID",
        }
        widgets = {
            "synopsis": forms.Textarea(attrs={"rows": 4}),
            "creators": forms.CheckboxSelectMultiple,
        }
        error_messages = {
            "metron_id": {"unique": _("Another issue already has this Metron ID.")},
            "comicvine_id": {"unique": _("Another issue already has this Comic Vine ID.")},
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", self.PREFIX)
        super().__init__(*args, **kwargs)
        self.fields["creators"].queryset = self.fields["creators"].queryset.order_by("name")
        self.style_fields()
        self.fields["synopsis"].widget.attrs["style"] = "height: 120px"

    def validate_unique(self) -> None:
        """Not against the shown issue: the values go to the issue the edition ends up with (e.g. a new
        cover of an issue that already has these ids), so apply_to() checks them against that one."""

    @classmethod
    def shown_issue(cls, data: QueryDict | None, fallback: Issue | None) -> Issue:
        """The issue whose values the submitted form showed (SHOWN_FIELD), or, when nothing was
        submitted, `fallback` (what the page shows when it is opened); a blank issue otherwise."""
        if data is not None:
            raw = str(data.get(cls.SHOWN_FIELD, ""))
            shown = Issue.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
            return shown or Issue()
        return fallback or Issue()

    def apply_to(self, issue: Issue) -> None:
        """Write the changed values on `issue` (call after is_valid(), inside a transaction). The
        creators are applied as a change too: the ones added and removed against the shown issue, so
        another issue keeps its own creators. Raises ValidationError when one of its unique ids
        belongs to another issue."""
        changed = [name for name in self.changed_data if name in self.fields]
        values = [name for name in changed if name != "creators"]
        if values:
            for name in values:
                setattr(issue, name, self.cleaned_data[name])
            try:
                issue.validate_unique(exclude=[f.name for f in Issue._meta.fields if f.name not in values])
            except ValidationError as exc:
                raise ValidationError(
                    [
                        self._meta.error_messages.get(name, {}).get("unique", message)
                        for name, messages in exc.message_dict.items()
                        for message in messages
                    ]
                ) from exc
            try:
                with transaction.atomic():
                    issue.save(update_fields=values)
            except IntegrityError as exc:  # another request took the id in the meantime
                raise ValidationError(_("Another issue already has one of these ids.")) from exc
        if "creators" in changed:
            initial = self.get_initial_for_field(self.fields["creators"], "creators") or []
            shown = {getattr(item, "pk", item) for item in initial}
            wanted = {artist.pk for artist in self.cleaned_data["creators"]}
            issue.creators.add(*(wanted - shown))
            issue.creators.remove(*(shown - wanted))


CATALOG_ID_PATTERN = re.compile(r"^(?:4000-)?(\d{1,10})$")
SERIES_ID_PATTERN = re.compile(r"^\d{1,10}$")
MAX_CATALOG_ID = 2_147_483_647  # the Issue id fields are PositiveIntegerField


def clean_series_id(value: str) -> int:
    """A catalogue's series id (MyComicShop's TID), as typed in the import box."""
    value = value.strip()
    number = int(value) if SERIES_ID_PATTERN.match(value) else 0
    if not 0 < number <= MAX_CATALOG_ID:
        raise ValidationError(_("Enter the series' number in the catalogue, e.g. 58451662."), code="invalid_series")
    return number


class CatalogLookupForm(forms.Form):
    """What the import box asks a catalogue for: the source and the issue's id there (Comic Vine's
    "4000-" prefix, as its URLs show it, is accepted too), plus the series id for the catalogues
    whose issues are only reachable through their series (CatalogSource.requires_series:
    MyComicShop's IVID needs its TID); the others ignore it."""

    source = forms.ChoiceField(choices=[(source.key, source.name) for source in catalog_service.SOURCES.values()])
    id = forms.CharField(max_length=20)
    # No max_length: only the sources that require a series validate it (clean), so whatever else
    # is sent with Metron or Comic Vine never makes their lookup fail.
    series = forms.CharField(required=False)

    def clean_id(self) -> int:
        match = CATALOG_ID_PATTERN.match(self.cleaned_data["id"].strip())
        number = int(match.group(1)) if match else 0
        if not 0 < number <= MAX_CATALOG_ID:
            raise ValidationError(_("Enter the issue's number in the catalogue, e.g. 15439."), code="invalid_id")
        return number

    def clean(self) -> dict[str, Any]:
        """The series id as an int for the sources that require it (an error when it is missing or
        not a number); None for the others, whatever was sent."""
        cleaned = super().clean()
        source = catalog_service.SOURCES.get(cleaned.get("source") or "")
        value = (cleaned.get("series") or "").strip()
        cleaned["series"] = None
        if source is None or not source.requires_series:
            return cleaned
        if not value:
            self.add_error("series", series_required_message(source.name))
            return cleaned
        try:
            cleaned["series"] = clean_series_id(value)
        except ValidationError as exc:
            self.add_error("series", exc)
        return cleaned


class CatalogSeriesForm(forms.Form):
    """The series a catalogue's series view (MyComicShop's variants modal) asks for."""

    id = forms.CharField(max_length=20)

    def clean_id(self) -> int:
        return clean_series_id(self.cleaned_data["id"])


class MyComicShopLinkForm(forms.Form):
    """The edition and the MyComicShop cover (IVID) the variants modal links it to."""

    edition = forms.IntegerField(min_value=1, error_messages={"required": _("Choose the edition to link.")})
    ivid = forms.IntegerField(
        min_value=1,
        max_value=MAX_CATALOG_ID,  # Edition.mycomicshop_id is a PositiveIntegerField
        error_messages={"required": _("The MyComicShop cover is missing.")},
    )


class PublishingManageForm(forms.ModelForm):
    """Publishing data for Gestión. The title (the group used by the letter filter) is typed as
    text: an existing one is reused and a new one is created when needed."""

    title_name = forms.CharField(
        label=_("Title (group)"),
        max_length=100,
        required=False,
        help_text=_("Groups publishings under a letter. Empty: the publishing title is used."),
    )

    class Meta:
        model = Publishing
        fields = [
            "publishing_title",
            "serie",
            "language",
            "date",
            "year",
            "editorials",
            "metron_series_id",
            "comicvine_volume_id",
            "gcd_series_id",
        ]
        labels = {
            "publishing_title": _("Publishing title"),
            "serie": _("Series"),
            "language": _("Language"),
            "date": _("Start date"),
            "year": _("Year"),
            "editorials": _("Editorials"),
            "metron_series_id": _("Metron series ID"),
            "comicvine_volume_id": _("Comic Vine volume ID"),
            "gcd_series_id": _("GCD series ID"),
        }
        help_texts = {"year": _("Empty: taken from the start date.")}
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "editorials": forms.SelectMultiple(attrs={"data-multiselect": ""}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("A publishing with that title, year, series and language already exists."),
            },
            "metron_series_id": {"unique": _("Another publishing already has this Metron series ID.")},
            "comicvine_volume_id": {"unique": _("Another publishing already has this Comic Vine volume ID.")},
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if "title_name" in self.fields:
            if self.instance.pk and self.instance.title_id:
                self.fields["title_name"].initial = self.instance.title.name
            self.fields["title_name"].widget.attrs["list"] = "title-options"
        self.fields["editorials"].queryset = Editorial.objects.order_by("name")
        self.fields["editorials"].widget.attrs["data-placeholder"] = _("Search an editorial…")
        for _name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs.update({"class": "form-control", "placeholder": field.label})

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        # Derive the year before the unique constraint is validated.
        if cleaned.get("publishing_title"):
            cleaned["publishing_title"] = cleaned["publishing_title"].strip()
        if cleaned.get("year") is None and cleaned.get("date"):
            cleaned["year"] = cleaned["date"].year
        return cleaned

    def save(self, commit: bool = True) -> Publishing:
        publishing = super().save(commit=False)
        publishing.year = self.cleaned_data.get("year")
        publishing.title = self.chosen_title(publishing)
        if commit:
            publishing.save()
            self.save_m2m()
        return publishing

    def chosen_title(self, publishing: Publishing) -> Title:
        """The typed title, reused when it exists (ignoring case) and created otherwise; the
        publishing title when nothing was typed."""
        name = (self.cleaned_data.get("title_name") or "").strip() or publishing.publishing_title.strip()
        return Title.objects.filter(name__iexact=name).first() or Title.objects.create(name=name)


class PublishingDialogForm(RequiredHintsMixin, PublishingManageForm):
    """The "Create publishing" dialog of the Gestión edition form (posted to an API, see
    comics.views.manage_publishings.publishing_create_api). The title is chosen from the existing
    ones (the "Create title" dialog adds new ones), never derived from the publishing title.
    Neither the language nor the start date starts with the model's default (English, today): the
    dialog is often prefilled from a catalogue that may not know them, and a default the user
    overlooked would be saved as if it were known. Both are required. Prefixed, as it lives on the
    edition form's page."""

    PREFIX = "new_publishing"
    required_hints = {
        "title": _("Required: choose a title or create one with +."),
        "editorials": _("Required: choose an editorial or create one with +."),
        "language": _("Required: choose the language."),
        "date": _("Required: enter the start date."),
    }

    title_name = None  # replaced by the `title` select
    title = forms.ModelChoiceField(queryset=Title.objects.order_by("name"), label=_("Title (group)"))
    language = forms.ChoiceField(
        choices=[("", _("Choose a language")), *Publishing.LangAbbr.choices],
        label=_("Language"),
        widget=PlaceholderSelect,
    )

    class Meta(PublishingManageForm.Meta):
        fields = ["title", *PublishingManageForm.Meta.fields]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", self.PREFIX)
        kwargs["initial"] = {"language": "", "date": None, **(kwargs.get("initial") or {})}
        super().__init__(*args, **kwargs)
        self.fields["title"].widget.attrs["data-combobox"] = ""
        self.mark_required()

    def chosen_title(self, publishing: Publishing) -> Title:
        return self.cleaned_data["title"]


class TitleDialogForm(UniqueNameMixin, RequiredHintsMixin, MaterialFieldsMixin, forms.ModelForm):
    """The "Create title" dialog (a Title groups publishings under a letter)."""

    PREFIX = "new_title"
    duplicate_message = _("A title with that name already exists.")

    class Meta:
        model = Title
        fields = ["name"]
        labels = {"name": _("Name")}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", self.PREFIX)
        super().__init__(*args, **kwargs)
        self.style_fields()
        self.mark_required()


class EditorialDialogForm(UniqueNameMixin, RequiredHintsMixin, MaterialFieldsMixin, forms.ModelForm):
    """The "Create editorial" dialog."""

    PREFIX = "new_editorial"
    duplicate_message = _("An editorial with that name already exists.")

    class Meta:
        model = Editorial
        fields = ["name", "country"]
        labels = {"name": _("Name"), "country": _("Country")}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", self.PREFIX)
        super().__init__(*args, **kwargs)
        self.style_fields()
        self.mark_required()


class EditorialAliasForm(forms.Form):
    """A catalogue's publisher name remembered as an alias of an existing editorial (offered by the
    import box and the "Create publishing" dialog when the publisher matched no editorial), so the
    next import matches it. The rules are the model's (EditorialAlias.clean)."""

    editorial = forms.ModelChoiceField(queryset=Editorial.objects.all(), label=_("Editorial"))
    name = forms.CharField(label=_("Alias"), max_length=100)


class RepeatableTextInput(forms.TextInput):
    """A text input whose name may be posted several times (e.g. by a script): every value is read."""

    def value_from_datadict(self, data: Any, files: Any, name: str) -> list[str]:
        if hasattr(data, "getlist"):
            return data.getlist(name)
        value = data.get(name)
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]

    def format_value(self, value: Any) -> str | None:
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        return super().format_value(value)


class NameListField(forms.Field):
    """One or more names (the same key posted several times), each stripped and with its inner
    spaces collapsed. A blank or too long name, or too many of them, is an error."""

    widget = RepeatableTextInput
    default_error_messages = {
        "blank": _("Enter a name."),
        "too_long": _("“%(name)s” is longer than %(limit)d characters."),
        "too_many": _("Send at most %(limit)d names at a time."),
    }

    def __init__(self, *, max_length: int, max_names: int, **kwargs: Any) -> None:
        self.max_length = max_length
        self.max_names = max_names
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        values = value if isinstance(value, (list, tuple)) else [value]
        return [" ".join(str(item).split()) for item in values]

    def validate(self, value: list[str]) -> None:
        super().validate(value)
        if len(value) > self.max_names:
            raise ValidationError(self.error_messages["too_many"], code="too_many", params={"limit": self.max_names})
        for name in value:
            if not name:
                raise ValidationError(self.error_messages["blank"], code="blank")
            if len(name) > self.max_length:
                raise ValidationError(
                    self.error_messages["too_long"], code="too_long", params={"name": name, "limit": self.max_length}
                )


class ArtistCreateForm(RequiredHintsMixin, MaterialFieldsMixin, forms.Form):
    """The artists to create: one from the "Create artist" dialog of the edition form, or several at
    once from the import box (the names a catalogue credits and Comi does not have). A name an artist
    already has is not an error: comics.models.ArtistQuerySet.ensure_named answers that artist."""

    PREFIX = "new_artist"
    MAX_NAMES = 100

    name = NameListField(label=_("Name"), max_length=Artist._meta.get_field("name").max_length, max_names=MAX_NAMES)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", self.PREFIX)
        super().__init__(*args, **kwargs)
        self.style_fields()
        self.mark_required()


class RevertForm(forms.Form):
    """Going back to a version from the Gestión history: an optional reason, kept with the new version."""

    reason = forms.CharField(label=_("Reason (optional)"), max_length=255, required=False)


class CatalogCoverField(LimitedImageField):
    """A cover downloaded from a catalogue (see catalog_cover)."""

    MAX_BYTES = MAX_COVER_BYTES
