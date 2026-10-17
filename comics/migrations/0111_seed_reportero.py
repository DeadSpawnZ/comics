"""Seed the "Reportero" achievement (rule in comics/achievements/rules.py; the current catalogue is
comics/achievements/catalogue.py, this is a frozen copy of what this migration adds, checked by a
test): awarded for a first approved change request.

Like the other seeds it only creates what is missing, by code (get_or_create), so a database where
it already exists keeps it and whatever Gestión changed; the "random" category is created if a
database lacks it. Nobody has an approved request yet when this runs, so there is nothing to award
retroactively (`manage.py award_achievements` would award it anyway).

Reverse deletes the achievement and the awards users got of it (the awards are lost); the category
stays (it holds other achievements)."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

CATEGORIES = [
    {"code": "random", "name_en": "Random", "name_es": "Random", "display_order": 60},
]

ACHIEVEMENTS = [
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


def seed(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    AchievementCategory = apps.get_model("comics", "AchievementCategory")
    categories = {}
    for data in CATEGORIES:
        values = dict(data)
        code = values.pop("code")
        categories[code], _created = AchievementCategory.objects.using(db).get_or_create(code=code, defaults=values)
    for data in ACHIEVEMENTS:
        values = dict(data)
        code = values.pop("code")
        values.pop("tiers")  # none: a done-or-not achievement
        values["category"] = categories[values["category"]]
        values.setdefault("rule", code)
        Achievement.objects.using(db).get_or_create(code=code, defaults=values)


def unseed(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    UserAchievement = apps.get_model("comics", "UserAchievement")
    Notification = apps.get_model("comics", "Notification")
    codes = [item["code"] for item in ACHIEVEMENTS]
    # Their notifications would cascade anyway; the awards are protected, so they go first.
    Notification.objects.using(db).filter(achievement__code__in=codes).delete()
    UserAchievement.objects.using(db).filter(achievement__code__in=codes).delete()
    Achievement.objects.using(db).filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0110_change_request_permissions"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
