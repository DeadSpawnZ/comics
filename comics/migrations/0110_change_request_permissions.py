"""Grant the change-request permissions to the roles: Reviewers validate (and reject pending
requests), Administrators validate and approve.

- The permissions belong to the ChangeRequest model (0109). post_migrate creates model permissions
  only after the whole migrate run, too late for this one, so they are created here if missing
  (get_or_create, like 0093).
- The groups are get_or_create too, so this also works on a database where 0093's groups were
  deleted; existing memberships and permissions are kept.
- Reverse removes these permissions from the two groups (the groups and the permissions stay).
- `manage.py ensure_roles` performs the same forward steps outside migrations.

The table is frozen here on purpose (migrations must not depend on code that keeps changing);
comics/roles.py holds the runtime copy and a test checks that 0093 plus this match it."""

from django.db import migrations

REVIEWERS = "Reviewers"
ADMINISTRATORS = "Administrators"

# (app_label, model, codename, name)
VALIDATE = ("comics", "changerequest", "validate_changerequest", "Can validate or reject pending change requests")
APPROVE = ("comics", "changerequest", "approve_changerequest", "Can approve or reject change requests")

ROLE_PERMISSIONS = {
    REVIEWERS: [VALIDATE],
    ADMINISTRATORS: [VALIDATE, APPROVE],
}


def _permission(apps, db, app_label, model, codename, label):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    content_type, _created = ContentType.objects.using(db).get_or_create(app_label=app_label, model=model)
    permission, _created = Permission.objects.using(db).get_or_create(
        content_type=content_type, codename=codename, defaults={"name": label}
    )
    return permission


def grant(apps, schema_editor):
    db = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    for name, permissions in ROLE_PERMISSIONS.items():
        group, _created = Group.objects.using(db).get_or_create(name=name)
        group.permissions.add(*(_permission(apps, db, *permission) for permission in permissions))


def revoke(apps, schema_editor):
    db = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for name, permissions in ROLE_PERMISSIONS.items():
        group = Group.objects.using(db).filter(name=name).first()
        if group is None:
            continue
        codenames = [codename for _app_label, _model, codename, _label in permissions]
        group.permissions.remove(
            *Permission.objects.using(db).filter(content_type__app_label="comics", codename__in=codenames)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("comics", "0109_change_requests"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
