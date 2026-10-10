"""The rule registry: the only part of an achievement that lives in code is how it is earned.

Each rule has a key; achievements name the rule that awards them (`Achievement.rule`), so several
achievements can share one rule. A built-in rule measures the user only (`progress(user)`) and has
one row per rule, seeded by a data migration. A generic rule also reads its parameters from the
achievement row (`row_progress(user, achievement)`, e.g. its time windows): Gestión can create
achievements with it, as many as wanted."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from django.contrib.auth.models import User
from django.utils.functional import Promise
from django.utils.translation import gettext as _

from .windows import WindowKind

if TYPE_CHECKING:
    from comics.models import Achievement

UserProgress = Callable[[User], int]
RowProgress = Callable[[User, "Achievement"], int]


class Event(StrEnum):
    """What can change a user's progress. Each rule lists the events that re-evaluate it, so a
    save only evaluates the rules it can affect."""

    ACCOUNT = "account"  # the user registered (the account was created)
    COLLECTION = "collection"  # a piece was added, changed or deleted
    SIGNATURE = "signature"  # a signature was added, changed or deleted
    PROFILE = "profile"  # the profile or the personal info changed (privacy, names, phone, picture)
    LOGIN = "login"  # the user signed in (date-based rules)
    EDITION_RARITY = "edition_rarity"  # an edition the user owns changed its rarity
    CONNECTING = "connecting"  # the pieces of a connecting with an edition the user owns changed
    READING_ARC = "reading_arc"  # the issues of a reading arc (or a compilation) the user owns changed


class Kind(StrEnum):
    COUNT = "count"  # progress is a number; levels come from the achievement's tiers
    BOOLEAN = "boolean"  # done or not; a single level


@dataclass(frozen=True)
class Rule:
    key: str
    kind: Kind
    events: frozenset[Event]
    measure: str | Promise  # what is counted, shown in Gestión and next to the levels
    progress: UserProgress | None = None  # built-in rules: a count, or 0/1 for BOOLEAN rules
    row_progress: RowProgress | None = None  # generic rules: also read the achievement row
    # Generic rules that read the row's time windows: the kinds they use. A row needs at least one
    # of them, and no other kind (validated in Gestión; the rule itself never counts without them).
    window_kinds: frozenset[WindowKind] = frozenset()
    # Count rules: what reaching a threshold means, plural-aware (ngettext_lazy with a "count" key),
    # e.g. "Own at least %(count)s signed piece(s)". Without it: "<measure>: <threshold>".
    requirement: Promise | None = None

    @property
    def generic(self) -> bool:
        return self.row_progress is not None

    @property
    def needs_window(self) -> bool:
        return bool(self.window_kinds)

    @property
    def counted(self) -> bool:
        """Whether achievements with this rule have levels (tiers)."""
        return self.kind is Kind.COUNT

    def requirement_for(self, threshold: int) -> str:
        """The requirement of a level with this threshold, in the active language."""
        if self.requirement is not None:
            return str(self.requirement % {"count": threshold})
        return _("%(measure)s: %(count)s") % {"measure": self.measure, "count": threshold}

    def progress_for(self, user: User, achievement: Achievement) -> int:
        if self.row_progress is not None:
            return self.row_progress(user, achievement)
        assert self.progress is not None
        return self.progress(user)


_RULES: dict[str, Rule] = {}


def _register(item: Rule) -> None:
    if item.key in _RULES:
        raise ValueError(f"Achievement rule {item.key!r} is registered twice.")
    _RULES[item.key] = item


def rule(
    key: str,
    *,
    kind: Kind,
    events: Iterable[Event],
    measure: str | Promise,
    requirement: Promise | None = None,
) -> Callable[[UserProgress], UserProgress]:
    """Register the decorated function as the progress of a built-in rule."""

    def register(progress: UserProgress) -> UserProgress:
        item = Rule(
            key=key, kind=kind, events=frozenset(events), measure=measure, progress=progress, requirement=requirement
        )
        _register(item)
        return progress

    return register


def generic_rule(
    key: str,
    *,
    kind: Kind,
    events: Iterable[Event],
    measure: str | Promise,
    window_kinds: Iterable[WindowKind] = (),
) -> Callable[[RowProgress], RowProgress]:
    """Register the decorated function as the progress of a generic rule (reads the row too)."""

    def register(progress: RowProgress) -> RowProgress:
        item = Rule(
            key=key,
            kind=kind,
            events=frozenset(events),
            measure=measure,
            row_progress=progress,
            window_kinds=frozenset(window_kinds),
        )
        _register(item)
        return progress

    return register


def get_rule(key: str) -> Rule | None:
    return _RULES.get(key)


def all_rules() -> dict[str, Rule]:
    return dict(_RULES)


def generic_rules() -> dict[str, Rule]:
    return {key: item for key, item in _RULES.items() if item.generic}


def builtin_rules() -> dict[str, Rule]:
    """Rules awarded by one seeded row each (Gestión cannot create rows for them)."""
    return {key: item for key, item in _RULES.items() if not item.generic}


def rule_keys_for(event: Event) -> set[str]:
    return {key for key, item in _RULES.items() if event in item.events}
