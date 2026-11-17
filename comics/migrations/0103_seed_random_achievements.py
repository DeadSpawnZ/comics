"""Seed the "Random" category and its achievements (rules in comics/achievements/rules.py; the
current catalogue is comics/achievements/catalogue.py, this is a frozen copy of what this
migration adds, checked by a test).

Like the other seeds it only creates what is missing, by code (get_or_create), so a database
where they already exist keeps them and whatever Gestión changed. Run
`manage.py award_achievements` after migrating: novato, aplicado, fotogenico and verano are
retroactive; madrugador and ave_nocturna only see each user's last sign-in.

Reverse deletes these six achievements with their levels and the awards users got of them (the
awards are lost), and the category if no other achievement is in it."""

import datetime

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

CATEGORIES = [
    {"code": "random", "name_en": "Random", "name_es": "Random", "display_order": 60},
]

ACHIEVEMENTS = [
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
]


def seed(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    AchievementCategory = apps.get_model("comics", "AchievementCategory")
    AchievementTier = apps.get_model("comics", "AchievementTier")
    categories = {}
    for data in CATEGORIES:
        values = dict(data)
        code = values.pop("code")
        categories[code], _created = AchievementCategory.objects.using(db).get_or_create(code=code, defaults=values)
    for data in ACHIEVEMENTS:
        values = dict(data)
        code, thresholds = values.pop("code"), values.pop("tiers")
        values.update(values.pop("window", {}))
        values["category"] = categories[values["category"]]
        values.setdefault("rule", code)
        achievement, _created = Achievement.objects.using(db).get_or_create(code=code, defaults=values)
        for level, threshold in enumerate(thresholds, start=1):
            AchievementTier.objects.using(db).get_or_create(
                achievement=achievement, level=level, defaults={"threshold": threshold}
            )


def unseed(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    AchievementCategory = apps.get_model("comics", "AchievementCategory")
    UserAchievement = apps.get_model("comics", "UserAchievement")
    codes = [item["code"] for item in ACHIEVEMENTS]
    UserAchievement.objects.using(db).filter(achievement__code__in=codes).delete()
    Achievement.objects.using(db).filter(code__in=codes).delete()  # their levels go with them (CASCADE)
    for data in CATEGORIES:
        AchievementCategory.objects.using(db).filter(code=data["code"], achievements__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0102_achievement_category_foreign_key"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
