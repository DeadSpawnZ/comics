"""The achievement catalogue: the rows every rule needs, with their initial texts, icons and levels.

Data migrations seed these rows (each keeps a frozen copy of what it seeds; a test checks they
match this list). `manage.py ensure_achievements` creates the achievements that are missing (with
their levels), e.g. after `flush` and restoring a dump taken before they existed. Existing
achievements are never touched, so nothing Gestión changed is overwritten."""

from __future__ import annotations

from django.db import transaction

from comics.models import Achievement, AchievementTier

ACHIEVEMENTS = [
    {
        "code": "origin",
        "category": "collection",
        "icon": "auto_stories",
        "display_order": 10,
        "name_en": "Origin",
        "name_es": "Origen",
        "description_en": "Register your first comic. Every hero has an origin story.",
        "description_es": "Registra tu primer cómic. Todo héroe tiene una historia de origen.",
        "tiers": [],
    },
    {
        "code": "great_power",
        "category": "collection",
        "icon": "collections_bookmark",
        "display_order": 20,
        "name_en": "With great power…",
        "name_es": "Un gran poder…",
        "description_en": "Have comics in your collection. …comes great responsibility.",
        "description_es": "Ten cómics en tu colección. …conlleva una gran responsabilidad.",
        "tiers": [100, 500, 1000],
    },
    {
        "code": "veteran",
        "category": "seniority",
        "icon": "military_tech",
        "display_order": 10,
        "name_en": "Veteran",
        "name_es": "Veterano",
        "description_en": "Be part of Comi for years, like a hero from the Golden Age.",
        "description_es": "Forma parte de Comi durante años, como un héroe de la Edad de Oro.",
        "tiers": [1, 3, 5],
    },
    {
        "code": "secret_identity",
        "category": "community",
        "icon": "visibility_off",
        "display_order": 10,
        "is_secret": True,
        "name_en": "Secret identity",
        "name_es": "Identidad secreta",
        "description_en": "Make your profile private. Not even Lois Lane knows who you are.",
        "description_es": "Haz tu perfil privado. Ni Lois Lane sabe quién eres.",
        "hint_en": "Some heroes prefer to stay in the shadows.",
        "hint_es": "Algunos héroes prefieren quedarse en las sombras.",
        "tiers": [],
    },
]


def ensure_catalogue() -> list[str]:
    """Create the missing achievements and levels of the catalogue. Returns the codes created."""
    created_codes = []
    with transaction.atomic():
        for data in ACHIEVEMENTS:
            values = dict(data)
            code, thresholds = values.pop("code"), values.pop("tiers")
            achievement, created = Achievement.objects.get_or_create(code=code, defaults=values)
            if created:
                created_codes.append(code)
                for level, threshold in enumerate(thresholds, start=1):
                    AchievementTier.objects.create(achievement=achievement, level=level, threshold=threshold)
    return created_codes
