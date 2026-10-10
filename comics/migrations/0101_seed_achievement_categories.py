"""Step 2 of 3 (data): seed the categories and point every achievement at its own.

- One category row per former category value, with its former translated label as the bilingual
  name (a frozen copy of comics/achievements/catalogue.py CATEGORIES; a test checks they match).
  A value outside that list (none is expected) gets a row named after the value.
- `rule` = `code` for every achievement (until now the code was the rule's key).
- `halloween` moves to the generic rule `login_in_window` with a yearly 31/10 - 31/10 window, which
  awards exactly what its own rule did.

Reverse puts the former values back (the category code into the text column, halloween's own
rule); 0100's reverse then drops the new columns and the category table. What only exists in the
new schema is lost on the way back:
- the category rows, with every Gestión edit of them (names, descriptions, order, enabled flag)
  and their medallion images: the uploaded files stay in media/achievement_categories/, orphaned;
- every time window (calendar and time of day), on all achievements;
- the `rule` of achievements created in Gestión: they stay as rows whose code has no rule in code
  ("No rule in code" in Gestión, never awarded). Their category code is kept in the text column."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

CATEGORIES = [
    {"code": "collection", "name_en": "Collection", "name_es": "Colección", "display_order": 10},
    {"code": "treasures", "name_en": "Treasures", "name_es": "Tesoros", "display_order": 20},
    {"code": "sagas", "name_en": "Sagas", "name_es": "Sagas", "display_order": 30},
    {"code": "community", "name_en": "Community", "name_es": "Comunidad", "display_order": 40},
    {"code": "seniority", "name_en": "Seniority", "name_es": "Antigüedad", "display_order": 50},
]

HALLOWEEN_RULE = "login_in_window"
HALLOWEEN_WINDOW = {
    "calendar_window": "yearly",
    "window_start_day": 31,
    "window_start_month": 10,
    "window_end_day": 31,
    "window_end_month": 10,
}


def forward(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    AchievementCategory = apps.get_model("comics", "AchievementCategory")
    categories = {}
    for data in CATEGORIES:
        values = dict(data)
        code = values.pop("code")
        categories[code], _created = AchievementCategory.objects.using(db).get_or_create(code=code, defaults=values)
    for achievement in Achievement.objects.using(db).all():
        value = achievement.category or "collection"
        if value not in categories:
            name = value.replace("_", " ").capitalize()
            categories[value], _created = AchievementCategory.objects.using(db).get_or_create(
                code=value, defaults={"name_en": name, "name_es": name, "display_order": 100}
            )
        achievement.category_new = categories[value]
        achievement.rule = achievement.rule or achievement.code
        if achievement.code == "halloween" and achievement.rule == "halloween":
            achievement.rule = HALLOWEEN_RULE
            for field, window_value in HALLOWEEN_WINDOW.items():
                setattr(achievement, field, window_value)
        achievement.save()


def backward(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    for achievement in Achievement.objects.using(db).select_related("category_new"):
        if achievement.category_new is not None:
            achievement.category = achievement.category_new.code
        if achievement.code == "halloween" and achievement.rule == HALLOWEEN_RULE:
            achievement.rule = "halloween"
        achievement.save()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0100_achievement_categories_rules_windows"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
