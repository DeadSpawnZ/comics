# Generated by Django 5.0.6 on 2024-05-24 07:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0004_alter_comic_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comic',
            name='name',
            field=models.CharField(max_length=100, null=True),
        ),
    ]
