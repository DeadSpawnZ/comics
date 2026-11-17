"""Give every issue and edition that exists before the history (0107) an initial version: its current
fields and relations (creators; cover artists and collected issues), marked `is_initial`, with no user
(the system). Rows that already have a version are skipped, so it is safe to re-run; the versions
are bulk inserted in this migration's transaction.

Reverse deletes only these initial versions (and their relation rows)."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import FileField
from django.utils import timezone

# Tracked model -> its versioned many-to-many fields (frozen: see comics.models).
TRACKED = {
    "Issue": ["creators"],
    "Edition": ["cover_artists", "collected_issues"],
}
CREATED = "+"
BATCH_SIZE = 500


def create_initial_versions(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    now = timezone.now()
    for model_name, m2m_names in TRACKED.items():
        model = apps.get_model("comics", model_name)
        history = apps.get_model("comics", f"Historical{model_name}")
        fields = model._meta.concrete_fields
        rows = []
        for obj in model.objects.exclude(pk__in=history.objects.values("id")).order_by("pk").iterator():
            values = {}
            for field in fields:
                value = getattr(obj, field.attname)
                values[field.attname] = value.name if isinstance(field, FileField) else value
            rows.append(history(**values, history_date=now, history_type=CREATED, is_initial=True))
        if not rows:
            continue
        history.objects.bulk_create(rows, batch_size=BATCH_SIZE)
        # MySQL does not return the inserted ids: read them back (one initial version per new row).
        created_ids = {row.id for row in rows}
        version_of = {
            pk: history_id
            for pk, history_id in history.objects.filter(is_initial=True).values_list("id", "history_id")
            if pk in created_ids
        }
        for name in m2m_names:
            field = model._meta.get_field(name)
            through = field.remote_field.through
            owner = field.m2m_field_name()
            m2m_history = apps.get_model("comics", f"Historical{through._meta.object_name}")
            columns = [f.attname for f in through._meta.concrete_fields]
            owner_attname = through._meta.get_field(owner).attname
            relation_rows = []
            for row in through.objects.filter(**{f"{owner_attname}__in": created_ids}).order_by("pk").iterator():
                values = {column: getattr(row, column) for column in columns}
                relation_rows.append(m2m_history(**values, history_id=version_of[values[owner_attname]]))
            m2m_history.objects.bulk_create(relation_rows, batch_size=BATCH_SIZE)


def remove_initial_versions(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    for model_name, m2m_names in TRACKED.items():
        model = apps.get_model("comics", model_name)
        history = apps.get_model("comics", f"Historical{model_name}")
        initial = history.objects.filter(is_initial=True)
        for name in m2m_names:
            through = model._meta.get_field(name).remote_field.through
            m2m_history = apps.get_model("comics", f"Historical{through._meta.object_name}")
            m2m_history.objects.filter(history__in=initial.values("history_id")).delete()
        initial.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0107_edition_issue_history"),
    ]

    operations = [
        migrations.RunPython(create_initial_versions, remove_initial_versions),
    ]
