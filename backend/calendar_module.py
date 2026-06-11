"""macOS Calendar bridge via AppleScript (osascript).

Every call is wrapped so a missing Calendar app, denied automation
permission, or a non-macOS host degrades gracefully instead of raising.
"""

import datetime
import os
import subprocess

try:  # dateutil is a declared dependency; degrade gracefully if missing.
    from dateutil import parser as _du_parser
except Exception:  # noqa: BLE001
    _du_parser = None

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Calendars to skip when reading events. These are the auto-generated /
# subscribed calendars (holidays, birthdays, Siri Suggestions) that hold
# hundreds of recurring all-day events — they're noise for a personal agenda
# and what makes the date query slow. Matching is case/diacritical-insensitive
# substring, so "svatky" also matches "České svátky". Override with the
# CALENDAR_SKIP env var (comma-separated names; empty = show everything).
DEFAULT_SKIP = ["birthday", "siri", "holiday", "sviatky", "svatky"]


def _esc(s: str) -> str:
    """Escape a string for safe interpolation into an AppleScript literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _skip_patterns():
    env = os.getenv("CALENDAR_SKIP")
    if env is None:
        return DEFAULT_SKIP
    return [p.strip() for p in env.split(",") if p.strip()]


def _run_applescript(script: str):
    """Run an AppleScript snippet. Returns (stdout, error_message)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=35,
        )
        if result.returncode != 0:
            return None, (result.stderr or "AppleScript error").strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "osascript not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "Calendar request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def _events_in_range(days_ahead: int):
    """Return a list of event strings between now and ``days_ahead`` days out.

    Every calendar is queried except those whose name matches the skip list
    (holidays / birthdays / Siri Suggestions by default), so all of the user's
    own events show up regardless of whether the calendar is writable, while
    the slow auto-generated calendars are skipped for speed.
    """
    patterns = _skip_patterns()

    # AppleScript list literal; empty list ({}) means "skip nothing".
    skip_literal = ", ".join(f'"{_esc(p)}"' for p in patterns)

    script = f'''
    set skipList to {{{skip_literal}}}
    set output to ""
    set startDate to current date
    set endDate to (current date) + ({days_ahead} * days)
    with timeout of 30 seconds
        tell application "Calendar"
            repeat with cal in calendars
                set cname to name of cal
                set doSkip to false
                repeat with pat in skipList
                    ignoring case and diacriticals
                        if cname contains pat then set doSkip to true
                    end ignoring
                end repeat
                if doSkip is false then
                    set theEvents to (every event of cal whose start date is greater than or equal to startDate and start date is less than or equal to endDate)
                    repeat with ev in theEvents
                        set evTitle to summary of ev
                        set evStart to start date of ev
                        set output to output & evTitle & " @ " & (evStart as string) & linefeed
                    end repeat
                end if
            end repeat
        end tell
    end timeout
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return [f"(Calendar unavailable: {err})"]
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def get_today_events():
    """Events scheduled within the next 24 hours."""
    return _events_in_range(1)


def get_week_events():
    """Events scheduled within the next 7 days."""
    return _events_in_range(7)


def _parse_datetime(date_str: str, time_str: str = ""):
    """Parse flexible natural date/time strings into a datetime, or None.

    Handles relative words ("tomorrow", "tonight", "next Monday") plus absolute
    dates/times via dateutil ("June 10 2026 3pm", "2026-06-10 15:00", "3pm").
    """
    combined = f"{date_str or ''} {time_str or ''}".strip()
    if not combined:
        return None
    low = combined.lower()
    now = datetime.datetime.now()

    base = None
    if "tomorrow" in low:
        base = now + datetime.timedelta(days=1)
    elif "today" in low or "tonight" in low:
        base = now
    else:
        for name, idx in _WEEKDAYS.items():
            if name in low:
                # Next upcoming occurrence; if it's that weekday today, jump a
                # week (so "Monday"/"next Monday" both mean the coming Monday).
                ahead = (idx - now.weekday()) % 7
                if ahead == 0:
                    ahead = 7
                base = now + datetime.timedelta(days=ahead)
                break

    # Strip relative words so dateutil can parse just the time-of-day.
    cleaned = low
    for w in ["tomorrow", "today", "tonight", "next", *_WEEKDAYS]:
        cleaned = cleaned.replace(w, "")
    cleaned = cleaned.replace(" at ", " ").strip()

    parsed_time = None
    if _du_parser and cleaned:
        try:
            # Zero out minutes/seconds so "3pm" -> 15:00, not the current minute.
            time_default = now.replace(minute=0, second=0, microsecond=0)
            parsed_time = _du_parser.parse(
                cleaned, fuzzy=True, default=time_default
            )
        except (ValueError, OverflowError):
            parsed_time = None

    if base is not None:
        hour = parsed_time.hour if parsed_time else (20 if "tonight" in low else 9)
        minute = parsed_time.minute if parsed_time else 0
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if _du_parser:
        try:
            default = now.replace(hour=9, minute=0, second=0, microsecond=0)
            return _du_parser.parse(combined, fuzzy=True, default=default)
        except (ValueError, OverflowError):
            return None
    return None


def _as_set_date(var: str, dt: datetime.datetime) -> str:
    """AppleScript that builds a date in ``var`` from explicit components.

    Component assignment avoids locale-sensitive `date "..."` string parsing.
    Day is reset to 1 first to avoid end-of-month overflow when setting month.
    """
    return (
        f"set {var} to (current date)\n"
        f"set day of {var} to 1\n"
        f"set year of {var} to {dt.year}\n"
        f"set month of {var} to {dt.month}\n"
        f"set day of {var} to {dt.day}\n"
        f"set hours of {var} to {dt.hour}\n"
        f"set minutes of {var} to {dt.minute}\n"
        f"set seconds of {var} to 0"
    )


def create_event(
    title: str,
    date: str,
    time: str = "",
    duration_minutes: int = 60,
    calendar: str = None,
    notes: str = None,
):
    """Create a calendar event from flexible natural date/time strings."""
    dt = _parse_datetime(date, time)
    if dt is None:
        return f"I couldn't understand the date/time '{date} {time}'."
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        minutes = 60

    safe_title = _esc(title)
    props = [f'summary:"{safe_title}"', "start date:startDate", "end date:endDate"]
    if notes:
        props.append(f'description:"{_esc(notes)}"')
    props_literal = ", ".join(props)

    target = f'calendar "{_esc(calendar)}"' if calendar else "calendar 1"
    script = f'''
    tell application "Calendar"
        {_as_set_date("startDate", dt)}
        set endDate to startDate + ({minutes} * minutes)
        tell {target}
            make new event with properties {{{props_literal}}}
        end tell
    end tell
    return "created"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not create event: {err}"
    when = dt.strftime("%A, %B %d at %-I:%M %p")
    return f'Created event "{title}" on {when}.'


