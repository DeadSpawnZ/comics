# Generated by Django 5.0.6 on 2024-06-02 00:49

import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0021_rename_collector_id_collection_collector_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='publishing',
            name='date',
            field=models.DateField(default=datetime.date(2024, 6, 2)),
        ),
    ]
