"""Create the role groups (Collectors, Reviewers, Administrators) and place the existing users.

- Every existing user becomes a Collector; superusers also become Administrators.
- Administrators get `comics.access_management` (Gestión).
- Safe to run on a database where some of this already exists: everything is get_or_create / add.
- Reverse deletes the groups named Collectors, Reviewers and Administrators (and with them their
  memberships and permission links), including groups with those names that existed before this
  migration ran (there were none on the real database). The permission itself belongs to the
  AreaAccess model (0092) and is left in place.
- `manage.py ensure_roles` performs the same forward steps outside migrations.

The role table is frozen here on purpose (migrations must not depend on code that keeps changing);
comics/roles.py holds the runtime copy and a test checks both agree."""

from django.db import migrations

COLLECTORS = "Collectors"
REVIEWERS = "Reviewers"
ADMINISTRATORS = "Administrators"

# (app_label, model, codename, name): permissions created here if Django has not created them yet.
# post_migrate creates model permissions only after the whole migrate run, too late for this one.
ACCESS_MANAGEMENT = ("comics", "areaaccess", "access_management", "Can access the management area (Gestión)")

ROLE_PERMISSIONS = {
    COLLECTORS: [],
    REVIEWERS: [],
    ADMINISTRATORS: [ACCESS_MANAGEMENT],
}


def create_roles(apps, schema_editor):
    db = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    groups = {}
    for name, permissions in ROLE_PERMISSIONS.items():
        group, _created = Group.objects.using(db).get_or_create(name=name)
        for app_label, model, codename, label in permissions:
            content_type, _created = ContentType.objects.using(db).get_or_create(app_label=app_label, model=model)
            permission, _created = Permission.objects.using(db).get_or_create(
                content_type=content_type, codename=codename, defaults={"name": label}
            )
            group.permissions.add(permission)
        groups[name] = group

    users = User.objects.using(db)
    groups[COLLECTORS].user_set.add(*users.values_list("pk", flat=True))
    groups[ADMINISTRATORS].user_set.add(*users.filter(is_superuser=True).values_list("pk", flat=True))


def delete_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).filter(name__in=ROLE_PERMISSIONS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("comics", "0092_area_access"),
    ]

    operations = [
        migrations.RunPython(create_roles, delete_roles),
    ]
