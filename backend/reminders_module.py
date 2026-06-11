"""macOS Reminders bridge via AppleScript (osascript).

Create / list / complete / delete reminders, plus a due-soon query used by the
proactive notification engine. Every call degrades gracefully off-macOS or when
automation permission is denied. Natural date/time strings are parsed with the
shared :mod:`time_parser` (Europe/Prague), same as the Calendar bridge.
"""

import subprocess

import calendar_module
import time_parser

try:  # for parsing AppleScript date strings in the duplicate check
    from dateutil import parser as _du_parser
except Exception:  # noqa: BLE001
    _du_parser = None

# Task spec priority mapping (macOS Reminders priority field).
_PRIORITY = {"low": 0, "medium": 1, "high": 9}


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _run_applescript(script: str):
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if result.returncode != 0:
            return None, (result.stderr or "AppleScript error").strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "osascript not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "Reminders request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def _find_duplicate(title: str, dt):
    """An existing pending reminder that would duplicate this one, or None.

    A duplicate is a same-titled pending reminder due within 24 hours of the
    new one (or one with no readable due date — better to ask than double up).
    """
    existing = get_reminders()
    if not isinstance(existing, list):
        return None  # Reminders unavailable — let creation proceed/fail there
    key = title.strip().lower()
    for r in existing:
        if (r.get("title") or "").strip().lower() != key:
            continue
        due_str = (r.get("due") or "").strip()
        if dt is None or not due_str or _du_parser is None:
            return r
        try:
            existing_dt = _du_parser.parse(due_str, fuzzy=True)
        except (ValueError, OverflowError):
            return r
        if abs((existing_dt - dt).total_seconds()) <= 24 * 3600:
            return r
    return None


def create_reminder(
    title: str,
    due_date: str = None,
    due_time: str = None,
    notes: str = None,
    priority: str = "medium",
    list_name: str = None,
):
    """Create a reminder, optionally with a due date/time, notes and priority."""
    if not title or not title.strip():
        return "I need a title for the reminder."
    pri = _PRIORITY.get((priority or "medium").lower(), 1)

    props = [f'name:"{_esc(title)}"', f"priority:{pri}"]
    if notes:
        props.append(f'body:"{_esc(notes)}"')

    due_block = ""
    when_label = ""
    dt = None
    if due_date or due_time:
        combined = f"{due_date or ''} {due_time or ''}".strip()
        dt = time_parser.parse_datetime(combined)
        if dt is not None:
            due_block = calendar_module._as_set_date("dueDate", dt)
            props.append("due date:dueDate")
            when_label = f" for {dt.strftime('%A, %B %d at %-I:%M %p')}"

    # Never create duplicates: same title due within 24h of the new one.
    duplicate = _find_duplicate(title, dt)
    if duplicate:
        due_str = (duplicate.get("due") or "").strip()
        if due_str:
            return (
                f"You already have that reminder set for {due_str}. "
                "Want me to update it instead?"
            )
        return (
            f'You already have a reminder called "{title}". '
            "Want me to update it instead?"
        )

    target = f'list "{_esc(list_name)}"' if list_name else "default list"
    script = f'''
    tell application "Reminders"
        {due_block}
        tell {target}
            make new reminder with properties {{{", ".join(props)}}}
        end tell
    end tell
    return "created"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not create reminder: {err}"
    return f'Reminder set: "{title}"{when_label}.'


def get_reminders(list_name: str = None, include_completed: bool = False):
    """Return reminders as a list of dicts {title, due, completed}."""
    completed_filter = "" if include_completed else " whose completed is false"
    source = f'reminders of list "{_esc(list_name)}"' if list_name else "reminders"
    script = f'''
    set output to ""
    tell application "Reminders"
        repeat with r in ({source}{completed_filter})
            set dueStr to ""
            try
                if due date of r is not missing value then set dueStr to (due date of r) as string
            end try
            set isDone to "0"
            if completed of r then set isDone to "1"
            set output to output & (name of r) & "|::|" & dueStr & "|::|" & isDone & linefeed
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Reminders unavailable: {err}"
    if not out:
        return []
    items = []
    for line in out.splitlines():
        parts = line.split("|::|")
        if parts and parts[0].strip():
            items.append(
                {
                    "title": parts[0].strip(),
                    "due": parts[1].strip() if len(parts) > 1 else "",
                    "completed": len(parts) > 2 and parts[2].strip() == "1",
                }
            )
    return items


def complete_reminder(title: str):
    """Mark the first matching pending reminder as completed."""
    safe = _esc(title)
    script = f'''
    set done to false
    tell application "Reminders"
        set matches to (reminders whose name contains "{safe}" and completed is false)
        if (count of matches) > 0 then
            set completed of (item 1 of matches) to true
            set done to true
        end if
    end tell
    if done then
        return "done"
    else
        return "notfound"
    end if
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not update reminder: {err}"
    if out == "notfound":
        return f'I couldn\'t find a pending reminder matching "{title}".'
    return f'Marked "{title}" as done.'


def delete_reminder(title: str):
    """Delete the first reminder whose name matches ``title``."""
    safe = _esc(title)
    script = f'''
    set deletedName to ""
    tell application "Reminders"
        set matches to (reminders whose name contains "{safe}")
        if (count of matches) > 0 then
            set deletedName to name of (item 1 of matches)
            delete (item 1 of matches)
        end if
    end tell
    return deletedName
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not delete reminder: {err}"
    if not out:
        return f'I couldn\'t find a reminder matching "{title}".'
    return f'Deleted reminder "{out}".'


def get_reminder_lists():
    """Return the names of all Reminders lists."""
    script = '''
    set output to ""
    tell application "Reminders"
        repeat with l in lists
            set output to output & (name of l) & linefeed
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Reminders unavailable: {err}"
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_due_within(minutes: int = 10):
    """Return pending reminders due within ``minutes`` (for notifications).

    Each item is {title, minutes_until}. Returns [] on any failure.
    """
    try:
        within = max(1, int(minutes))
    except (TypeError, ValueError):
        within = 10
    script = f'''
    set output to ""
    set endDate to (current date) + ({within} * minutes)
    tell application "Reminders"
        set matches to (reminders whose completed is false and due date is not missing value)
        repeat with r in matches
            set d to due date of r
            if d is greater than or equal to (current date) and d is less than or equal to endDate then
                set mins to ((d - (current date)) / 60) as integer
                set output to output & (name of r) & "|::|" & mins & linefeed
            end if
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err or not out:
        return []
    items = []
    for line in out.splitlines():
        parts = line.split("|::|")
        if parts and parts[0].strip():
            try:
                mins = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                mins = 0
            items.append({"title": parts[0].strip(), "minutes_until": mins})
    return items
