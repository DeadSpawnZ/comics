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
from .windows import (
    ALL_WINDOW_KINDS,
    CalendarWindow,
    WindowKind,
    has_window,
    in_calendar_window,
    in_windows,
    year_occurrence,
    yearly_months,
)


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


# ---------- Random ----------


@rule("novato", kind=Kind.BOOLEAN, events=[Event.ACCOUNT], measure=_("has registered"))
def novato(user: User) -> int:
    # Every registered user: awarded when the account is created (and retroactively by
    # award_achievements for the accounts that already existed).
    return 1


@rule(
    "aplicado",
    kind=Kind.BOOLEAN,
    events=[Event.PROFILE],
    measure=_("has filled in first name, last name, email, phone and profile picture"),
)
def aplicado(user: User) -> int:
    # Like every award, it is not taken away if the data is cleared later.
    names_and_email = all(value.strip() for value in (user.first_name, user.last_name, user.email))
    if not names_and_email:
        return 0
    complete = Profile.objects.filter(user=user).exclude(phone="").exclude(avatar="")
    return int(complete.exists())


@rule("fotogenico", kind=Kind.BOOLEAN, events=[Event.PROFILE], measure=_("has a profile picture"))
def fotogenico(user: User) -> int:
    return int(Profile.objects.filter(user=user).exclude(avatar="").exists())


# ---------- Generic rules: Gestión creates achievements with them ----------


@generic_rule(
    "login_in_window",
    kind=Kind.BOOLEAN,
    events=[Event.LOGIN],
    measure=_("has signed in inside the achievement's time window"),
    window_kinds=ALL_WINDOW_KINDS,
)
def login_in_window(user: User, achievement: Achievement) -> int:
    # The last sign-in, in the project's time zone (e.g. Halloween: a yearly 31/10 - 31/10 window).
    # Django updates last_login (its own receiver, connected before ours) before this runs. Only
    # the last sign-in is stored, so it is not retroactive beyond it: a past window counts only if
    # the user has not signed in since. An achievement without any window never counts.
    if user.last_login is None or not has_window(achievement):
        return 0
    return int(in_windows(achievement, user.last_login))


@generic_rule(
    "purchases_each_month_in_window",
    kind=Kind.BOOLEAN,
    events=[Event.COLLECTION],
    measure=_("has purchases in every month of the achievement's yearly window, in the same year"),
    window_kinds=[WindowKind.YEARLY],
)
def purchases_each_month_in_window(user: User, achievement: Achievement) -> int:
    # E.g. Summer: a yearly 01/07 - 31/08 window needs a purchase in July and another in August
    # of the same year. Purchases count sold or not (buying them already happened), by trade date,
    # so it is retroactive. Only the yearly window is read; a row without one never counts.
    if achievement.calendar_window != CalendarWindow.YEARLY:
        return 0
    months = yearly_months(achievement)
    if not months:
        return 0
    # One query: the distinct purchase dates in those months (a few per year at most).
    days = (
        Collection.objects.filter(
            collector=user, trade_type=Collection.TradeChoices.BUYING, trade_date__month__in=months
        )
        .values_list("trade_date", flat=True)
        .distinct()
    )
    covered: dict[int, set[int]] = {}
    for day in days:
        if in_calendar_window(achievement, day):
            covered.setdefault(year_occurrence(achievement, day), set()).add(day.month)
    return int(any(seen >= set(months) for seen in covered.values()))
