"""Achievement time windows (comics/achievements/windows.py): yearly windows (with year wrap and
29 February), absolute date windows, time-of-day windows (with midnight wrap), both combined, the
project time zone (and DST in another one), and their validation in the model and the form."""

import datetime
import zoneinfo

from django.test import override_settings

from comics.achievements.registry import get_rule
from comics.achievements.windows import (
    CalendarWindow,
    has_window,
    in_calendar_window,
    in_time_window,
    in_windows,
    window_errors,
)
from comics.forms import AchievementCreateForm
from comics.models import Achievement, AchievementCategory

from .base import ComiTestCase

LOCAL = zoneinfo.ZoneInfo("America/Monterrey")
UTC = datetime.UTC


def yearly(start: tuple[int, int], end: tuple[int, int], **fields: object) -> Achievement:
    """An unsaved achievement with a yearly window from (day, month) to (day, month)."""
    return Achievement(
        calendar_window=CalendarWindow.YEARLY,
        window_start_day=start[0],
        window_start_month=start[1],
        window_end_day=end[0],
        window_end_month=end[1],
        **fields,
    )


def absolute(start: datetime.date, end: datetime.date, **fields: object) -> Achievement:
    return Achievement(calendar_window=CalendarWindow.ABSOLUTE, window_start_date=start, window_end_date=end, **fields)


def hours(start: str, end: str, **fields: object) -> Achievement:
    return Achievement(
        window_start_time=datetime.time.fromisoformat(start), window_end_time=datetime.time.fromisoformat(end), **fields
    )


def local(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=LOCAL)


class CalendarWindowTests(ComiTestCase):
    def check(self, item: Achievement, cases: dict[datetime.date, bool]) -> None:
        for day, expected in cases.items():
            with self.subTest(day=day):
                self.assertEqual(in_calendar_window(item, day), expected)

    def test_single_day_every_year(self) -> None:
        self.check(
            yearly((31, 10), (31, 10)),
            {
                datetime.date(2025, 10, 30): False,
                datetime.date(2025, 10, 31): True,
                datetime.date(1999, 10, 31): True,
                datetime.date(2025, 11, 1): False,
            },
        )

    def test_yearly_window_wrapping_the_end_of_the_year(self) -> None:
        self.check(
            yearly((24, 12), (6, 1)),
            {
                datetime.date(2025, 12, 23): False,
                datetime.date(2025, 12, 24): True,
                datetime.date(2025, 12, 31): True,
                datetime.date(2026, 1, 1): True,
                datetime.date(2026, 1, 6): True,
                datetime.date(2026, 1, 7): False,
                datetime.date(2026, 6, 15): False,
            },
        )

    def test_29_february_bounds(self) -> None:
        self.check(
            yearly((29, 2), (29, 2)),
            {
                datetime.date(2024, 2, 29): True,
                datetime.date(2024, 2, 28): False,
                datetime.date(2025, 2, 28): False,  # no 29/02 that year: the window does not happen
                datetime.date(2025, 3, 1): False,
            },
        )
        self.check(
            yearly((29, 2), (5, 3)),
            {datetime.date(2025, 2, 28): False, datetime.date(2025, 3, 1): True, datetime.date(2024, 2, 29): True},
        )
        self.check(
            yearly((20, 2), (29, 2)),
            {datetime.date(2025, 2, 28): True, datetime.date(2025, 3, 1): False, datetime.date(2024, 2, 29): True},
        )

    def test_absolute_window_includes_both_days(self) -> None:
        self.check(
            absolute(datetime.date(2026, 12, 1), datetime.date(2026, 12, 3)),
            {
                datetime.date(2026, 11, 30): False,
                datetime.date(2026, 12, 1): True,
                datetime.date(2026, 12, 3): True,
                datetime.date(2026, 12, 4): False,
                datetime.date(2025, 12, 2): False,  # another year
            },
        )

    def test_no_calendar_window_does_not_restrict(self) -> None:
        self.assertTrue(in_calendar_window(Achievement(), datetime.date(2025, 5, 5)))


class TimeWindowTests(ComiTestCase):
    def check(self, item: Achievement, cases: dict[str, bool]) -> None:
        for moment, expected in cases.items():
            with self.subTest(moment=moment):
                self.assertEqual(in_time_window(item, datetime.time.fromisoformat(moment)), expected)

    def test_daytime_window_excludes_its_end(self) -> None:
        self.check(hours("09:00", "17:00"), {"08:59": False, "09:00": True, "16:59:59": True, "17:00": False})

    def test_window_wrapping_midnight(self) -> None:
        self.check(
            hours("23:00", "02:00"),
            {
                "22:59": False,
                "23:00": True,
                "23:59:59": True,
                "00:00": True,
                "01:59": True,
                "02:00": False,
                "12:00": False,
            },
        )


