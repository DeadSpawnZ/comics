# Generated by Django 5.0.6 on 2024-06-25 22:33

import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0033_alter_collection_purchase_price'),
    ]

    operations = [
        migrations.AlterField(
            model_name='publishing',
            name='date',
            field=models.DateField(default=datetime.date(2024, 6, 25)),
        ),
    ]
