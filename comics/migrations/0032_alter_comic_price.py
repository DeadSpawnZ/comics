# Generated by Django 5.0.6 on 2024-06-24 03:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0031_alter_collection_collector_alter_collection_comic_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comic',
            name='price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=8),
        ),
    ]
