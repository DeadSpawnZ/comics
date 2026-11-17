"""Seed the first achievements (their earning rules live in comics/achievements/rules.py; the
current catalogue is comics/achievements/catalogue.py, this is a frozen copy).

Only creates what is missing (by code, and tiers by level), so running it again never overwrites
what Gestión changed. Reverse does nothing: unapplying 0096 drops the tables, and deleting rows
here would throw away achievements users earned. Awarding them for existing activity is done by
`manage.py award_achievements` after migrating (migrations do not run the rule code)."""

from django.db import migrations

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
        ("comics", "0096_achievements"),
    ]

    operations = [
        migrations.RunPython(seed_achievements, migrations.RunPython.noop),
    ]
