# Generated by Django 5.0.6 on 2024-05-24 08:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0006_alter_comic_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comic',
            name='artists',
            field=models.ManyToManyField(null=True, to='comics.artist'),
        ),
    ]
