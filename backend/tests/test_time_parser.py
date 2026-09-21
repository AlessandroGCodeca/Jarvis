"""Tests for the shared natural-language date/time parser.

Both the Calendar and Reminders bridges route through ``parse_datetime``, so a
regression here silently schedules things at the wrong time. Every test pins
"now" to Wednesday 2026-06-10 12:00 via the ``frozen_now`` fixture.
"""

import datetime

import pytest

import time_parser

NOW = datetime.datetime(2026, 6, 10, 12, 0)


def test_the_reference_day_is_a_wednesday():
    """Guards the weekday assertions below against a bad reference date."""
    assert NOW.weekday() == 2


# --- relative offsets -------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("in 30 minutes", NOW + datetime.timedelta(minutes=30)),
        ("in 45 min", NOW + datetime.timedelta(minutes=45)),
        ("in 1 minute", NOW + datetime.timedelta(minutes=1)),
        ("in 2 hours", NOW + datetime.timedelta(hours=2)),
        ("in 1 hr", NOW + datetime.timedelta(hours=1)),
        ("remind me in 90 minutes to stretch", NOW + datetime.timedelta(minutes=90)),
    ],
)
def test_relative_offsets_are_measured_from_now(frozen_now, text, expected):
    assert time_parser.parse_datetime(text) == expected


def test_relative_offset_wins_over_a_date_word(frozen_now):
    """"in X" is documented to win outright, even alongside "tomorrow"."""
    assert time_parser.parse_datetime("tomorrow in 2 hours") == NOW + datetime.timedelta(
        hours=2
    )


# --- date words + default times --------------------------------------------


def test_today_with_no_time_defaults_to_9am(frozen_now):
    assert time_parser.parse_datetime("today") == NOW.replace(hour=9, minute=0)


def test_tomorrow_with_no_time_defaults_to_9am(frozen_now):
    assert time_parser.parse_datetime("tomorrow") == datetime.datetime(
        2026, 6, 11, 9, 0
    )


def test_tonight_defaults_to_8pm(frozen_now):
    assert time_parser.parse_datetime("tonight") == NOW.replace(hour=20, minute=0)


def test_tonight_still_honours_an_explicit_time(frozen_now):
    assert time_parser.parse_datetime("tonight at 11pm") == NOW.replace(
        hour=23, minute=0
    )


# --- the bare-hour meridiem heuristic ---------------------------------------


@pytest.mark.parametrize(
    "text,hour",
    [
        ("today at 1", 13),   # 1-6 -> afternoon
        ("today at 3", 15),
        ("today at 6", 18),
        ("today at 7", 7),    # 7-11 -> morning
        ("today at 9", 9),
        ("today at 11", 11),
        ("today at 12", 12),  # noon
        ("today at 13", 13),  # already 24h
        ("today at 22", 22),
    ],
)
def test_bare_hour_heuristic(frozen_now, text, hour):
    assert time_parser.parse_datetime(text).hour == hour


@pytest.mark.parametrize(
    "text,hour",
    [
        ("today at 9am", 9),
        ("today at 9pm", 21),
        ("today at 3 p.m.", 15),
        ("today at 12am", 0),   # midnight
        ("today at 12pm", 12),  # noon
        ("today at 5 a.m.", 5),
    ],
)
def test_explicit_meridiem_overrides_the_heuristic(frozen_now, text, hour):
    """An explicit am/pm must win — "at 5 a.m." is 05:00, not the 17:00 the
    bare-hour heuristic would pick."""
    assert time_parser.parse_datetime(text).hour == hour


# --- hour:minute forms ------------------------------------------------------


@pytest.mark.parametrize(
    "text,hour,minute",
    [
        ("tomorrow at 3:30", 15, 30),
        ("today at 15:00", 15, 0),
        ("today at 9:15pm", 21, 15),
        ("today at 08:45", 8, 45),
        ("9:00", 9, 0),  # bare time, no date word -> today
    ],
)
def test_hour_minute_forms(frozen_now, text, hour, minute):
    got = time_parser.parse_datetime(text)
    assert (got.hour, got.minute) == (hour, minute)


