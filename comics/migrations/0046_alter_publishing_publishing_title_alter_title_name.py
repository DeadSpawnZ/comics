# Generated by Django 5.0.6 on 2024-09-29 05:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0045_alter_comic_ratio_signature_collection_signatures'),
    ]

    operations = [
        migrations.AlterField(
            model_name='publishing',
            name='publishing_title',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='title',
            name='name',
            field=models.CharField(max_length=100, unique=True),
        ),
    ]
