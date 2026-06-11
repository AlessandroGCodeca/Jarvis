"""Offline detection + offline-mode helpers for JARVIS.

A lightweight, non-blocking connectivity check (HEAD to Cloudflare's 1.1.1.1
with a short timeout) sets a cached online/offline flag that the rest of the
backend can consult synchronously. Also provides a tiny built-in phrase
dictionary for offline translation and a small last-known-value cache that
weather/currency use to degrade gracefully.
"""

import time

import httpx

CONNECTIVITY_URL = "https://1.1.1.1"
_TIMEOUT = 2.0
_RECHECK_INTERVAL = 300  # seconds (re-check every 5 minutes)

# Module-level state, read synchronously from anywhere.
_state = {"offline": False, "checked_at": 0.0}

# Last-known good values (weather summary, currency results, ...), so offline
# requests can show stale data instead of nothing.
_cache: dict = {}

# A tiny offline phrase book for the most common greetings/courtesies.
# Keyed by lowercased English phrase -> {lang_code: translation}.
_OFFLINE_PHRASES = {
    "hello": {"sk": "ahoj", "it": "ciao", "cs": "ahoj"},
    "hi": {"sk": "ahoj", "it": "ciao", "cs": "ahoj"},
    "goodbye": {"sk": "dovidenia", "it": "arrivederci", "cs": "na shledanou"},
    "good morning": {"sk": "dobré ráno", "it": "buongiorno", "cs": "dobré ráno"},
    "good night": {"sk": "dobrú noc", "it": "buonanotte", "cs": "dobrou noc"},
    "thank you": {"sk": "ďakujem", "it": "grazie", "cs": "děkuji"},
    "thanks": {"sk": "ďakujem", "it": "grazie", "cs": "díky"},
    "please": {"sk": "prosím", "it": "per favore", "cs": "prosím"},
    "yes": {"sk": "áno", "it": "sì", "cs": "ano"},
    "no": {"sk": "nie", "it": "no", "cs": "ne"},
    "sorry": {"sk": "prepáč", "it": "scusa", "cs": "promiň"},
    "how are you": {
        "sk": "ako sa máš",
        "it": "come stai",
        "cs": "jak se máš",
    },
}


def is_offline() -> bool:
    """Return the cached offline flag (False until proven otherwise)."""
    return bool(_state["offline"])


def status_label() -> str:
    """Human-readable status for the HUD / status endpoint."""
    return "OFFLINE ⚠" if _state["offline"] else "ONLINE"


def remember(key: str, value) -> None:
    """Cache a last-known-good value for offline degradation."""
    _cache[key] = value


def recall(key: str, default=None):
    """Return a cached last-known-good value, or ``default``."""
    return _cache.get(key, default)


async def check_connectivity() -> bool:
    """Probe connectivity (non-blocking, 2s timeout). Returns True if online.

    Updates the cached flag as a side effect. Never raises.
    """
    online = True
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.head(CONNECTIVITY_URL)
    except Exception:  # noqa: BLE001 - any failure means treat as offline
        online = False
    _state["offline"] = not online
    _state["checked_at"] = time.time()
    return online


def offline_translate(text: str, target_code: str):
    """Best-effort offline translation from the built-in phrase book.

    Returns the translated phrase, or None if it isn't in the dictionary.
    """
    if not text or not target_code:
        return None
    phrase = text.strip().lower().rstrip("?.!")
    entry = _OFFLINE_PHRASES.get(phrase)
    if entry:
        return entry.get(target_code)
    return None
