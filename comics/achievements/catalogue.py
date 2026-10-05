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
    {
        "code": "infinity_gauntlet",
        "category": "treasures",
        "icon": "diamond",
        "display_order": 10,
        "name_en": "Infinity Gauntlet",
        "name_es": "Guantelete del Infinito",
        "description_en": "Own 6 very rare or legendary editions, one for each Infinity Stone.",
        "description_es": "Ten 6 ediciones muy raras o legendarias, una por cada Gema del Infinito.",
        "tiers": [6],
    },
    {
        "code": "batcave_vault",
        "category": "treasures",
        "icon": "draw",
        "display_order": 20,
        "name_en": "Batman's vault",
        "name_es": "Bóveda de Batman",
        "description_en": "Keep signed pieces in your collection, safe like the trophies in the Batcave.",
        "description_es": "Guarda piezas firmadas en tu colección, a salvo como los trofeos de la Batcueva.",
        "tiers": [1, 5, 15],
    },
    {
        "code": "multiverse",
        "category": "collection",
        "icon": "auto_awesome_motion",
        "display_order": 30,
        "name_en": "Multiverse",
        "name_es": "Multiverso",
        "description_en": "Own 5 different editions (covers, variants or printings) of the same issue.",
        "description_es": "Ten 5 ediciones distintas (portadas, variantes o impresiones) del mismo número.",
        "tiers": [5],
    },
    {
        "code": "speed_force",
        "category": "collection",
        "icon": "bolt",
        "display_order": 40,
        "name_en": "Speed Force",
        "name_es": "Speed Force",
        "description_en": "Buy 10 comics on the same day. Faster than the Flash!",
        "description_es": "Compra 10 cómics el mismo día. ¡Más rápido que Flash!",
        "tiers": [10, 25, 50],
    },
    {
        "code": "lazarus_pit",
        "category": "collection",
        "icon": "replay",
        "display_order": 50,
        "is_secret": True,
        "name_en": "Lazarus Pit",
        "name_es": "Pozo de Lázaro",
        "description_en": "Buy again an issue you had sold. Whatever goes into the pit comes back…",
        "description_es": "Vuelve a comprar un número que habías vendido. Lo que entra al pozo, regresa…",
        "hint_en": "Some comics come back from the dead.",
        "hint_es": "Algunos cómics vuelven de entre los muertos.",
        "tiers": [],
    },
    {
        "code": "assemble",
        "category": "sagas",
        "icon": "grid_view",
        "display_order": 10,
        "name_en": "Avengers, assemble!",
        "name_es": "¡Vengadores, reuníos!",
        "description_en": "Complete a connecting: own every cover of it.",
        "description_es": "Completa un connecting: ten todas sus portadas.",
        "tiers": [1, 3, 5],
    },
    {
        "code": "chronicler",
        "category": "sagas",
        "icon": "history_edu",
        "display_order": 20,
        "name_en": "Chronicler",
        "name_es": "Lector de crónicas",
        "description_en": "Complete a reading arc: own every issue of it, in any edition or compilation.",
        "description_es": "Completa un arco de lectura: ten todos sus números, en cualquier edición o compilación.",
        "tiers": [1, 3, 5],
    },
    {
        "code": "halloween",
        "category": "community",
        "icon": "skull",
        "display_order": 20,
        "is_secret": True,
        "name_en": "Halloween night",
        "name_es": "Noche de brujas",
        "description_en": "Sign in on October 31. Not even the Scarecrow can scare you.",
        "description_es": "Inicia sesión un 31 de octubre. Ni el Espantapájaros te asusta.",
        "hint_en": "Something happens one night a year…",
        "hint_es": "Algo pasa una noche al año…",
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
