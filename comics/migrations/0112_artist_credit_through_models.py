"""Explicit through models for Issue.creators and Edition.cover_artists, so an artist cannot be
deleted while it is credited (PROTECT) instead of silently losing its credits.

No data moves:

- IssueCreator and EditionCoverArtist take over the tables of the auto-created relations
  (comics_issue_creators, comics_edition_cover_artists) with the same columns and the same unique
  (owner, artist) pair, so they only change Django's state. The foreign key constraints stay as
  they are: Django applies on_delete itself, and the database constraints (no ON DELETE clause)
  already reject deleting a referenced artist.
- The relation history models (django-simple-history names them after the through model) are
  renamed, which renames their tables (RENAME TABLE: the stored versions are kept). Their foreign
  keys lose the `db_tablespace=''` the auto-created fields had and their verbose name follows the
  new model name: state only, nothing in the database depends on either.
"""

import django.db.models.deletion
from django.db import migrations, models


def history_relation_fk(to: str) -> models.ForeignKey:
    return models.ForeignKey(
        blank=True,
        db_constraint=False,
        null=True,
        on_delete=django.db.models.deletion.DO_NOTHING,
        related_name="+",
        to=to,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0111_seed_reportero"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="IssueCreator",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                        ),
                        (
                            "issue",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE, related_name="creator_entries", to="comics.issue"
                            ),
                        ),
                        (
                            "artist",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT, related_name="issue_credits", to="comics.artist"
                            ),
                        ),
                    ],
                    options={
                        "db_table": "comics_issue_creators",
                        "unique_together": {("issue", "artist")},
                    },
                ),
                migrations.AlterField(
                    model_name="issue",
                    name="creators",
                    field=models.ManyToManyField(
                        blank=True, related_name="issues", through="comics.IssueCreator", to="comics.artist"
                    ),
                ),
                migrations.CreateModel(
                    name="EditionCoverArtist",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                        ),
                        (
                            "edition",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE, related_name="cover_artist_entries", to="comics.edition"
                            ),
                        ),
                        (
                            "artist",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT, related_name="cover_credits", to="comics.artist"
                            ),
                        ),
                    ],
                    options={
                        "db_table": "comics_edition_cover_artists",
                        "unique_together": {("edition", "artist")},
                    },
                ),
                migrations.AlterField(
                    model_name="edition",
                    name="cover_artists",
                    field=models.ManyToManyField(
                        blank=True, related_name="covers", through="comics.EditionCoverArtist", to="comics.artist"
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.RenameModel(old_name="HistoricalIssue_creators", new_name="HistoricalIssueCreator"),
        migrations.RenameModel(old_name="HistoricalEdition_cover_artists", new_name="HistoricalEditionCoverArtist"),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterModelOptions(
                    name="historicalissuecreator",
                    options={"verbose_name": "HistoricalIssueCreator"},
                ),
                migrations.AlterField(
                    model_name="historicalissuecreator",
                    name="artist",
                    field=history_relation_fk("comics.artist"),
                ),
                migrations.AlterField(
                    model_name="historicalissuecreator",
                    name="issue",
                    field=history_relation_fk("comics.issue"),
                ),
                migrations.AlterModelOptions(
                    name="historicaleditioncoverartist",
                    options={"verbose_name": "HistoricalEditionCoverArtist"},
                ),
                migrations.AlterField(
                    model_name="historicaleditioncoverartist",
                    name="artist",
                    field=history_relation_fk("comics.artist"),
                ),
                migrations.AlterField(
                    model_name="historicaleditioncoverartist",
                    name="edition",
                    field=history_relation_fk("comics.edition"),
                ),
            ],
            database_operations=[],
        ),
    ]
