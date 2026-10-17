"""The achievement catalogue: the built-in categories and achievements, with their initial texts,
icons, levels and windows. Gestión can add more categories, and achievements with generic rules.

Data migrations seed these rows (each keeps a frozen copy of what it seeds; tests check they
match this list). `manage.py ensure_achievements` creates the categories and achievements that are
missing (with their levels), e.g. after `flush` and restoring a dump taken before they existed.
Existing rows are never touched, so nothing Gestión changed is overwritten.

An achievement's rule is its code unless it says otherwise ("rule"); "window" holds the time
window fields of achievements with a windowed rule.

Seeding a new built-in achievement or category: its data migration must get_or_create by code
(never create blindly nor overwrite), so a database where it already exists keeps its row and
whatever Gestión changed. Built-in codes never start with "g_": that prefix is reserved for codes
generated in Gestión (models.GENERATED_CODE_PREFIX), so the two can never collide."""

from __future__ import annotations

import datetime

from django.db import transaction

from comics.models import Achievement, AchievementCategory, AchievementTier

CATEGORIES = [
    {"code": "collection", "name_en": "Collection", "name_es": "Colección", "display_order": 10},
    {"code": "treasures", "name_en": "Treasures", "name_es": "Tesoros", "display_order": 20},
    {"code": "sagas", "name_en": "Sagas", "name_es": "Sagas", "display_order": 30},
    {"code": "community", "name_en": "Community", "name_es": "Comunidad", "display_order": 40},
    {"code": "seniority", "name_en": "Seniority", "name_es": "Antigüedad", "display_order": 50},
    {"code": "random", "name_en": "Random", "name_es": "Random", "display_order": 60},
]

ACHIEVEMENTS = [
    {
        "code": "origin",
        "category": "collection",
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
        "display_order": 20,
        "is_secret": True,
        "name_en": "Halloween night",
        "name_es": "Noche de brujas",
        "description_en": "Sign in on October 31. Not even the Scarecrow can scare you.",
        "description_es": "Inicia sesión un 31 de octubre. Ni el Espantapájaros te asusta.",
        "hint_en": "Something happens one night a year…",
        "hint_es": "Algo pasa una noche al año…",
        "tiers": [],
        "rule": "login_in_window",
        "window": {
            "calendar_window": "yearly",
            "window_start_day": 31,
            "window_start_month": 10,
            "window_end_day": 31,
            "window_end_month": 10,
        },
    },
    {
        "code": "novato",
        "category": "random",
        "display_order": 10,
        "name_en": "Rookie",
        "name_es": "Novato",
        "description_en": "Register in Comi. Welcome to the team!",
        "description_es": "Regístrate en Comi. ¡Bienvenido al equipo!",
        "tiers": [],
    },
    {
        "code": "aplicado",
        "category": "random",
        "display_order": 20,
        "name_en": "Diligent",
        "name_es": "Aplicado",
        "description_en": "Fill in your whole profile: first name, last name, email, phone and profile picture.",
        "description_es": "Completa todo tu perfil: nombre, apellidos, correo, teléfono y foto de perfil.",
        "tiers": [],
    },
    {
        "code": "fotogenico",
        "category": "random",
        "display_order": 30,
        "name_en": "Photogenic",
        "name_es": "Fotogénico",
        "description_en": "Upload a profile picture.",
        "description_es": "Sube una foto de perfil.",
        "tiers": [],
    },
    {
        "code": "madrugador",
        "category": "random",
        "display_order": 40,
        "name_en": "Early bird",
        "name_es": "Madrugador",
        "description_en": "Sign in between 7:00 and 8:00 in the morning.",
        "description_es": "Inicia sesión entre las 7:00 y las 8:00 de la mañana.",
        "tiers": [],
        "rule": "login_in_window",
        "window": {"window_start_time": datetime.time(7, 0), "window_end_time": datetime.time(8, 0)},
    },
    {
        "code": "ave_nocturna",
        "category": "random",
        "display_order": 50,
        "name_en": "Night owl",
        "name_es": "Ave nocturna",
        "description_en": "Sign in between 1:00 and 3:00 at night.",
        "description_es": "Inicia sesión entre la 1:00 y las 3:00 de la madrugada.",
        "tiers": [],
        "rule": "login_in_window",
        "window": {"window_start_time": datetime.time(1, 0), "window_end_time": datetime.time(3, 0)},
    },
    {
        "code": "verano",
        "category": "random",
        "display_order": 60,
        "name_en": "Summer",
        "name_es": "Verano",
        "description_en": "Register a purchase in July and another in August of the same year.",
        "description_es": "Registra una compra en julio y otra en agosto del mismo año.",
        "tiers": [],
        "rule": "purchases_each_month_in_window",
        "window": {
            "calendar_window": "yearly",
            "window_start_day": 1,
            "window_start_month": 7,
            "window_end_day": 31,
            "window_end_month": 8,
        },
    },
    {
        "code": "reportero",
        "category": "random",
        "display_order": 70,
        "name_en": "Reporter",
        "name_es": "Reportero",
        "description_en": "Get a change request approved. J. Jonah Jameson would hire you at the Daily Bugle!",
        "description_es": "Consigue que aprueben una de tus solicitudes de cambio. ¡J. Jonah Jameson te contrataría en el Daily Bugle!",
        "tiers": [],
    },
]


# Codes of the built-in achievements: Gestión never deletes them nor gives their codes to new ones.
BUILTIN_CODES = frozenset(item["code"] for item in ACHIEVEMENTS)


def ensure_catalogue() -> list[str]:
    """Create the missing categories, achievements and levels of the catalogue. Returns the codes
    of the achievements created."""
    created_codes = []
    with transaction.atomic():
        categories = {}
        for data in CATEGORIES:
            values = dict(data)
            code = values.pop("code")
            categories[code], _created = AchievementCategory.objects.get_or_create(code=code, defaults=values)
        for data in ACHIEVEMENTS:
            values = dict(data)
            code, thresholds = values.pop("code"), values.pop("tiers")
            values.update(values.pop("window", {}))
            values["category"] = categories[values["category"]]
            values.setdefault("rule", code)
            achievement, created = Achievement.objects.get_or_create(code=code, defaults=values)
            if created:
                created_codes.append(code)
                for level, threshold in enumerate(thresholds, start=1):
                    AchievementTier.objects.create(achievement=achievement, level=level, threshold=threshold)
    return created_codes
