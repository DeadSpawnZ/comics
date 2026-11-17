"""Give every existing user an empty profile (new users get one from the post_save signal).

Only creates profiles that are missing, so running it again changes nothing. Reverse does nothing:
unapplying 0094 drops the whole table, and deleting rows here would throw away pictures and phone
numbers users added after the migration ran."""

from django.db import migrations


def create_missing_profiles(apps, schema_editor):
    db = schema_editor.connection.alias
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("comics", "Profile")
    missing = User.objects.using(db).filter(profile__isnull=True).values_list("pk", flat=True)
    Profile.objects.using(db).bulk_create([Profile(user_id=pk) for pk in missing])


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0094_profile"),
    ]

    operations = [
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
