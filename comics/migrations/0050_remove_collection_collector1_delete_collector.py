# Generated by Django 5.0.6 on 2024-09-29 06:52

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0049_alter_collection_collector'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='collection',
            name='collector1',
        ),
        migrations.DeleteModel(
            name='Collector',
        ),
    ]
