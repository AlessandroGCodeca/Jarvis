"""Tests for the task store.

``add_task`` writes to both macOS Reminders and SQLite; the autouse
``_no_applescript`` guard makes the Reminders half degrade exactly as it does
off-macOS, so these tests cover the SQLite store and the formatting that the
user actually hears.
"""

import datetime
import sqlite3

import pytest

import memory
import tasks_module


def _rows():
    # add_task can return before touching SQLite (e.g. an empty title), so make
    # sure the table exists before reading it.
    tasks_module.init_tasks_table()
    conn = sqlite3.connect(memory.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
    finally:
        conn.close()


# --- adding -----------------------------------------------------------------


def test_adding_a_task_stores_it_locally():
    out = tasks_module.add_task("Buy milk")
    assert "Buy milk" in out
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["title"] == "Buy milk"
    assert rows[0]["completed_at"] is None


def test_the_default_priority_is_medium():
    tasks_module.add_task("Buy milk")
    assert _rows()[0]["priority"] == "medium"


@pytest.mark.parametrize("priority", ["high", "medium", "low"])
def test_valid_priorities_are_kept(priority):
    tasks_module.add_task("Task", priority=priority)
    assert _rows()[0]["priority"] == priority


def test_priority_is_normalised_to_lowercase():
    tasks_module.add_task("Task", priority="HIGH")
    assert _rows()[0]["priority"] == "high"


@pytest.mark.parametrize("priority", ["urgent", "", None, "9"])
def test_an_unrecognised_priority_falls_back_to_medium(priority):
    tasks_module.add_task("Task", priority=priority)
    assert _rows()[0]["priority"] == "medium"


@pytest.mark.parametrize("title", [None, "", "   "])
def test_a_task_with_no_title_is_refused(title):
    assert "need a title" in tasks_module.add_task(title)
    assert _rows() == []


def test_a_title_is_stored_trimmed():
    tasks_module.add_task("  Buy milk  ")
    assert _rows()[0]["title"] == "Buy milk"


def test_a_natural_language_due_date_is_stored_as_iso(frozen_now):
    tasks_module.add_task("Call the dentist", due_date="tomorrow at 3pm")
    due = _rows()[0]["due_date"]
    assert due.startswith("2026-06-11T15:00")


def test_a_task_with_no_due_date_stores_null():
    tasks_module.add_task("Someday task")
    assert _rows()[0]["due_date"] is None


def test_an_unparseable_due_date_stores_null_rather_than_failing(frozen_now):
    tasks_module.add_task("Vague task", due_date="whenever")
    assert _rows()[0]["due_date"] is None


# --- reading ----------------------------------------------------------------


def test_pending_tasks_are_sorted_by_priority():
    tasks_module.add_task("Low one", priority="low")
    tasks_module.add_task("High one", priority="high")
    tasks_module.add_task("Medium one", priority="medium")
    assert [t["title"] for t in tasks_module.get_all_tasks()] == [
        "High one",
        "Medium one",
        "Low one",
    ]


def test_tasks_of_equal_priority_are_sorted_by_due_date(frozen_now):
    tasks_module.add_task("Later", priority="high", due_date="2026-06-20")
    tasks_module.add_task("Sooner", priority="high", due_date="2026-06-12")
    assert [t["title"] for t in tasks_module.get_all_tasks()] == ["Sooner", "Later"]


def test_a_task_without_a_due_date_sorts_after_dated_ones(frozen_now):
    tasks_module.add_task("Undated", priority="high")
    tasks_module.add_task("Dated", priority="high", due_date="2026-06-12")
    assert [t["title"] for t in tasks_module.get_all_tasks()] == ["Dated", "Undated"]


def test_completed_tasks_are_excluded_from_the_pending_list():
    tasks_module.add_task("Done thing")
    tasks_module.complete_task("Done thing")
    assert tasks_module.get_all_tasks() == []


def test_get_all_tasks_on_an_empty_store():
    assert tasks_module.get_all_tasks() == []


# --- completing and deleting ------------------------------------------------


def test_completing_a_task_stamps_it():
    tasks_module.add_task("Buy milk")
    assert "done" in tasks_module.complete_task("Buy milk")
    assert _rows()[0]["completed_at"] is not None


def test_a_task_can_be_completed_by_a_partial_title():
    """The user rarely repeats the full title back."""
    tasks_module.add_task("Buy milk and eggs")
    tasks_module.complete_task("milk")
    assert _rows()[0]["completed_at"] is not None


def test_completing_an_already_completed_task_does_not_restamp_it():
    tasks_module.add_task("Buy milk")
    tasks_module.complete_task("Buy milk")
    first = _rows()[0]["completed_at"]
    tasks_module.complete_task("Buy milk")
    assert _rows()[0]["completed_at"] == first


def test_deleting_a_task_removes_it():
    tasks_module.add_task("Buy milk")
    assert "Deleted" in tasks_module.delete_task("Buy milk")
    assert _rows() == []


# --- overdue ----------------------------------------------------------------


def test_a_past_due_task_is_overdue(frozen_now):
    tasks_module.add_task("Overdue", due_date="2020-01-01")
    assert [t["title"] for t in tasks_module.get_overdue_tasks()] == ["Overdue"]


def test_a_future_task_is_not_overdue():
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    tasks_module.add_task("Future", due_date=future)
    assert tasks_module.get_overdue_tasks() == []


def test_an_undated_task_is_never_overdue():
    tasks_module.add_task("Undated")
    assert tasks_module.get_overdue_tasks() == []


def test_a_completed_task_is_not_reported_overdue(frozen_now):
    tasks_module.add_task("Overdue", due_date="2020-01-01")
    tasks_module.complete_task("Overdue")
    assert tasks_module.get_overdue_tasks() == []


# --- spoken formatting ------------------------------------------------------


def test_formatting_an_empty_task_list():
    assert tasks_module.format_tasks([]) == "You have no pending tasks."


def test_a_single_task_is_phrased_in_the_singular():
    out = tasks_module.format_tasks([{"title": "Buy milk", "priority": "high"}])
    assert out.startswith("You have 1 task:")
    assert "[high] Buy milk" in out


def test_several_tasks_are_phrased_in_the_plural():
    out = tasks_module.format_tasks(
        [{"title": "A", "priority": "high"}, {"title": "B", "priority": "low"}]
    )
    assert out.startswith("You have 2 tasks:")


def test_a_due_date_is_spoken_as_a_plain_day():
    out = tasks_module.format_tasks(
        [{"title": "A", "priority": "high", "due_date": "2026-06-11T15:00:00"}]
    )
    assert "(due 2026-06-11)" in out


def test_an_error_string_is_passed_through_untouched():
    """Reminders errors reach the user verbatim instead of being formatted."""
    assert tasks_module.format_tasks("Reminders is unavailable.") == (
        "Reminders is unavailable."
    )


def test_a_task_missing_its_fields_still_formats():
    assert "untitled" in tasks_module.format_tasks([{}])


def test_init_tasks_table_is_idempotent():
    tasks_module.init_tasks_table()
    tasks_module.init_tasks_table()
    assert "Buy milk" in tasks_module.add_task("Buy milk")
