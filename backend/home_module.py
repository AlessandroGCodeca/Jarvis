"""HomeKit control for JARVIS via the macOS Shortcuts CLI.

Unlike Calendar, Mail and Notes, the Home app has no AppleScript dictionary —
there is no ``tell application "Home"``. The supported native route to HomeKit
from a script is the Shortcuts app: you build a shortcut containing Home
actions (a scene, or "Control My Home" for an accessory) and run it with
``shortcuts run "<name>"``. That CLI ships with macOS 12 Monterey and later.

So a "device" here is really a shortcut name. JARVIS lists the shortcuts in a
dedicated folder (``home_shortcuts_folder``, default "Home") so it only ever
sees home controls rather than every shortcut on the Mac, and falls back to the
full list if that folder doesn't exist.

Two safeguards, because a misheard command here unlocks a door rather than
mistyping a note:

* Anything naming a lock, door, garage, gate or alarm requires an explicit
  confirmation, the same gate the iMessage/WhatsApp tools use.
* A name that matches several shortcuts asks which one instead of guessing.
"""

import re
import subprocess
import time

import preferences_module

SHORTCUTS_BIN = "shortcuts"
DEFAULT_FOLDER = "Home"
_TIMEOUT = 30

# Shortcut listings are stable and the CLI is slow enough to notice mid-sentence.
_CACHE_TTL = 300
_cache = {"names": None, "folder": None, "ts": 0.0}

# Words that make an action security-sensitive. Matched whole-word so "alarm"
# and "warm" can't be mistaken for "arm".
_SENSITIVE_RE = re.compile(
    r"\b(lock|unlock|locks|locked|unlocking|door|doors|garage|gate|gates|"
    r"alarm|alarms|arm|disarm|armed|security|safe|deadbolt|entry|"
    r"front\s*door|back\s*door)\b",
    re.IGNORECASE,
)


def _run_shortcuts(args, timeout: int = _TIMEOUT):
    """Run the shortcuts CLI. Returns ``(stdout, error)``; one is always None."""
    try:
        result = subprocess.run(
            [SHORTCUTS_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None, (result.stderr or "Shortcuts returned an error").strip()
        return result.stdout, None
    except FileNotFoundError:
        return None, (
            "The shortcuts command isn't available — it needs macOS 12 "
            "Monterey or later."
        )
    except subprocess.TimeoutExpired:
        return None, "The Home request timed out."
    except Exception as exc:  # noqa: BLE001 - fail gracefully like the other bridges
        return None, str(exc)


def _parse_names(stdout: str):
    """Shortcuts prints one name per line; drop blanks and stray whitespace."""
    return [line.strip() for line in (stdout or "").splitlines() if line.strip()]


def _home_folder() -> str:
    """The Shortcuts folder holding home controls (a user preference)."""
    return preferences_module.get_preference("home_shortcuts_folder", DEFAULT_FOLDER)


def list_shortcuts(folder: str = None):
    """Shortcut names in ``folder`` (or all of them). Returns a list or an error."""
    args = ["list"]
    if folder:
        args += ["-f", folder]
    stdout, err = _run_shortcuts(args)
    if err:
        return err
    return _parse_names(stdout)


def available_devices(refresh: bool = False):
    """Home shortcut names, cached briefly. Returns a list or an error string.

    Prefers the configured folder; if it's missing or empty, falls back to every
    shortcut so a user who hasn't organised theirs into a folder still works.
    """
    folder = _home_folder()
    fresh = (
        not refresh
        and _cache["names"] is not None
        and _cache["folder"] == folder
        and (time.time() - _cache["ts"]) < _CACHE_TTL
    )
    if fresh:
        return _cache["names"]

    names = list_shortcuts(folder)
    if isinstance(names, str):  # folder missing -> the CLI errors; fall back
        names = list_shortcuts(None)
    elif not names:
        names = list_shortcuts(None)
    if isinstance(names, str):
        return names  # Shortcuts genuinely unavailable

    _cache.update({"names": names, "folder": folder, "ts": time.time()})
    return names


def invalidate_cache() -> None:
    """Forget the cached shortcut list (after the user adds a new one)."""
    _cache.update({"names": None, "folder": None, "ts": 0.0})


def _tokens(text: str):
    return set(re.findall(r"\w+", (text or "").lower()))


def _match(spoken: str, candidates):
    """Resolve spoken text to shortcut names.

    Returns ``(exact, options)``: a single confident match, or a list of
    plausible ones to disambiguate. Tried in order — exact name, substring
    either way, then word overlap — so "lights" finds "Living Room Lights".
    """
    key = (spoken or "").strip().lower()
    if not key:
        return None, []

    for name in candidates:
        if name.lower() == key:
            return name, []

    subs = [n for n in candidates if key in n.lower() or n.lower() in key]
    if len(subs) == 1:
        return subs[0], []
    if subs:
        return None, subs

    spoken_words = _tokens(key)
    overlap = [n for n in candidates if _tokens(n) & spoken_words]
    if len(overlap) == 1:
        return overlap[0], []
    return None, overlap


def needs_confirmation(name: str) -> bool:
    """True if this action touches a lock, door, garage, gate or alarm."""
    return bool(_SENSITIVE_RE.search(name or ""))


def list_home_devices() -> str:
    """Spoken list of the home controls JARVIS can run."""
    names = available_devices(refresh=True)
    if isinstance(names, str):
        return names
    if not names:
        return (
            "I can't see any Home shortcuts. Create one in the Shortcuts app "
            f"(a Home action, in a folder called '{_home_folder()}') and I'll "
            "be able to run it."
        )
    noun = "control" if len(names) == 1 else "controls"
    return f"I can run {len(names)} home {noun}: " + ", ".join(names) + "."


def run_home_shortcut(name: str, confirmed: bool = False) -> str:
    """Run a home shortcut by (approximate) name."""
    if not name or not name.strip():
        return "Which home control should I run?"

    names = available_devices()
    if isinstance(names, str):
        return names
    if not names:
        return (
            "I can't see any Home shortcuts to run. Create one in the "
            "Shortcuts app first."
        )

    exact, options = _match(name, names)
    if exact is None:
        if options:
            return (
                f"I found several matches for '{name}': "
                + ", ".join(options)
                + ". Which one?"
            )
        return (
            f"I don't have a home control called '{name}'. "
            f"I know: {', '.join(names)}."
        )

    # Locks, doors and alarms need to be said out loud twice.
    if needs_confirmation(exact) and not confirmed:
        return (
            f"Please confirm out loud: run '{exact}'? "
            "I won't touch locks, doors or alarms until you say yes."
        )

    _, err = _run_shortcuts(["run", exact])
    if err:
        return f"I couldn't run '{exact}': {err}"
    return f"Done — ran '{exact}'."
