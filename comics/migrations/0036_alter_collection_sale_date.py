# Generated by Django 5.0.6 on 2024-06-25 22:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0035_collection_buyer_collection_sale_date'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collection',
            name='sale_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
