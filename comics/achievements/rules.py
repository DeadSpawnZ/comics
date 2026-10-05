"""How each achievement is earned. Names, texts, icons and tiers are data (Gestión); a rule only
measures progress. Every built-in rule needs a row (rule = its key), seeded by a data migration;
generic rules read the achievement row too, and Gestión creates their rows.

Rules run on every event they listen to, so each one is one or two aggregate queries: never a
loop over the user's whole collection. "Owned" means Collection.objects.owned_by (purchases not
sold); a rule about past activity (purchases, logins) says so."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models import Count, F, Max, Min, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from comics.models import Achievement, Collection, Connecting, Edition, Profile, ReadingArc

from .registry import Event, Kind, generic_rule, rule
from .windows import has_window, in_windows


@rule("origin", kind=Kind.BOOLEAN, events=[Event.COLLECTION], measure=_("has registered a piece"))
def origin(user: User) -> int:
    return int(Collection.objects.filter(collector=user).exists())


@rule("great_power", kind=Kind.COUNT, events=[Event.COLLECTION], measure=_("comics in the collection"))
def great_power(user: User) -> int:
    return Collection.objects.owned_by(user).count()


@rule("veteran", kind=Kind.COUNT, events=[Event.LOGIN], measure=_("full years as a member"))
def veteran(user: User) -> int:
    today = timezone.localdate()
    joined = timezone.localdate(user.date_joined)
    years = today.year - joined.year
    if (today.month, today.day) < (joined.month, joined.day):
        years -= 1
    return max(years, 0)


@rule("secret_identity", kind=Kind.BOOLEAN, events=[Event.PROFILE], measure=_("has made the profile private"))
def secret_identity(user: User) -> int:
    return int(Profile.objects.filter(user=user, is_public=False).exists())


# ---------- Treasures ----------


@rule(
    "infinity_gauntlet",
    kind=Kind.COUNT,
    events=[Event.COLLECTION, Event.EDITION_RARITY],
    measure=_("very rare or legendary editions owned"),
)
def infinity_gauntlet(user: User) -> int:
    # Distinct editions: two copies of the same edition are one stone.
    owned = Collection.objects.owned_by(user).filter(edition__rarity__in=Edition.GREAT_RARITY)
    return owned.values("edition_id").distinct().count()


@rule(
    "batcave_vault",
    kind=Kind.COUNT,
    events=[Event.COLLECTION, Event.SIGNATURE],
    measure=_("signed pieces owned"),
)
def batcave_vault(user: User) -> int:
    return Collection.objects.owned_by(user).filter(signature__isnull=False).distinct().count()


# ---------- Collection ----------


@rule("multiverse", kind=Kind.COUNT, events=[Event.COLLECTION], measure=_("most editions owned of one issue"))
def multiverse(user: User) -> int:
    per_issue = (
        Collection.objects.owned_by(user)
        .filter(edition__issue__isnull=False)
        .values("edition__issue_id")
        .annotate(editions=Count("edition_id", distinct=True))
        .order_by("-editions")
        .values_list("editions", flat=True)
    )
    return per_issue.first() or 0


@rule("speed_force", kind=Kind.COUNT, events=[Event.COLLECTION], measure=_("most purchases on a single day"))
def speed_force(user: User) -> int:
    # Purchases, sold or not: buying them that day already happened. The trade date is the only
    # date a piece has (no creation timestamp), which is also what makes this retroactive.
    per_day = (
        Collection.objects.filter(collector=user, trade_type=Collection.TradeChoices.BUYING)
        .values("trade_date")
        .annotate(purchases=Count("id"))
        .order_by("-purchases")
        .values_list("purchases", flat=True)
    )
    return per_day.first() or 0


@rule("lazarus_pit", kind=Kind.BOOLEAN, events=[Event.COLLECTION], measure=_("has bought again an issue they sold"))
def lazarus_pit(user: User) -> int:
    # A purchase of an issue (any edition of it) dated after a sale of that issue, i.e. some issue
    # whose latest purchase is later than its earliest sale. One grouped pass (a correlated EXISTS
    # per purchase took ~0.5 s on MySQL with 1000 pieces). Same-day trades have no order: not counted.
    rebought = (
        Collection.objects.filter(collector=user, edition__issue__isnull=False)
        .values("edition__issue_id")
        .annotate(
            first_sale=Min("trade_date", filter=Q(trade_type=Collection.TradeChoices.SELLING)),
            last_purchase=Max("trade_date", filter=Q(trade_type=Collection.TradeChoices.BUYING)),
        )
        .filter(last_purchase__gt=F("first_sale"))
    )
    return int(rebought.exists())


# ---------- Sagas ----------


@rule(
    "assemble",
    kind=Kind.COUNT,
    events=[Event.COLLECTION, Event.CONNECTING],
    measure=_("connectings completed"),
)
def assemble(user: User) -> int:
    # Same ownership as Connecting.ownership_grid: every piece's edition owned.
    owned_editions = Collection.objects.owned_by(user).values("edition_id")
    return (
        Connecting.objects.annotate(
            pieces_total=Count("pieces", distinct=True),
            pieces_owned=Count("pieces", filter=Q(pieces__edition_id__in=owned_editions), distinct=True),
        )
        .filter(pieces_total__gt=0, pieces_owned=F("pieces_total"))
        .count()
    )


@rule(
    "chronicler",
    kind=Kind.COUNT,
    events=[Event.COLLECTION, Event.READING_ARC],
    measure=_("reading arcs completed"),
)
def chronicler(user: User) -> int:
    # Same ownership as ReadingArc.entries_with_ownership: an issue is owned through any edition
    # of it or a compilation that collects it.
    owned = Collection.objects.owned_by(user)
    in_owned_edition = Q(entries__issue_id__in=owned.values("edition__issue_id"))
    in_owned_compilation = Q(entries__issue_id__in=owned.values("edition__collected_entries__issue_id"))
    return (
        ReadingArc.objects.annotate(
            issues_total=Count("entries", distinct=True),
            issues_owned=Count("entries", filter=in_owned_edition | in_owned_compilation, distinct=True),
        )
        .filter(issues_total__gt=0, issues_owned=F("issues_total"))
        .count()
    )


# ---------- Community ----------


# ---------- Generic rules: Gestión creates achievements with them ----------


@generic_rule(
    "login_in_window",
    kind=Kind.BOOLEAN,
    events=[Event.LOGIN],
    measure=_("has signed in inside the achievement's time window"),
    needs_window=True,
)
def login_in_window(user: User, achievement: Achievement) -> int:
    # The last sign-in, in the project's time zone (e.g. Halloween: a yearly 31/10 - 31/10 window).
    # Django updates last_login (its own receiver, connected before ours) before this runs. Only
    # the last sign-in is stored, so it is not retroactive beyond it: a past window counts only if
    # the user has not signed in since. An achievement without any window never counts.
    if user.last_login is None or not has_window(achievement):
        return 0
    return int(in_windows(achievement, user.last_login))
