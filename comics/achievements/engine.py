"""Evaluating rules and awarding achievements.

Awards are computed from the current data (counts, flags), never from increments, so evaluating
again is harmless (idempotent) and existing activity counts (retroactive). Levels only go up and
earned achievements are never taken away. Only enabled achievements with a registered rule are
awarded."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.contrib.auth.models import User
from django.db import transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Q, QuerySet
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from comics.models import Achievement, Collection, UserAchievement

from . import rules as _rules  # noqa: F401  (registers the rules)
from .registry import Event, Kind, Rule, all_rules, codes_for

logger = logging.getLogger(__name__)


def level_for(rule: Rule, achievement: Achievement, progress: int) -> int:
    """Level `progress` reaches (0 = none). Uses the prefetched tiers of `achievement`."""
    if rule.kind is Kind.BOOLEAN:
        return 1 if progress else 0
    return max((tier.level for tier in achievement.tiers.all() if progress >= tier.threshold), default=0)


def evaluate(user: User, codes: Iterable[str] | None = None, *, bulk: bool = False) -> list[str]:
    """Award `user` every level earned among the rules `codes` (all rules when None). `bulk` marks
    the awards to be announced together (retroactive runs). Returns the codes that went up."""
    rules = all_rules()
    if codes is not None:
        wanted = set(codes)
        rules = {code: item for code, item in rules.items() if code in wanted}
    if not rules:
        return []
    rows = list(Achievement.objects.filter(code__in=rules).prefetch_related("tiers"))
    for code in sorted(set(rules) - {row.code for row in rows}):
        logger.warning("Achievement rule %r has no Achievement row: it is not awarded.", code)
    enabled = [row for row in rows if row.is_enabled]
    held = dict(
        UserAchievement.objects.filter(user=user, achievement__in=enabled).values_list("achievement_id", "level")
    )
    raised = []
    for achievement in enabled:
        rule = rules[achievement.code]
        level = level_for(rule, achievement, rule.progress(user))
        if level > held.get(achievement.pk, 0):
            with transaction.atomic():
                if UserAchievement.objects.award(user, achievement, level, bulk=bulk):
                    raised.append(achievement.code)
    return raised


def _evaluate_user(user_id: int, codes: frozenset[str]) -> None:
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is not None:
        evaluate(user, codes)


class _PendingEvaluation:
    """An on_commit callback evaluating some rules for one user. Kept mutable so that later events
    of the same user in the same transaction join it instead of adding another evaluation."""

    def __init__(self, user_id: int, codes: set[str]) -> None:
        self.user_id = user_id
        self.codes = set(codes)
        self.done = False  # once run, later events need a new evaluation

    def __call__(self) -> None:
        self.done = True
        _evaluate_user(self.user_id, frozenset(self.codes))


def schedule(event: Event, user_id: int | None) -> None:
    """Re-evaluate the rules `event` can affect for this user, once the current transaction
    commits (right away outside one): the data the rules count is then final, and a rolled back
    change awards nothing. Failures are logged, never raised into the request (robust).

    One evaluation per user and transaction: saving 50 pieces in one transaction evaluates once.
    The pending callbacks are looked up in `connection.run_on_commit` (Django's list of
    (savepoint ids, callback, robust)), which Django itself prunes when a savepoint rolls back;
    codes joined from a rolled-back savepoint are only evaluated in vain, which is harmless."""
    codes = codes_for(event)
    if not (user_id and codes):
        return
    connection = transaction.get_connection()
    pending = _pending_evaluation(connection, user_id) if connection.in_atomic_block else None
    if pending is not None:
        pending.codes |= codes
        return
    transaction.on_commit(_PendingEvaluation(user_id, codes), robust=True)


def _pending_evaluation(connection: BaseDatabaseWrapper, user_id: int) -> _PendingEvaluation | None:
    """This user's evaluation waiting for the current transaction to commit, if any.

    `run_on_commit` is Django's internal list of pending callbacks; checked against Django 5.1,
    where each entry is (savepoint ids, callback, robust). Read defensively: if a future Django
    changes it, this finds nothing and schedule() registers a separate evaluation, as without
    de-duplication; a save never fails because of it."""
    try:
        for entry in connection.run_on_commit:
            callback = entry[1]
            if isinstance(callback, _PendingEvaluation) and callback.user_id == user_id and not callback.done:
                return callback
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    return None


def schedule_for_owners(event: Event, collectors: QuerySet[Collection]) -> None:
    """schedule() for every user with a purchase among `collectors` (a Collection queryset).
    Sold pieces are included: evaluating someone who no longer qualifies is harmless. Skips the
    query when no rule listens to `event`."""
    if codes_for(event):
        purchases = collectors.filter(trade_type=Collection.TradeChoices.BUYING)
        for user_id in purchases.values_list("collector_id", flat=True).distinct():
            schedule(event, user_id)


def schedule_edition_owners(edition_id: int) -> None:
    """After an edition's rarity changed."""
    schedule_for_owners(Event.EDITION_RARITY, Collection.objects.filter(edition_id=edition_id))


