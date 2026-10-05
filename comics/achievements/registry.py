"""The rule registry: the only part of an achievement that lives in code is how it is earned."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from django.contrib.auth.models import User
from django.utils.functional import Promise


class Event(StrEnum):
    """What can change a user's progress. Each rule lists the events that re-evaluate it, so a
    save only evaluates the rules it can affect."""

    COLLECTION = "collection"  # a piece was added, changed or deleted
    SIGNATURE = "signature"  # a signature was added, changed or deleted
    PROFILE = "profile"  # the profile changed (e.g. privacy)
    LOGIN = "login"  # the user signed in (date-based rules)


class Kind(StrEnum):
    COUNT = "count"  # progress is a number; levels come from the achievement's tiers
    BOOLEAN = "boolean"  # done or not; a single level


@dataclass(frozen=True)
class Rule:
    code: str
    kind: Kind
    events: frozenset[Event]
    progress: Callable[[User], int]  # a count, or 0/1 for BOOLEAN rules
    measure: str | Promise  # what is counted, shown in Gestión next to the tiers


_RULES: dict[str, Rule] = {}


def rule(
    code: str, *, kind: Kind, events: Iterable[Event], measure: str | Promise
) -> Callable[[Callable[[User], int]], Callable[[User], int]]:
    """Register the decorated function as the progress of the achievement with this `code`."""

    def register(progress: Callable[[User], int]) -> Callable[[User], int]:
        if code in _RULES:
            raise ValueError(f"Achievement rule {code!r} is registered twice.")
        _RULES[code] = Rule(code=code, kind=kind, events=frozenset(events), progress=progress, measure=measure)
        return progress

    return register


def get_rule(code: str) -> Rule | None:
    return _RULES.get(code)


def all_rules() -> dict[str, Rule]:
    return dict(_RULES)


def codes_for(event: Event) -> set[str]:
    return {code for code, item in _RULES.items() if event in item.events}