def test_bare_time_with_no_date_word_means_today(frozen_now):
    assert time_parser.parse_datetime("9:00").date() == NOW.date()


def test_bare_trailing_hour_after_a_date_word_is_a_time(frozen_now):
    """"tomorrow 9" has no "at", but the trailing number is still the hour."""
    assert time_parser.parse_datetime("tomorrow 9") == datetime.datetime(
        2026, 6, 11, 9, 0
    )


# --- weekdays ---------------------------------------------------------------


def test_weekday_resolves_to_the_next_occurrence(frozen_now):
    """From Wednesday, "Friday" is two days out."""
    assert time_parser.parse_datetime("Friday at 6pm") == datetime.datetime(
        2026, 6, 12, 18, 0
    )


def test_next_weekday_is_treated_the_same_as_the_bare_weekday(frozen_now):
    assert time_parser.parse_datetime("next Friday at 6pm") == time_parser.parse_datetime(
        "Friday at 6pm"
    )


def test_todays_weekday_jumps_a_full_week(frozen_now):
    """On a Wednesday, "Wednesday" means the *coming* Wednesday, not today —
    otherwise a reminder set in the afternoon lands in the past."""
    assert time_parser.parse_datetime("Wednesday at 10am") == datetime.datetime(
        2026, 6, 17, 10, 0
    )


def test_weekday_with_no_time_defaults_to_9am(frozen_now):
    assert time_parser.parse_datetime("Monday") == datetime.datetime(2026, 6, 15, 9, 0)


# --- absolute dates ---------------------------------------------------------


def test_absolute_iso_date(frozen_now):
    got = time_parser.parse_datetime("2026-06-20")
    assert (got.year, got.month, got.day) == (2026, 6, 20)


def test_absolute_date_defaults_to_9am(frozen_now):
    assert time_parser.parse_datetime("2026-06-20").hour == 9


def test_absolute_date_with_a_time(frozen_now):
    got = time_parser.parse_datetime("2026-06-20 at 14:30")
    assert got == datetime.datetime(2026, 6, 20, 14, 30)


def test_day_first_convention_for_dotted_dates(frozen_now):
    """Prague convention: 10.6. is 10 June, not 6 October."""
    got = time_parser.parse_datetime("10.6.2026")
    assert (got.month, got.day) == (6, 10)


def test_day_first_convention_for_slashed_dates(frozen_now):
    got = time_parser.parse_datetime("10/6/2026")
    assert (got.month, got.day) == (6, 10)


@pytest.mark.parametrize(
    "text,month,day",
    [
        ("2026-06-12", 6, 12),  # both parts <= 12: genuinely ambiguous
        ("2026-01-02", 1, 2),
        ("2026-12-01", 12, 1),
        ("2026-1-2", 1, 2),
        ("2026-06-20", 6, 20),  # day > 12, so never ambiguous
    ],
)
def test_iso_dates_are_always_year_month_day(frozen_now, text, month, day):
    """ISO-8601 is unambiguous and must not be read day-first — otherwise
    "2026-06-12" schedules on 6 December."""
    got = time_parser.parse_datetime(text)
    assert (got.month, got.day) == (month, day)


def test_an_iso_date_keeps_an_explicit_time(frozen_now):
    assert time_parser.parse_datetime("2026-06-12 at 14:30") == datetime.datetime(
        2026, 6, 12, 14, 30
    )


# --- no-op inputs -----------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   ", "sometime soon", "!!!"])
def test_unparseable_input_returns_none(frozen_now, text):
    assert time_parser.parse_datetime(text) is None


def test_results_never_carry_stray_seconds(frozen_now):
    """Seconds/microseconds must be zeroed or AppleScript dates drift."""
    for text in ["in 30 minutes", "today at 9", "tomorrow", "Friday at 6pm"]:
        got = time_parser.parse_datetime(text)
        assert (got.second, got.microsecond) == (0, 0), text
