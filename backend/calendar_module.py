"""macOS Calendar bridge via AppleScript (osascript).

Every call is wrapped so a missing Calendar app, denied automation
permission, or a non-macOS host degrades gracefully instead of raising.
"""

import os
import subprocess

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


def create_event(title: str, date: str, time: str, duration: int = 60):
    """Create a calendar event.

    ``date`` should be like ``"June 10, 2026"`` and ``time`` like ``"3:00 PM"``;
    ``duration`` is in minutes.
    """
    when = f"{date} {time}".strip()
    safe_when = _esc(when)
    safe_title = _esc(title)
    try:
        minutes = int(duration)
    except (TypeError, ValueError):
        minutes = 60
    script = f'''
    tell application "Calendar"
        set startDate to date "{safe_when}"
        set endDate to startDate + ({minutes} * minutes)
        tell calendar 1
            make new event with properties {{summary:"{safe_title}", start date:startDate, end date:endDate}}
        end tell
    end tell
    return "created"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not create event: {err}"
    return f'Created event "{title}" on {when}.'
