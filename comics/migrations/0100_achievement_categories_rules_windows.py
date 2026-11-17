"""Achievement categories become rows (managed in Gestión), achievements get the key of the rule
that awards them (`rule`, so a rule can serve several achievements) and optional time windows.

Step 1 of 3 (schema): the category table and a temporary nullable foreign key next to the old
`category` text column; 0101 fills both new things and 0102 swaps the columns."""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def day_validators() -> list[django.core.validators.BaseValidator]:
    return [django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(31)]


def month_validators() -> list[django.core.validators.BaseValidator]:
    return [django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)]


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0099_seed_more_achievements"),
    ]

    operations = [
        migrations.CreateModel(
            name="AchievementCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=30, unique=True)),
                ("name_en", models.CharField(max_length=80)),
                ("name_es", models.CharField(max_length=80)),
                ("description_en", models.CharField(blank=True, max_length=255)),
                ("description_es", models.CharField(blank=True, max_length=255)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_enabled", models.BooleanField(default=True)),
                ("image", models.ImageField(blank=True, upload_to="achievement_categories/")),
            ],
            options={
                "verbose_name_plural": "achievement categories",
                "ordering": ["display_order", "code"],
            },
        ),
        migrations.AddField(
            model_name="achievement",
            name="category_new",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="comics.achievementcategory",
            ),
        ),
        migrations.AddField(
            model_name="achievement",
            name="rule",
            field=models.CharField(default="", max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="achievement",
            name="calendar_window",
            field=models.CharField(
                choices=[
                    ("none", "No date window"),
                    ("yearly", "Every year, between two days"),
                    ("absolute", "Between two dates"),
                ],
                default="none",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_start_day",
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=day_validators()),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_start_month",
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=month_validators()),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_end_day",
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=day_validators()),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_end_month",
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=month_validators()),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_end_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="achievement",
            name="window_end_time",
            field=models.TimeField(blank=True, null=True),
        ),
    ]
