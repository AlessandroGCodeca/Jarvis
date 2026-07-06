"""Habit tracking for JARVIS, backed by SQLite tables in jarvis_memory.db.

Log habit completions (auto-creating the habit on first mention), then query
counts and streaks over day/week/month/all-time windows. Used by the proactive
engine for gentle evening reminders about un-logged daily habits.
"""

import datetime
import sqlite3
import time

import memory

_PERIOD_DAYS = {"today": 1, "day": 1, "week": 7, "month": 30}


def init_habits_tables() -> None:
    """Create the habits + habit_logs tables if they don't exist. Idempotent."""
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at REAL,
                target_frequency TEXT DEFAULT 'daily'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY,
                habit_id INTEGER,
                logged_at REAL,
                notes TEXT,
                FOREIGN KEY (habit_id) REFERENCES habits(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    init_habits_tables()
    conn = sqlite3.connect(memory.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _find_habit(conn, name: str):
    """Fuzzy-match a habit row by name (exact, then substring). Returns row|None."""
    key = (name or "").strip().lower()
    if not key:
        return None
    rows = conn.execute("SELECT * FROM habits").fetchall()
    for r in rows:
        if r["name"].lower() == key:
            return r
    for r in rows:
        if key in r["name"].lower() or r["name"].lower() in key:
            return r
    return None


def _count_in_period(conn, habit_id: int, period: str) -> int:
    """Distinct days (or rows for 'today') logged within ``period``."""
    if period == "all time":
        rows = conn.execute(
            "SELECT logged_at FROM habit_logs WHERE habit_id = ?", (habit_id,)
        ).fetchall()
    else:
        days = _PERIOD_DAYS.get(period, 7)
        since = time.time() - days * 86400
        rows = conn.execute(
            "SELECT logged_at FROM habit_logs WHERE habit_id = ? AND logged_at >= ?",
            (habit_id, since),
        ).fetchall()
    # Count distinct calendar days.
    days_set = {
        datetime.date.fromtimestamp(r["logged_at"]).isoformat() for r in rows
    }
    return len(days_set)


def _streak(conn, habit_id: int) -> int:
    """Consecutive-day streak ending today (or yesterday if not yet logged)."""
    rows = conn.execute(
        "SELECT logged_at FROM habit_logs WHERE habit_id = ?", (habit_id,)
    ).fetchall()
    days = {datetime.date.fromtimestamp(r["logged_at"]) for r in rows}
    if not days:
        return 0
    today = datetime.date.today()
    cursor = today if today in days else today - datetime.timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak


def log_habit(habit_name: str, notes: str = None):
    """Log a completion for ``habit_name`` (creating the habit if new)."""
    if not habit_name or not habit_name.strip():
        return "Which habit should I log?"
    conn = _connect()
    try:
        habit = _find_habit(conn, habit_name)
        if habit is None:
            cur = conn.execute(
                "INSERT INTO habits (name, created_at) VALUES (?, ?)",
                (habit_name.strip(), time.time()),
            )
            conn.commit()
            habit_id = cur.lastrowid
            display = habit_name.strip()
        else:
            habit_id = habit["id"]
            display = habit["name"]
        conn.execute(
            "INSERT INTO habit_logs (habit_id, logged_at, notes) VALUES (?, ?, ?)",
            (habit_id, time.time(), notes),
        )
        conn.commit()
        week = _count_in_period(conn, habit_id, "week")
    finally:
        conn.close()
    times = "time" if week == 1 else "times"
    return f"Logged: {display}. You've done this {week} {times} this week."


def get_habit_stats(habit_name: str = None, period: str = "week"):
    """Return completion count + streak for a habit over ``period``."""
    period = (period or "week").lower()
    conn = _connect()
    try:
        if not habit_name:
            return get_weekly_report()
        habit = _find_habit(conn, habit_name)
        if habit is None:
            return f"I'm not tracking a habit called '{habit_name}' yet."
        count = _count_in_period(conn, habit["id"], period)
        streak = _streak(conn, habit["id"])
    finally:
        conn.close()
    label = period if period == "all time" else f"this {period}"
    return (
        f"{habit['name']}: {count} day(s) {label}, current streak {streak} day(s)."
    )


def get_weekly_report():
    """Summarize all habits' completions and streaks for the past week."""
    conn = _connect()
    try:
        habits = conn.execute("SELECT * FROM habits").fetchall()
        if not habits:
            return "You're not tracking any habits yet."
        parts = []
        for h in habits:
            count = _count_in_period(conn, h["id"], "week")
            streak = _streak(conn, h["id"])
            parts.append(f"{h['name']} {count}/7 days (streak: {streak})")
    finally:
        conn.close()
    return "This week: " + ", ".join(parts) + "."


def list_habits():
    """List every tracked habit with its weekly count and streak."""
    return get_weekly_report()


def delete_habit(name: str):
    """Delete a habit and all of its logs."""
    conn = _connect()
    try:
        habit = _find_habit(conn, name)
        if habit is None:
            return f"I'm not tracking a habit called '{name}'."
        conn.execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit["id"],))
        conn.execute("DELETE FROM habits WHERE id = ?", (habit["id"],))
        conn.commit()
        display = habit["name"]
    finally:
        conn.close()
    return f"Stopped tracking '{display}' and removed its history."


def get_unlogged_daily_habits():
    """Return names of daily habits with no log today (for evening reminders)."""
    conn = _connect()
    try:
        habits = conn.execute(
            "SELECT * FROM habits WHERE target_frequency = 'daily'"
        ).fetchall()
        start_of_day = datetime.datetime.combine(
            datetime.date.today(), datetime.time.min
        ).timestamp()
        unlogged = []
        for h in habits:
            row = conn.execute(
                "SELECT 1 FROM habit_logs WHERE habit_id = ? AND logged_at >= ? "
                "LIMIT 1",
                (h["id"], start_of_day),
            ).fetchone()
            if row is None:
                unlogged.append(h["name"])
    finally:
        conn.close()
    return unlogged