class InWindowsTests(ComiTestCase):
    def test_without_any_window_nothing_matches(self) -> None:
        item = Achievement()
        self.assertFalse(has_window(item))
        self.assertFalse(in_windows(item, local(2025, 10, 31, 12, 0)))

    def test_both_windows_must_match(self) -> None:
        item = yearly((31, 10), (31, 10), window_start_time=datetime.time(20), window_end_time=datetime.time(23))
        self.assertTrue(in_windows(item, local(2025, 10, 31, 21, 0)))
        self.assertFalse(in_windows(item, local(2025, 10, 31, 12, 0)))  # right day, wrong time
        self.assertFalse(in_windows(item, local(2025, 10, 30, 21, 0)))  # right time, wrong day

    def test_day_and_time_are_checked_on_the_same_local_moment(self) -> None:
        # A night crossing midnight is not stretched into the next day: 01/11 01:00 is outside 31/10.
        item = yearly((31, 10), (31, 10), window_start_time=datetime.time(23), window_end_time=datetime.time(2))
        self.assertTrue(in_windows(item, local(2025, 10, 31, 23, 30)))
        self.assertFalse(in_windows(item, local(2025, 11, 1, 1, 0)))
        self.assertTrue(in_windows(item, local(2025, 10, 31, 1, 0)))  # the early hours of the 31st

    def test_moments_are_read_in_the_project_time_zone(self) -> None:
        item = yearly((31, 10), (31, 10))
        self.assertTrue(in_windows(item, datetime.datetime(2025, 11, 1, 3, 0, tzinfo=UTC)))  # 31/10 21:00 locally
        self.assertFalse(in_windows(item, datetime.datetime(2025, 10, 31, 3, 0, tzinfo=UTC)))  # still 30/10 locally
        self.assertTrue(in_windows(item, datetime.datetime(2025, 10, 31, 12, 0)))  # naive: project time

    @override_settings(TIME_ZONE="America/New_York")
    def test_daylight_saving_changes(self) -> None:
        night = hours("01:00", "02:00")
        # 2 November 2025: 01:30 happens twice (EDT, then EST); both are inside.
        self.assertTrue(in_windows(night, datetime.datetime(2025, 11, 2, 5, 30, tzinfo=UTC)))
        self.assertTrue(in_windows(night, datetime.datetime(2025, 11, 2, 6, 30, tzinfo=UTC)))
        # 9 March 2025: clocks jump from 02:00 to 03:00, so 02:00-03:00 never happens that night.
        skipped = hours("02:00", "03:00")
        self.assertFalse(in_windows(skipped, datetime.datetime(2025, 3, 9, 7, 30, tzinfo=UTC)))  # 03:30 EDT
        self.assertTrue(in_windows(skipped, datetime.datetime(2025, 3, 10, 6, 30, tzinfo=UTC)))  # 02:30 EDT


class WindowValidationTests(ComiTestCase):
    def test_consistent_windows_have_no_errors(self) -> None:
        for item in [
            Achievement(),
            yearly((29, 2), (1, 3)),
            yearly((24, 12), (6, 1)),
            absolute(datetime.date(2026, 1, 1), datetime.date(2026, 1, 1)),
            hours("23:00", "02:00"),
        ]:
            with self.subTest(item=item.calendar_window):
                self.assertEqual(window_errors(item), {})

    def test_yearly_needs_real_days(self) -> None:
        errors = window_errors(yearly((31, 4), (30, 2)))
        self.assertEqual(errors["window_start_day"], ["That day does not exist in that month."])
        self.assertEqual(errors["window_end_day"], ["That day does not exist in that month."])
        missing = Achievement(calendar_window=CalendarWindow.YEARLY, window_start_day=1)
        self.assertEqual(set(window_errors(missing)), {"window_start_day", "window_end_day"})

    def test_absolute_needs_ordered_dates(self) -> None:
        errors = window_errors(absolute(datetime.date(2026, 2, 1), datetime.date(2026, 1, 1)))
        self.assertEqual(errors, {"window_end_date": ["The end date cannot be before the start date."]})
        missing = Achievement(calendar_window=CalendarWindow.ABSOLUTE)
        self.assertEqual(set(window_errors(missing)), {"window_start_date", "window_end_date"})

    def test_times_come_in_pairs_and_differ(self) -> None:
        self.assertEqual(
            window_errors(Achievement(window_start_time=datetime.time(9))),
            {"window_end_time": ["Enter both times, or neither."]},
        )
        self.assertEqual(
            window_errors(hours("09:00", "09:00")),
            {"window_end_time": ["The end time must be different from the start time."]},
        )

    def test_model_clean_drops_the_fields_of_other_kinds(self) -> None:
        item = yearly((24, 12), (6, 1), window_start_date=datetime.date(2026, 1, 1))
        item.calendar_window = CalendarWindow.NONE
        item.clean()
        self.assertEqual((item.window_start_day, item.window_end_month, item.window_start_date), (None, None, None))


class WindowFormTests(ComiTestCase):
    def data(self, **overrides: str) -> dict[str, str]:
        values = {
            "rule": "login_in_window",
            "name_en": "Christmas Eve",
            "name_es": "Nochebuena",
            "category": str(AchievementCategory.objects.get(code="community").pk),
            "display_order": "0",
            "is_enabled": "on",
            "calendar_window": "yearly",
            "window_start_day": "24",
            "window_start_month": "12",
            "window_end_day": "24",
            "window_end_month": "12",
        }
        values.update(overrides)
        return values

    def form(self, **overrides: str) -> AchievementCreateForm:
        return AchievementCreateForm(self.data(**overrides), rule=get_rule("login_in_window"))

    def test_valid_window(self) -> None:
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.code, "g_christmas_eve")

    def test_a_windowed_rule_needs_a_window(self) -> None:
        form = self.form(calendar_window="none")
        self.assertFalse(form.is_valid())
        self.assertIn("only counts inside a time window", form.non_field_errors()[0])
        self.assertTrue(
            self.form(calendar_window="none", window_start_time="23:00", window_end_time="02:00").is_valid()
        )

    def test_errors_point_at_the_fields(self) -> None:
        form = self.form(window_start_day="31", window_start_month="4")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["window_start_day"], ["That day does not exist in that month."])
        form = self.form(calendar_window="absolute", window_start_date="2026-02-01", window_end_date="2026-01-01")
        self.assertEqual(form.errors["window_end_date"], ["The end date cannot be before the start date."])
        form = self.form(window_start_month="13")
        self.assertIn("window_start_month", form.errors)
        form = self.form(window_start_time="10:00")
        self.assertEqual(form.errors["window_end_time"], ["Enter both times, or neither."])
