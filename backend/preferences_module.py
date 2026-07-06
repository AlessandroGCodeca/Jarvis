"""Persistent user preferences for JARVIS, stored as JSON (stdlib only).

Preferences live in ``user_preferences.json`` next to this module. The file is
gitignored (it can hold personal info) and is auto-created from sensible
defaults on first read, so a fresh checkout still works.
"""

import json
from pathlib import Path

_PREFS_PATH = Path(__file__).resolve().parent / "user_preferences.json"

DEFAULT_PREFERENCES = {
    "name": "Sandro",
    "language": "en",
    "weather_unit": "celsius",
    "weather_city": "Prague",
    "currency_from": "EUR",
    "currency_to": "CZK",
}


def _load():
    """Return the stored preferences dict, or None if unreadable/missing."""
    try:
        if _PREFS_PATH.exists():
            with _PREFS_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 - corrupt/unreadable -> fall back
        pass
    return None


def _save(data) -> bool:
    """Write the preferences dict to disk. Returns True on success."""
    try:
        with _PREFS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        return True
    except Exception:  # noqa: BLE001 - best-effort persistence
        return False


def get_all_preferences() -> dict:
    """Return all preferences, creating the file from defaults if needed."""
    data = _load()
    if data is None:
        data = dict(DEFAULT_PREFERENCES)
        _save(data)
    return data


def get_preference(key, default=None):
    """Return a single preference value, or ``default`` if unset."""
    return get_all_preferences().get(key, default)


def set_preference(key, value) -> str:
    """Persist a single preference and return a short status message."""
    if not key:
        return "I need a preference name to save."
    data = get_all_preferences()
    data[key] = value
    if _save(data):
        return f"Saved preference: {key} = {value}."
    return "I couldn't save that preference right now."
