"""Task lists for JARVIS, backed by BOTH macOS Reminders and a local SQLite
table in jarvis_memory.db.

Reminders gives native OS tasks (and notifications); the SQLite ``tasks`` table
is JARVIS's own reliable store that works offline and powers overdue checks and
the proactive engine. Writes go to both stores; reads prefer SQLite.
"""

import sqlite3
import time

import calendar_module
import memory
import reminders_module

DEFAULT_LIST = "JARVIS Tasks"
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def init_tasks_table() -> None:
    """Create the tasks table if it doesn't exist. Idempotent."""
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                title TEXT,
                priority TEXT DEFAULT 'medium',
                due_date TEXT,
                created_at REAL,
                completed_at REAL,
                tags TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    init_tasks_table()
    conn = sqlite3.connect(memory.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_list(list_name: str):
    """Make sure a Reminders list exists (best-effort)."""
    safe = reminders_module._esc(list_name)
    script = f'''
    tell application "Reminders"
        if not (exists list "{safe}") then
            make new list with properties {{name:"{safe}"}}
        end if
    end tell
    return "ok"
    '''
    reminders_module._run_applescript(script)


def add_task(
    title: str,
    due_date: str = None,
    priority: str = "medium",
    list_name: str = DEFAULT_LIST,
):
    """Add a task to the JARVIS Tasks Reminders list and the SQLite store."""
    if not title or not title.strip():
        return "I need a title for the task."
    priority = (priority or "medium").lower()
    if priority not in _PRIORITY_RANK:
        priority = "medium"

    # Local SQLite record (reliable, offline-capable).
    due_iso = None
    if due_date:
        dt = calendar_module._parse_datetime(due_date, "")
        if dt is not None:
            due_iso = dt.isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO tasks (title, priority, due_date, created_at, "
            "completed_at, tags) VALUES (?, ?, ?, ?, NULL, NULL)",
            (title.strip(), priority, due_iso, time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    # Native Reminders record (best-effort; may be unavailable off-macOS).
    _ensure_list(list_name)
    reminders_module.create_reminder(
        title, due_date=due_date, priority=priority, list_name=list_name
    )

    return f'Added task "{title}" ({priority} priority).'


def get_tasks(filter: str = "pending", list_name: str = DEFAULT_LIST):
    """Return tasks (from the Reminders list) as structured dicts.

    ``filter`` is 'pending' (default), 'completed', or 'all'.
    """
    include_completed = filter in ("completed", "all")
    items = reminders_module.get_reminders(
        list_name=list_name, include_completed=include_completed
    )
    if isinstance(items, str):  # error message
        return items
    if filter == "completed":
        items = [t for t in items if t.get("completed")]
    return items


def complete_task(title: str):
    """Mark a task done in both Reminders and the SQLite store."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE tasks SET completed_at = ? WHERE title LIKE ? "
            "AND completed_at IS NULL",
            (time.time(), f"%{title}%"),
        )
        conn.commit()
    finally:
        conn.close()
    reminders_module.complete_reminder(title)
    return f'Marked task "{title}" as done.'


def delete_task(title: str):
    """Delete a task from both Reminders and the SQLite store."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{title}%",))
        conn.commit()
    finally:
        conn.close()
    reminders_module.delete_reminder(title)
    return f'Deleted task "{title}".'


def get_all_tasks():
    """Return pending SQLite tasks, sorted by priority then due date."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE completed_at IS NULL"
        ).fetchall()
    finally:
        conn.close()
    tasks = [dict(r) for r in rows]
    tasks.sort(
        key=lambda t: (
            _PRIORITY_RANK.get(t.get("priority", "medium"), 1),
            t.get("due_date") or "9999",
        )
    )
    return tasks


def get_overdue_tasks():
    """Return pending tasks whose due date is in the past."""
    now_iso = __import__("datetime").datetime.now().isoformat()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE completed_at IS NULL "
            "AND due_date IS NOT NULL AND due_date < ?",
            (now_iso,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def format_tasks(tasks) -> str:
    """Format a task list into a natural spoken sentence."""
    if isinstance(tasks, str):
        return tasks
    if not tasks:
        return "You have no pending tasks."
    parts = []
    for t in tasks:
        pri = t.get("priority", "medium")
        bit = f"[{pri}] {t.get('title', 'untitled')}"
        due = t.get("due_date")
        if due:
            bit += f" (due {str(due)[:10]})"
        parts.append(bit)
    noun = "task" if len(parts) == 1 else "tasks"
    return f"You have {len(parts)} {noun}: " + ", ".join(parts) + "."
