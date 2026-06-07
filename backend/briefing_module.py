"""Daily briefing: combines calendar, unread email, and weather into one
clean block for Claude to read aloud as a natural, flowing summary.

Each source is fetched defensively so one failing bridge (e.g. Mail without
automation permission) doesn't sink the whole briefing.
"""

import datetime

import calendar_module
import mail_module
import weather_module


def _calendar_section() -> str:
    try:
        events = calendar_module.get_today_events()
        if isinstance(events, str):
            return events
        if not events:
            return "No events on the calendar today."
        return "Today's events:\n" + "\n".join(f"- {e}" for e in events)
    except Exception as exc:  # noqa: BLE001
        return f"(Calendar unavailable: {exc})"


def _email_section(limit: int = 5) -> str:
    try:
        emails = mail_module.get_unread_emails(limit)
        if isinstance(emails, str):
            return emails
        if not emails:
            return "No unread emails."
        lines = [f"{len(emails)} unread email(s):"]
        for e in emails:
            lines.append(
                f"- From {e.get('sender', '?')}: {e.get('subject', '(no subject)')}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"(Mail unavailable: {exc})"


def _weather_section(city: str) -> str:
    try:
        return weather_module.get_weather(city)
    except Exception as exc:  # noqa: BLE001
        return f"(Weather unavailable: {exc})"


def get_daily_briefing(city: str = "Prague") -> str:
    """Return a structured briefing for Claude to turn into a spoken summary."""
    today = datetime.datetime.now().strftime("%A, %B %d, %Y")
    sections = [
        f"Date: {today}",
        "",
        "CALENDAR",
        _calendar_section(),
        "",
        "EMAIL",
        _email_section(),
        "",
        "WEATHER",
        _weather_section(city),
        "",
        "Read this back to the user as one warm, natural, flowing spoken "
        "summary (not a list). Start with a greeting, mention the number of "
        "meetings and the first one, then the weather, then the unread emails "
        "and who the latest is from.",
    ]
    return "\n".join(sections)
