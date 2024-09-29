# Generated by Django 5.0.6 on 2024-09-29 03:56

import datetime
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comics', '0044_remove_collection_signatures'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comic',
            name='ratio',
            field=models.CharField(blank=True, max_length=10, validators=[django.core.validators.RegexValidator(code='invalid_ratio', message='Ratio must be a valid relation Example: (1:100)', regex='^[0-9]{1,3}+:[0-9]{1,3}$')]),
        ),
        migrations.CreateModel(
            name='Signature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=datetime.datetime.now)),
                ('has_coa', models.BooleanField(default=False)),
                ('artist', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='comics.artist')),
                ('collectable', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='comics.collection')),
            ],
        ),
        migrations.AddField(
            model_name='collection',
            name='signatures',
            field=models.ManyToManyField(blank=True, through='comics.Signature', to='comics.artist'),
        ),
    ]
