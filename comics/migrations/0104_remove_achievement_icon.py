"""Achievements lose their Material Symbols icon name: medals always show a picture now (the image
uploaded in Gestión, the artwork bundled for the code, else the generic medal), so the name was
no longer shown anywhere.

Reverse brings the column back with its old default and puts back the icons the built-in
achievements were seeded with (a frozen copy); achievements created in Gestión get the default.
An icon name changed in Gestión before this migration is not restored."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

SEEDED_ICONS = {
    "origin": "auto_stories",
    "great_power": "collections_bookmark",
    "veteran": "military_tech",
    "secret_identity": "visibility_off",
    "infinity_gauntlet": "diamond",
    "batcave_vault": "draw",
    "multiverse": "auto_awesome_motion",
    "speed_force": "bolt",
    "lazarus_pit": "replay",
    "assemble": "grid_view",
    "chronicler": "history_edu",
    "halloween": "skull",
}


def restore_icons(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    db = schema_editor.connection.alias
    Achievement = apps.get_model("comics", "Achievement")
    for code, icon in SEEDED_ICONS.items():
        Achievement.objects.using(db).filter(code=code).update(icon=icon)


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0103_seed_random_achievements"),
    ]

    operations = [
        # Reverse runs bottom-up: the column comes back first, then its seeded values.
        migrations.RunPython(migrations.RunPython.noop, restore_icons),
        migrations.RemoveField(
            model_name="achievement",
            name="icon",
        ),
    ]
