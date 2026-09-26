from django.db import migrations, models

# Solo renombres: tablas y columnas conservan sus datos. Los constraints cuyo nombre
# mencionaba "comic" se quitan ANTES de renombrar los campos (RenameField no actualiza
# los campos listados en un constraint) y se crean de nuevo al final con el nombre nuevo.


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0085_remove_old_compilation_fields"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="comic", name="unique_comic_publishing_number_variant_printing"),
        migrations.RemoveConstraint(model_name="collection", name="unique_collection_comic_date_amount_type_participant"),
        migrations.RemoveConstraint(model_name="connectingpiece", name="unique_connecting_comic"),
        migrations.RenameModel(old_name="Comic", new_name="Edition"),
        migrations.RenameField(model_name="edition", old_name="details", new_name="notes"),
        migrations.RenameField(model_name="collection", old_name="comic", new_name="edition"),
        migrations.RenameField(model_name="connectingpiece", old_name="comic", new_name="edition"),
        migrations.RenameField(model_name="connecting", old_name="comics", new_name="editions"),
        migrations.AddConstraint(
            model_name="edition",
            constraint=models.UniqueConstraint(
                fields=("publishing", "number", "variant", "printing"),
                name="unique_edition_publishing_number_variant_printing",
            ),
        ),
        migrations.AddConstraint(
            model_name="collection",
            constraint=models.UniqueConstraint(
                fields=("edition", "trade_date", "amount", "trade_type", "participant"),
                name="unique_collection_edition_date_amount_type_participant",
            ),
        ),
        migrations.AddConstraint(
            model_name="connectingpiece",
            constraint=models.UniqueConstraint(fields=("connecting", "edition"), name="unique_connecting_edition"),
        ),
    ]
