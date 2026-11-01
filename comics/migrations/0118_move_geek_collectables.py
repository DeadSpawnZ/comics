"""Split every GeekCollectable into a catalogue Article and the owner's Collectable (a purchase).

GeekCollectable had no owner: its rows go to the user "DeadSpawnZ" or, if that user does not exist,
to the first superuser. The Article keeps the stored image path as is (no file is converted or moved),
so existing files under collectables/ stay where they are; only new uploads go to articles/.

Reverse rebuilds one GeekCollectable per purchase (Collectable with trade_type buying) from the
piece and its article, then deletes every Collectable and Article. Sales, categories, print runs,
valuations and notes did not exist in GeekCollectable and are lost; a purchase without participant
gets the first Dealer, since GeekCollectable.participant cannot be empty."""

from django.db import migrations

OWNER_USERNAME = "DeadSpawnZ"
BATCH_SIZE = 500
BUYING = "buying"


def owner_id(apps, db: str) -> int:
    """The user who receives the ownerless collectables: OWNER_USERNAME, else the first superuser."""
    User = apps.get_model("auth", "User")
    users = User.objects.using(db)
    pk = users.filter(username=OWNER_USERNAME).values_list("pk", flat=True).first()
    if pk is None:
        pk = users.filter(is_superuser=True).order_by("pk").values_list("pk", flat=True).first()
    if pk is None:
        raise RuntimeError(
            f'Cannot move the geek collectables: there is no user "{OWNER_USERNAME}" and no superuser '
            "to own them. Create one of them and run the migration again."
        )
    return pk


def geek_collectables_to_pieces(apps, schema_editor):
    db = schema_editor.connection.alias
    GeekCollectable = apps.get_model("comics", "GeekCollectable")
    Article = apps.get_model("comics", "Article")
    Collectable = apps.get_model("comics", "Collectable")

    rows = GeekCollectable.objects.using(db).order_by("pk")
    if not rows.exists():
        return
    collector_id = owner_id(apps, db)

    pieces = []
    for row in rows.iterator(chunk_size=BATCH_SIZE):
        # One by one: MySQL does not return the primary keys of a bulk_create, and each piece needs
        # its article's. Assigning the stored name keeps the file where it is.
        article = Article.objects.using(db).create(
            name=row.name, description=row.description, category="other", image=row.image.name
        )
        pieces.append(
            Collectable(
                collector_id=collector_id,
                article_id=article.pk,
                amount=row.amount,
                trade_date=row.trade_date,
                trade_type=BUYING,
                participant_id=row.participant_id,
            )
        )
        if len(pieces) >= BATCH_SIZE:
            Collectable.objects.using(db).bulk_create(pieces)
            pieces = []
    Collectable.objects.using(db).bulk_create(pieces)


def pieces_to_geek_collectables(apps, schema_editor):
    db = schema_editor.connection.alias
    GeekCollectable = apps.get_model("comics", "GeekCollectable")
    Article = apps.get_model("comics", "Article")
    Collectable = apps.get_model("comics", "Collectable")
    Dealer = apps.get_model("comics", "Dealer")

    purchases = Collectable.objects.using(db).filter(trade_type=BUYING).select_related("article").order_by("pk")
    fallback_participant_id = None
    if purchases.filter(participant__isnull=True).exists():
        fallback_participant_id = Dealer.objects.using(db).order_by("pk").values_list("pk", flat=True).first()
        if fallback_participant_id is None:
            raise RuntimeError(
                "Cannot rebuild the geek collectables: some pieces have no participant and there is "
                "no Dealer to use instead (GeekCollectable.participant cannot be empty)."
            )

    rows = []
    for piece in purchases.iterator(chunk_size=BATCH_SIZE):
        rows.append(
            GeekCollectable(
                name=piece.article.name,
                description=piece.article.description,
                amount=piece.amount,
                trade_date=piece.trade_date,
                participant_id=piece.participant_id or fallback_participant_id,
                image=piece.article.image.name,
            )
        )
        if len(rows) >= BATCH_SIZE:
            GeekCollectable.objects.using(db).bulk_create(rows)
            rows = []
    GeekCollectable.objects.using(db).bulk_create(rows)

    # Sales point to purchases (SET_NULL) and pieces protect their articles, so pieces go first.
    Collectable.objects.using(db).update(previous_trade=None)
    Collectable.objects.using(db).all().delete()
    Article.objects.using(db).all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("comics", "0117_article_collectable"),
    ]

    operations = [
        migrations.RunPython(geek_collectables_to_pieces, pieces_to_geek_collectables),
    ]