def update_event(
    title_or_id: str,
    new_title: str = None,
    new_date: str = None,
    new_time: str = None,
    new_notes: str = None,
):
    """Find the first event whose title contains ``title_or_id`` and update it."""
    safe_match = _esc(title_or_id)
    updates = []
    if new_title:
        updates.append(f'set summary of ev to "{_esc(new_title)}"')
    if new_notes:
        updates.append(f'set description of ev to "{_esc(new_notes)}"')
    if new_date or new_time:
        dt = _parse_datetime(new_date or "", new_time or "")
        if dt is None:
            return f"I couldn't understand the new date/time '{new_date} {new_time}'."
        # Preserve the original duration when moving the event.
        updates.append(_as_set_date("newStart", dt))
        updates.append("set dur to (end date of ev) - (start date of ev)")
        updates.append("set start date of ev to newStart")
        updates.append("set end date of ev to (newStart + dur)")

    if not updates:
        return "Nothing to update — give a new title, date, time, or notes."

    update_block = "\n                ".join(updates)
    script = f'''
    set didUpdate to false
    tell application "Calendar"
        repeat with cal in calendars
            set matches to (every event of cal whose summary contains "{safe_match}")
            if (count of matches) > 0 then
                set ev to item 1 of matches
                {update_block}
                set didUpdate to true
                exit repeat
            end if
        end repeat
    end tell
    if didUpdate then
        return "updated"
    else
        return "notfound"
    end if
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not update event: {err}"
    if out == "notfound":
        return f'I couldn\'t find an event matching "{title_or_id}".'
    return f'Updated event "{title_or_id}".'


def delete_event(title: str, date: str = None):
    """Delete an event by title. A ``date`` narrows the match (recommended)."""
    safe_match = _esc(title)
    date_filter = ""
    when_label = ""
    if date:
        dt = _parse_datetime(date, "")
        if dt is not None:
            date_filter = (
                f"\n            {_as_set_date('dayStart', dt.replace(hour=0, minute=0))}"
                "\n            set dayEnd to dayStart + (1 * days)"
            )
            when_label = f" on {dt.strftime('%A, %B %d')}"

    cond = 'summary contains "' + safe_match + '"'
    if date_filter:
        cond += " and start date is greater than or equal to dayStart and start date is less than dayEnd"

    script = f'''
    set deletedTitle to ""
    tell application "Calendar"{date_filter}
        repeat with cal in calendars
            set matches to (every event of cal whose {cond})
            if (count of matches) > 0 then
                set ev to item 1 of matches
                set deletedTitle to summary of ev
                delete ev
                exit repeat
            end if
        end repeat
    end tell
    return deletedTitle
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not delete event: {err}"
    if not out:
        return f'I couldn\'t find an event matching "{title}"{when_label}.'
    return f'Found event "{out}"{when_label} — deleted successfully.'


def get_upcoming_events(within_minutes: int = 10):
    """Return events starting within the next ``within_minutes`` minutes.

    Each item is a dict {title, minutes_until, start}. Used by the proactive
    notification engine. Returns [] on any failure (never raises).
    """
    try:
        within = max(1, int(within_minutes))
    except (TypeError, ValueError):
        within = 10
    patterns = _skip_patterns()
    skip_literal = ", ".join(f'"{_esc(p)}"' for p in patterns)
    script = f'''
    set skipList to {{{skip_literal}}}
    set output to ""
    set startDate to current date
    set endDate to (current date) + ({within} * minutes)
    with timeout of 20 seconds
        tell application "Calendar"
            repeat with cal in calendars
                set cname to name of cal
                set doSkip to false
                repeat with pat in skipList
                    ignoring case and diacriticals
                        if cname contains pat then set doSkip to true
                    end ignoring
                end repeat
                if doSkip is false then
                    set theEvents to (every event of cal whose start date is greater than or equal to startDate and start date is less than or equal to endDate)
                    repeat with ev in theEvents
                        set mins to (((start date of ev) - (current date)) / 60) as integer
                        set output to output & (summary of ev) & "|::|" & mins & "|::|" & ((start date of ev) as string) & linefeed
                    end repeat
                end if
            end repeat
        end tell
    end timeout
    return output
    '''
    out, err = _run_applescript(script)
    if err or not out:
        return []
    events = []
    for line in out.splitlines():
        parts = line.split("|::|")
        if len(parts) >= 3:
            try:
                mins = int(parts[1])
            except ValueError:
                mins = 0
            events.append(
                {"title": parts[0].strip(), "minutes_until": mins, "start": parts[2].strip()}
            )
    return events
