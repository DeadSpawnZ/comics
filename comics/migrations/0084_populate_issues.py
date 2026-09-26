from django.db import migrations


def create_issues(apps, schema_editor):
    """Un issue por cada (publishing, numero); cada comic queda como edicion de ese issue.
    `details` no se toca: son notas de la edicion (variante exclusiva, reimpresion), no sinopsis."""
    Comic = apps.get_model("comics", "Comic")
    Issue = apps.get_model("comics", "Issue")

    issues = {}
    comics = list(Comic.objects.order_by("id"))
    for comic in comics:
        key = (comic.publishing_id, comic.number.strip())
        issue = issues.get(key)
        if issue is None:
            issue = Issue.objects.create(publishing_id=comic.publishing_id, number=key[1])
            issues[key] = issue
        comic.issue_id = issue.id

    Comic.objects.bulk_update(comics, ["issue"], batch_size=500)


def remove_issues(apps, schema_editor):
    Comic = apps.get_model("comics", "Comic")
    Issue = apps.get_model("comics", "Issue")
    Comic.objects.update(issue=None)
    Issue.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0083_issue"),
    ]

    operations = [
        migrations.RunPython(create_issues, remove_issues),
    ]
