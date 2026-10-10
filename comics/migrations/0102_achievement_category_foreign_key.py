"""Step 3 of 3 (schema): the filled foreign key replaces the old `category` text column.

The foreign key is made NOT NULL first: MySQL does not run DDL in a transaction, so if a row were
somehow left without a category the migration stops there, before anything is dropped. Then the
text column goes and the foreign key takes its name.

Reverse brings the text column back (with its old default) and makes the foreign key nullable
again under its temporary name; 0101's reverse then copies the category codes into the column."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0101_seed_achievement_categories"),
    ]

    operations = [
        migrations.AlterField(
            model_name="achievement",
            name="category_new",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="achievements",
                to="comics.achievementcategory",
            ),
        ),
        migrations.RemoveField(
            model_name="achievement",
            name="category",
        ),
        migrations.RenameField(
            model_name="achievement",
            old_name="category_new",
            new_name="category",
        ),
    ]