def schedule_connecting_owners(connecting_id: int) -> None:
    """After a connecting's pieces changed: only owners of one of its editions can complete it."""
    pieces = Collection.objects.filter(edition__connecting_pieces__connecting_id=connecting_id)
    schedule_for_owners(Event.CONNECTING, pieces)


def schedule_reading_arc_owners(arc_id: int) -> None:
    """After a reading arc's issues changed: owners of an edition (or compilation) of one of them."""
    owners = Collection.objects.filter(
        Q(edition__issue__arc_entries__arc_id=arc_id) | Q(edition__collected_entries__issue__arc_entries__arc_id=arc_id)
    )
    schedule_for_owners(Event.READING_ARC, owners)


def schedule_compilation_owners(edition_id: int) -> None:
    """After the issues a compilation collects changed (they count for reading arcs)."""
    schedule_for_owners(Event.READING_ARC, Collection.objects.filter(edition_id=edition_id))


def schedule_for_everyone(code: str) -> None:
    """Re-evaluate one achievement for every active user after commit: after Gestión enables it
    or changes its levels, so whoever already qualifies gets it without waiting for new activity.

    It runs in the request that saved the change, one user after another; a failure is logged and
    the loop goes on with the next user. Fine for a community of up to a few hundred users; beyond
    that, run `manage.py award_achievements --code <code>` in a background job instead."""

    def run() -> None:
        for user in User.objects.filter(is_active=True).iterator():
            try:
                evaluate(user, [code])
            except Exception:
                logger.exception("Evaluating achievement %r for user %s failed.", code, user.pk)

    transaction.on_commit(run, robust=True)


def _pending_ids(user: User) -> list[int]:
    return list(UserAchievement.objects.filter(user=user, notified=False).values_list("pk", flat=True))


def _claim(ids: list[int]) -> list[int]:
    """Mark these awards announced and return the ones this call marked: an award another request
    claimed in the meantime is left to that request (each award is announced once)."""
    return [pk for pk in ids if UserAchievement.objects.filter(pk=pk, notified=False).update(notified=True)]


def take_notices(user: User) -> list[str]:
    """Messages for the awards not announced yet, which are marked announced. When nothing is
    pending this is one query on the (user, notified) index. Awards are claimed before they are
    announced, so two requests at once never announce the same one. Awards from retroactive runs
    are summed up in a single message instead of one each; disabled achievements are not announced."""
    ids = _pending_ids(user)
    if not ids:
        return []
    claimed = _claim(ids)
    pending = list(UserAchievement.objects.filter(pk__in=claimed).select_related("achievement"))
    visible = [award for award in pending if award.achievement.is_enabled]
    notices = []
    for award in (award for award in visible if not award.bulk_awarded):
        if award.level > 1:
            notices.append(
                _("Achievement unlocked: %(name)s (level %(level)s)")
                % {"name": award.achievement.name, "level": award.level}
            )
        else:
            notices.append(_("Achievement unlocked: %(name)s") % {"name": award.achievement.name})
    bulk = sum(1 for award in visible if award.bulk_awarded)
    if bulk:
        notices.append(
            ngettext(
                "You unlocked %(count)d achievement: see it on your profile.",
                "You unlocked %(count)d achievements: see them on your profile.",
                bulk,
            )
            % {"count": bulk}
        )
    return notices
