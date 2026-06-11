"""Shared natural-language date/time parsing for JARVIS.

Both the Reminders and Calendar bridges parse user-facing date/time strings
through here, so "today at 9", "tomorrow at 3:30", "in 30 minutes" and bare
times like "9:00" all resolve the same way. All results are wall-clock times
in Europe/Prague (returned naive, since the AppleScript bridges build dates
in the Mac's local timezone).

Rules:
- "in X minutes/hours" is relative to now.
- If no date is given, assume today; if no time is given, assume 09:00
  (20:00 for "tonight").
- A bare hour with no AM/PM: 1-6 means PM (13:00-18:00), 7-11 means AM;
  12 means noon; 13+ is already unambiguous.
"""

import datetime
import re

try:  # dateutil is a declared dependency; degrade gracefully if missing.
    from dateutil import parser as _du_parser
except Exception:  # noqa: BLE001
    _du_parser = None

try:
    from zoneinfo import ZoneInfo

    PRAGUE_TZ = ZoneInfo("Europe/Prague")
except Exception:  # noqa: BLE001 - no tz database; fall back to system time
    PRAGUE_TZ = None

TIMEZONE_NAME = "Europe/Prague"

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# "in 30 minutes", "in 2 hours", "in 1 hr", "in 45 min"
_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE
)
# "3:30", "15:00", "9:00pm" — hour:minute with optional meridiem.
_HM_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)?", re.IGNORECASE)
# "9pm", "9 a.m." — bare hour with an explicit meridiem.
_H_MERIDIEM_RE = re.compile(r"\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)", re.IGNORECASE)
# "at 9" — bare hour introduced by "at".
_AT_H_RE = re.compile(r"\bat\s+(\d{1,2})\b", re.IGNORECASE)


def now_prague() -> datetime.datetime:
    """Current wall-clock time in Europe/Prague (naive)."""
    if PRAGUE_TZ is not None:
        return datetime.datetime.now(PRAGUE_TZ).replace(tzinfo=None)
    return datetime.datetime.now()


def _apply_meridiem_heuristic(hour: int, meridiem: str | None) -> int:
    """Resolve a 12h-clock hour: explicit am/pm wins, else 1-6 → PM, 7-11 → AM,
    12 → noon, 13+ already 24h."""
    if meridiem:
        m = meridiem.lower().replace(".", "")
        if m.startswith("p"):
            return hour % 12 + 12
        return hour % 12
    if 1 <= hour <= 6:
        return hour + 12
    return hour  # 7-12 stay as-is (12 = noon); 13+ already 24h


def parse_datetime(text: str):
    """Parse a natural-language datetime string into a (naive) Prague datetime.

    Examples: "today at 9" → today 09:00; "today at 9pm" → today 21:00;
    "tomorrow at 3:30" → tomorrow 15:30; "next Monday at 10" → Monday 10:00;
    "in 30 minutes" → now+30m; "Friday at 6pm" → next Friday 18:00;
    "9:00" → today 09:00. Returns None if nothing date-like is recognized.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    low = raw.lower()
    now = now_prague()

    # Relative offsets win outright: "in 30 minutes", "in 2 hours".
    rel = _RELATIVE_RE.search(low)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        delta = (
            datetime.timedelta(hours=amount)
            if unit.startswith(("hour", "hr"))
            else datetime.timedelta(minutes=amount)
        )
        return (now + delta).replace(second=0, microsecond=0)

    # Base date from relative words ("today", "tomorrow", weekday names).
    base = None
    base_from_words = False
    if "tomorrow" in low:
        base = now + datetime.timedelta(days=1)
        base_from_words = True
    elif "today" in low or "tonight" in low:
        base = now
        base_from_words = True
    else:
        for name, idx in _WEEKDAYS.items():
            if name in low:
                # Next upcoming occurrence; if it's that weekday today, jump a
                # week (so "Monday"/"next Monday" both mean the coming Monday).
                ahead = (idx - now.weekday()) % 7
                if ahead == 0:
                    ahead = 7
                base = now + datetime.timedelta(days=ahead)
                base_from_words = True
                break

    # Explicit time-of-day token (checked on the raw string so we know whether
    # the user actually said am/pm — dateutil can't tell us that).
    hour = minute = None
    time_span = None
    m = _HM_RE.search(low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        hour = _apply_meridiem_heuristic(hour, m.group(3))
        time_span = m.span()
    else:
        m = _H_MERIDIEM_RE.search(low)
        if m:
            hour, minute = int(m.group(1)), 0
            hour = _apply_meridiem_heuristic(hour, m.group(2))
            time_span = m.span()
        else:
            m = _AT_H_RE.search(low)
            if m:
                hour, minute = int(m.group(1)), 0
                hour = _apply_meridiem_heuristic(hour, None)
                time_span = m.span()

    # What's left once relative words and the time token are removed — if it
    # still holds content, it's an absolute date ("June 10", "2026-06-10").
    leftover = low
    if time_span:
        leftover = leftover[: time_span[0]] + " " + leftover[time_span[1] :]
    for w in ["tomorrow", "today", "tonight", "next", " at ", *_WEEKDAYS]:
        leftover = leftover.replace(w, " ")
    leftover = re.sub(r"[^0-9a-z./-]+", " ", leftover).strip(" .,-/")

    # A bare trailing hour after a relative word ("tomorrow 9") is a time.
    if base is not None and hour is None:
        bare = re.fullmatch(r"(\d{1,2})", leftover)
        if bare:
            hour = _apply_meridiem_heuristic(int(bare.group(1)), None)
            minute = 0
            leftover = ""

    # Absolute date part (e.g. "June 10", "10.6.", "2026-06-10").
    if base is None and leftover and any(c.isalnum() for c in leftover):
        if _du_parser:
            try:
                base = _du_parser.parse(
                    leftover,
                    fuzzy=True,
                    dayfirst=True,  # Prague convention: 10.6. = 10 June
                    default=now.replace(
                        hour=9, minute=0, second=0, microsecond=0
                    ),
                )
            except (ValueError, OverflowError):
                base = None

    if hour is not None:
        if base is None:
            base = now  # time only ("9:00") → today
        return base.replace(
            hour=hour, minute=minute or 0, second=0, microsecond=0
        )

    if base is not None:
        if base_from_words:
            # Date words only → default time: 09:00 (20:00 for "tonight").
            default_hour = 20 if "tonight" in low else 9
            return base.replace(
                hour=default_hour, minute=0, second=0, microsecond=0
            )
        # dateutil base: its default already supplies 09:00 when the string
        # carried no time, and keeps the parsed time when it did.
        return base.replace(second=0, microsecond=0)

    return None
