"""macOS Reminders bridge via AppleScript (osascript).

Create / list / complete / delete reminders, plus a due-soon query used by the
proactive notification engine. Every call degrades gracefully off-macOS or when
automation permission is denied. Natural date/time strings are parsed with the
shared helper in :mod:`calendar_module`.
"""

import subprocess

import calendar_module

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
    if due_date or due_time:
        dt = calendar_module._parse_datetime(due_date or "", due_time or "")
        if dt is not None:
            due_block = calendar_module._as_set_date("dueDate", dt)
            props.append("due date:dueDate")
            when_label = f" for {dt.strftime('%A, %B %d at %-I:%M %p')}"

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
