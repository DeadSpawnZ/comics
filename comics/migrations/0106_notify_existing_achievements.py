"""Tell each user who already has achievements, once, how many they have ("You have N
achievements"), so the achievements earned before notifications existed show up under the bell.
Only active achievements count (enabled, in an enabled category), as on profiles; users without
any get nothing. Users who already have this notification are skipped, so it is safe to re-run.

Reverse deletes only these summary notifications (kind "achievements_held", which nothing else
creates)."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Count
from django.utils import timezone

HELD = "achievements_held"  # Notification.Kind.ACHIEVEMENTS_HELD, frozen


def notify_existing_achievements(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    UserAchievement = apps.get_model("comics", "UserAchievement")
    Notification = apps.get_model("comics", "Notification")
    notified = Notification.objects.filter(kind=HELD).values("recipient_id")
    counts = (
        UserAchievement.objects.filter(achievement__is_enabled=True, achievement__category__is_enabled=True)
        .exclude(user_id__in=notified)
        .values("user_id")
        .annotate(total=Count("id"))
        .order_by("user_id")
    )
    now = timezone.now()
    Notification.objects.bulk_create(
        [Notification(recipient_id=row["user_id"], kind=HELD, count=row["total"], created_at=now) for row in counts],
        batch_size=500,
    )


def remove_existing_achievements_notices(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model("comics", "Notification").objects.filter(kind=HELD).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0105_notifications"),
    ]

    operations = [
        migrations.RunPython(notify_existing_achievements, remove_existing_achievements_notices),
    ]
