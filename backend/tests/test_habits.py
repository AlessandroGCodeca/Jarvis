"""Tests for habit tracking (SQLite tables inside jarvis_memory.db).

Streaks and per-period counts are date arithmetic over logged timestamps, so
the tests insert logs at controlled days-ago offsets rather than relying on
wall-clock timing.
"""

import datetime
import sqlite3
import time

import pytest

import habits_module
import memory


def _log_days_ago(habit_name: str, *offsets: int) -> None:
    """Insert raw habit_logs rows at whole-day offsets from today.

    Uses noon local time so a log can't drift across a day boundary.
    """
    habits_module.init_habits_tables()
    conn = sqlite3.connect(memory.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM habits WHERE name = ?", (habit_name,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO habits (name, created_at, target_frequency) "
                "VALUES (?, ?, 'daily')",
                (habit_name, time.time()),
            )
            habit_id = cur.lastrowid
        else:
            habit_id = row["id"]
        for offset in offsets:
            day = datetime.date.today() - datetime.timedelta(days=offset)
            ts = datetime.datetime.combine(day, datetime.time(12, 0)).timestamp()
            conn.execute(
                "INSERT INTO habit_logs (habit_id, logged_at, notes) "
                "VALUES (?, ?, NULL)",
                (habit_id, ts),
            )
        conn.commit()
    finally:
        conn.close()


def _habit_id(name: str) -> int:
    conn = sqlite3.connect(memory.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT id FROM habits WHERE name = ?", (name,)).fetchone()[
            "id"
        ]
    finally:
        conn.close()


# --- logging ----------------------------------------------------------------


def test_logging_an_unknown_habit_creates_it():
    out = habits_module.log_habit("meditation")
    assert "meditation" in out
    assert "not tracking" not in habits_module.get_habit_stats("meditation")


def test_the_log_confirmation_reports_the_weekly_count():
    habits_module.log_habit("running")
    # Same calendar day, so the second log still reports one day this week.
    assert "1 time this week" in habits_module.log_habit("running")


def test_the_weekly_count_is_singular_on_the_first_day():
    assert "1 time this week" in habits_module.log_habit("yoga")


@pytest.mark.parametrize("name", [None, "", "   "])
def test_logging_with_no_habit_name_asks_for_one(name):
    assert "Which habit" in habits_module.log_habit(name)


def test_logging_twice_on_one_day_still_counts_as_one_day():
    """The count is distinct calendar days, not raw log rows."""
    habits_module.log_habit("pushups")
    habits_module.log_habit("pushups")
    assert "1 day(s) this week" in habits_module.get_habit_stats("pushups", "week")


def test_a_note_can_be_attached_to_a_log():
    habits_module.log_habit("reading", notes="30 pages")
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        assert conn.execute("SELECT notes FROM habit_logs").fetchone()[0] == "30 pages"
    finally:
        conn.close()


# --- fuzzy habit matching ---------------------------------------------------


def test_a_habit_is_matched_case_insensitively():
    habits_module.log_habit("Morning Run")
    assert "Morning Run" in habits_module.get_habit_stats("morning run")


def test_a_habit_is_matched_on_a_substring():
    """Speech recognition rarely repeats a habit name verbatim."""
    habits_module.log_habit("morning run")
    assert "morning run" in habits_module.get_habit_stats("run")


def test_logging_a_substring_does_not_create_a_duplicate_habit():
    habits_module.log_habit("morning run")
    habits_module.log_habit("run")
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        assert conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 1
    finally:
        conn.close()


# --- counts over periods ----------------------------------------------------


def test_the_weekly_count_covers_the_last_seven_days():
    _log_days_ago("gym", 0, 1, 2, 10)  # three within the week, one outside
    assert "3 day(s) this week" in habits_module.get_habit_stats("gym", "week")


def test_the_monthly_count_covers_the_last_thirty_days():
    _log_days_ago("gym", 0, 10, 20, 40)
    assert "3 day(s) this month" in habits_module.get_habit_stats("gym", "month")


def test_the_all_time_count_includes_everything():
    _log_days_ago("gym", 0, 10, 100, 900)
    assert "4 day(s) all time" in habits_module.get_habit_stats("gym", "all time")


def test_an_unknown_period_falls_back_to_a_week():
    _log_days_ago("gym", 0, 1, 30)
    assert "2 day(s)" in habits_module.get_habit_stats("gym", "fortnight")


def test_stats_for_an_untracked_habit_say_so():
    assert "not tracking" in habits_module.get_habit_stats("skydiving")


# --- streaks ----------------------------------------------------------------


def test_consecutive_days_ending_today_form_a_streak():
    _log_days_ago("journal", 0, 1, 2, 3)
    assert "streak 4 day(s)" in habits_module.get_habit_stats("journal")


def test_a_streak_survives_not_having_logged_yet_today():
    """Checking a streak in the morning must not report it as broken."""
    _log_days_ago("journal", 1, 2, 3)
    assert "streak 3 day(s)" in habits_module.get_habit_stats("journal")


def test_a_gap_breaks_the_streak():
    _log_days_ago("journal", 0, 1, 3, 4)  # day 2 missing
    assert "streak 2 day(s)" in habits_module.get_habit_stats("journal")


def test_a_habit_logged_only_long_ago_has_no_current_streak():
    _log_days_ago("journal", 10, 11, 12)
    assert "streak 0 day(s)" in habits_module.get_habit_stats("journal")


def test_duplicate_logs_on_one_day_do_not_inflate_the_streak():
    _log_days_ago("journal", 0, 0, 0, 1)
    assert "streak 2 day(s)" in habits_module.get_habit_stats("journal")


# --- reports ----------------------------------------------------------------


def test_the_weekly_report_with_no_habits():
    assert "not tracking any habits" in habits_module.get_weekly_report()


def test_the_weekly_report_lists_every_habit_out_of_seven():
    _log_days_ago("gym", 0, 1)
    _log_days_ago("reading", 0)
    report = habits_module.get_weekly_report()
    assert "gym 2/7" in report
    assert "reading 1/7" in report


def test_list_habits_matches_the_weekly_report():
    _log_days_ago("gym", 0)
    assert habits_module.list_habits() == habits_module.get_weekly_report()


def test_stats_with_no_habit_name_returns_the_weekly_report():
    _log_days_ago("gym", 0)
    assert habits_module.get_habit_stats() == habits_module.get_weekly_report()


# --- deletion ---------------------------------------------------------------


def test_deleting_a_habit_removes_it_and_its_logs():
    _log_days_ago("gym", 0, 1)
    habit_id = _habit_id("gym")
    assert "Stopped tracking" in habits_module.delete_habit("gym")
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        assert conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM habit_logs WHERE habit_id = ?", (habit_id,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_deleting_an_untracked_habit_says_so():
    assert "not tracking" in habits_module.delete_habit("skydiving")


# --- the evening reminder query ---------------------------------------------


def test_a_daily_habit_not_logged_today_is_reported_as_unlogged():
    _log_days_ago("gym", 1)
    assert "gym" in habits_module.get_unlogged_daily_habits()


def test_a_daily_habit_logged_today_is_not_reported():
    _log_days_ago("gym", 0)
    assert habits_module.get_unlogged_daily_habits() == []


def test_unlogged_habits_on_an_empty_store():
    assert habits_module.get_unlogged_daily_habits() == []


def test_init_habits_tables_is_idempotent():
    habits_module.init_habits_tables()
    habits_module.init_habits_tables()
    assert habits_module.log_habit("gym")
