"""Seed the rest of the achievement catalogue (rules in comics/achievements/rules.py; the current
catalogue is comics/achievements/catalogue.py, this is a frozen copy of what this migration adds).

Same behaviour as 0097: only creates what is missing, never overwrites what Gestión changed, and
reverse does nothing. Run `manage.py award_achievements` after migrating."""

from django.db import migrations

ACHIEVEMENTS = [
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


def seed_achievements(apps, schema_editor):
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    AchievementTier = apps.get_model("comics", "AchievementTier")
    for data in ACHIEVEMENTS:
        values = dict(data)
        code, thresholds = values.pop("code"), values.pop("tiers")
        achievement, _created = Achievement.objects.using(db).get_or_create(code=code, defaults=values)
        for level, threshold in enumerate(thresholds, start=1):
            AchievementTier.objects.using(db).get_or_create(
                achievement=achievement, level=level, defaults={"threshold": threshold}
            )


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0098_edition_rarity_achievement_categories"),
    ]

    operations = [
        migrations.RunPython(seed_achievements, migrations.RunPython.noop),
    ]
