"""Time windows of an achievement: when something has to happen for a windowed rule to count it.

An achievement may have a calendar window and a time-of-day window; when both are set a moment
must fall inside both. Every moment is looked at in the project's TIME_ZONE (settings), whatever
the time zone it comes in.

- Yearly calendar window: from a day/month to a day/month, every year, both days included. It may
  wrap the end of the year (24/12 - 06/01). The 29th of February is a valid bound: days are
  compared as (month, day) pairs, so in a year without that day a window starting on 29/02 starts
  on 01/03 and one ending on 29/02 ends on 28/02 (a 29/02 - 29/02 window only happens in leap years).
- Absolute calendar window: from a date to a date (one-off events), both days included.
- Time-of-day window: from a time (included) to a time (excluded), so 23:00 - 02:00 wraps midnight
  and ends right before 02:00. The calendar day and the time of day are checked separately on the
  same local moment: a 31/10 window with 23:00 - 02:00 does not match 01/11 at 01:00 (use a
  31/10 - 01/11 window for that).

This module has no model imports: models.py uses it to validate, rules use it to decide."""

from __future__ import annotations

import datetime
import zoneinfo
from typing import Protocol

from django.conf import settings
from django.db.models import TextChoices
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

# Any leap year: it makes 29/02 a valid yearly bound.
_LEAP_YEAR = 2000


class CalendarWindow(TextChoices):
    NONE = "none", _("No date window")
    YEARLY = "yearly", _("Every year, between two days")
    ABSOLUTE = "absolute", _("Between two dates")


class Windowed(Protocol):
    """What the helpers read: the window fields of an Achievement."""

    calendar_window: str
    window_start_day: int | None
    window_start_month: int | None
    window_end_day: int | None
    window_end_month: int | None
    window_start_date: datetime.date | None
    window_end_date: datetime.date | None
    window_start_time: datetime.time | None
    window_end_time: datetime.time | None


def project_zone() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(settings.TIME_ZONE)


def has_calendar_window(item: Windowed) -> bool:
    return item.calendar_window in (CalendarWindow.YEARLY, CalendarWindow.ABSOLUTE)


def has_time_window(item: Windowed) -> bool:
    return item.window_start_time is not None and item.window_end_time is not None


def has_window(item: Windowed) -> bool:
    return has_calendar_window(item) or has_time_window(item)


def in_windows(item: Windowed, moment: datetime.datetime) -> bool:
    """Whether `moment` falls inside every window `item` has. False when it has none: a windowed
    rule never counts an achievement nobody configured. A naive `moment` is taken as project time."""
    if not has_window(item):
        return False
    zone = project_zone()
    local = timezone.make_aware(moment, zone) if timezone.is_naive(moment) else moment.astimezone(zone)
    return in_calendar_window(item, local.date()) and in_time_window(item, local.time())


def in_calendar_window(item: Windowed, day: datetime.date) -> bool:
    """True when `item` has no calendar window."""
    if item.calendar_window == CalendarWindow.YEARLY:
        start = (item.window_start_month, item.window_start_day)
        end = (item.window_end_month, item.window_end_day)
        if None in start or None in end:
            return False
        current = (day.month, day.day)
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end  # wraps the end of the year
    if item.calendar_window == CalendarWindow.ABSOLUTE:
        if item.window_start_date is None or item.window_end_date is None:
            return False
        return item.window_start_date <= day <= item.window_end_date
    return True


def in_time_window(item: Windowed, moment: datetime.time) -> bool:
    """True when `item` has no time-of-day window."""
    if not has_time_window(item):
        return True
    start, end = item.window_start_time, item.window_end_time
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end  # wraps midnight


def window_errors(item: Windowed) -> dict[str, list[str]]:
    """Validation errors of the window fields, by field name (empty when they are consistent)."""
    errors: dict[str, list[str]] = {}
    for field, message in [*_calendar_errors(item), *_time_errors(item)]:
        errors.setdefault(field, []).append(str(message))
    return errors


def _calendar_errors(item: Windowed) -> list[tuple[str, str | Promise]]:
    errors: list[tuple[str, str | Promise]] = []
    if item.calendar_window == CalendarWindow.YEARLY:
        for side, day, month in [
            ("start", item.window_start_day, item.window_start_month),
            ("end", item.window_end_day, item.window_end_month),
        ]:
            if day is None or month is None:
                errors.append((f"window_{side}_day", _("Enter the day and the month.")))
            elif not _is_real_day(month, day):
                errors.append((f"window_{side}_day", _("That day does not exist in that month.")))
    elif item.calendar_window == CalendarWindow.ABSOLUTE:
        start, end = item.window_start_date, item.window_end_date
        if start is None:
            errors.append(("window_start_date", _("Enter the start date.")))
        if end is None:
            errors.append(("window_end_date", _("Enter the end date.")))
        if start and end and end < start:
            errors.append(("window_end_date", _("The end date cannot be before the start date.")))
    return errors


def _time_errors(item: Windowed) -> list[tuple[str, str | Promise]]:
    start, end = item.window_start_time, item.window_end_time
    if (start is None) != (end is None):
        return [("window_end_time" if end is None else "window_start_time", _("Enter both times, or neither."))]
    if start is not None and start == end:
        return [("window_end_time", _("The end time must be different from the start time."))]
    return []


def clear_unused(item: Windowed) -> None:
    """Empty the fields of the calendar window kinds `item` does not use, so a switched kind does
    not keep stale bounds."""
    if item.calendar_window != CalendarWindow.YEARLY:
        item.window_start_day = item.window_start_month = None
        item.window_end_day = item.window_end_month = None
    if item.calendar_window != CalendarWindow.ABSOLUTE:
        item.window_start_date = item.window_end_date = None


def _is_real_day(month: int, day: int) -> bool:
    try:
        datetime.date(_LEAP_YEAR, month, day)
    except ValueError:
        return False
    return True
