import django.db.models.deletion
from django.db import migrations, models


def ensure_old_fields_empty(apps, schema_editor):
    """Salvaguarda: los campos que se eliminan deben estar vacios para no perder datos."""
    Comic = apps.get_model("comics", "Comic")
    compilations = Comic.objects.filter(is_compilation=True).count()
    compiled = Comic.compiled_issues.through.objects.count()
    if compilations or compiled:
        raise RuntimeError(
            f"Hay {compilations} comics con is_compilation y {compiled} filas en compiled_issues. "
            "Migralos a CollectedIssue antes de eliminar esos campos."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0084_populate_issues"),
    ]

    operations = [
        migrations.RunPython(ensure_old_fields_empty, migrations.RunPython.noop),
        migrations.RemoveField(model_name="comic", name="compiled_issues"),
        migrations.RemoveField(model_name="comic", name="is_compilation"),
        migrations.RenameField(model_name="comic", old_name="artists", new_name="cover_artists"),
        migrations.AlterField(
            model_name="comic",
            name="cover_artists",
            field=models.ManyToManyField(blank=True, related_name="covers", to="comics.artist"),
        ),
        migrations.AlterField(
            model_name="comic",
            name="issue",
            field=models.ForeignKey(
                blank=True,
                help_text="Se asigna solo segun publishing y numero. Cambialo si es una edicion extranjera o de aniversario de un issue de otra serie. Vacio en compilaciones.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="editions",
                to="comics.issue",
            ),
        ),
    ]
