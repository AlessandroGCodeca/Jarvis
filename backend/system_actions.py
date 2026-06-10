"""Terminal and OS actions for JARVIS.

``run_command`` executes a *restricted* allowlist of shell commands with a hard
timeout; ``open_app`` launches a macOS application. Both fail gracefully
off-macOS or on error.

Safety: Claude can read untrusted text (web search results, email contents) and
could otherwise be steered (prompt injection) into running arbitrary commands.
``run_command`` therefore only permits a small allowlist of safe utilities and
runs them with ``shell=False`` so shell metacharacters can't be abused.
"""

import shlex
import subprocess

# Only commands whose first token / prefix matches one of these may run.
# Anything else is refused without executing.
ALLOWED_PREFIXES = ("open ", "ls", "pwd", "echo", "date", "whoami", "say ")

# Friendly spoken app names -> the macOS application name `open -a` expects.
APP_ALIASES = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "spotify": "Spotify",
    "chrome": "Google Chrome",
    "browser": "Google Chrome",
    "safari": "Safari",
    "terminal": "Terminal",
    "finder": "Finder",
    "notes": "Notes",
    "calendar": "Calendar",
    "mail": "Mail",
    "slack": "Slack",
    "discord": "Discord",
    "figma": "Figma",
    "notion": "Notion",
    "whatsapp": "WhatsApp",
    "messages": "Messages",
    "facetime": "FaceTime",
    "photos": "Photos",
    "music": "Music",
    "maps": "Maps",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "zoom": "zoom.us",
    "claude": "Claude",
}


def _resolve_app(name: str) -> str:
    """Map a spoken app name to its canonical macOS application name."""
    if not name:
        return name
    return APP_ALIASES.get(name.strip().lower(), name.strip())


def _esc_as(s: str) -> str:
    """Escape a string for safe interpolation into an AppleScript literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _is_allowed(cmd: str) -> bool:
    """True if ``cmd`` starts with one of the allowlisted command prefixes."""
    stripped = cmd.strip()
    for prefix in ALLOWED_PREFIXES:
        token = prefix.strip()
        # Match the bare command ("ls", "pwd") or the command followed by args
        # ("ls -la", "open Safari", "say hello").
        if stripped == token or stripped.startswith(token + " "):
            return True
    return False


def run_command(cmd: str):
    """Run an allowlisted shell command with a 10s timeout.

    Only the commands in ``ALLOWED_PREFIXES`` are permitted; everything else is
    refused. Commands run with ``shell=False`` (arguments are tokenized with
    ``shlex``), so shell metacharacters cannot chain extra commands.
    """
    if not cmd or not cmd.strip():
        return "No command provided."

    if not _is_allowed(cmd):
        allowed = ", ".join(p.strip() for p in ALLOWED_PREFIXES)
        return (
            f"Refused: '{cmd.strip()}' is not on the allowlist. "
            f"Only these commands are permitted: {allowed}."
        )

    try:
        args = shlex.split(cmd)
    except ValueError as exc:
        return f"Could not parse command: {exc}"
    if not args:
        return "No command provided."

    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr] {stderr}")
        if not parts:
            parts.append(f"(exit code {result.returncode}, no output)")
        return "\n".join(parts)
    except FileNotFoundError:
        return f"Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return "Command timed out after 10 seconds."
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"Command failed: {exc}"


def open_app(name: str):
    """Open a macOS application by name (resolving common aliases) via `open -a`."""
    if not name or not name.strip():
        return "No application name provided."
    app = _resolve_app(name)
    try:
        result = subprocess.run(
            ["open", "-a", app],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return f"Could not open {app}: {(result.stderr or '').strip()}"
        return f"Opened {app}."
    except FileNotFoundError:
        return "`open` not available (not running on macOS)."
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"Could not open {app}: {exc}"


# --------------------------------------------------------------------------- #
# AppleScript / Shortcuts helpers
# --------------------------------------------------------------------------- #


def _run_applescript(script: str):
    """Run an AppleScript snippet. Returns (stdout, error_message)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None, (result.stderr or "AppleScript error").strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "osascript not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def _run_shortcut(name: str):
    """Run a macOS Shortcut by name. Returns (stdout, error_message)."""
    try:
        result = subprocess.run(
            ["shortcuts", "run", name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None, (result.stderr or "shortcut failed").strip()
        return (result.stdout or "").strip(), None
    except FileNotFoundError:
        return None, "shortcuts CLI not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "shortcut timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


# --------------------------------------------------------------------------- #
# System volume
# --------------------------------------------------------------------------- #


def get_volume():
    """Return the current system output volume (0-100), or None on failure."""
    out, err = _run_applescript("output volume of (get volume settings)")
    if err:
        return None
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


def set_volume(level):
    """Set the system output volume to ``level`` (0-100)."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "I need a number between 0 and 100 for the volume."
    level = max(0, min(100, level))
    out, err = _run_applescript(f"set volume output volume {level}")
    if err:
        return f"I couldn't change the volume ({err})."
    return f"Volume set to {level}%."


def volume_up(step: int = 10):
    """Raise the system volume by ``step`` (default 10), capped at 100."""
    current = get_volume()
    if current is None:
        return set_volume(60)
    return set_volume(min(100, current + step))


def volume_down(step: int = 10):
    """Lower the system volume by ``step`` (default 10), floored at 0."""
    current = get_volume()
    if current is None:
        return set_volume(40)
    return set_volume(max(0, current - step))


def mute():
    """Mute system output."""
    out, err = _run_applescript("set volume with output muted")
    if err:
        return f"I couldn't mute the volume ({err})."
    return "Muted."


def unmute():
    """Unmute system output."""
    out, err = _run_applescript("set volume without output muted")
    if err:
        return f"I couldn't unmute the volume ({err})."
    return "Unmuted."


# --------------------------------------------------------------------------- #
# Display brightness
# --------------------------------------------------------------------------- #


def set_brightness(level):
    """Set display brightness. ``level`` may be 0-100 (percent) or 0.0-1.0.

    Uses the `brightness` CLI (https://github.com/nriley/brightness) for precise
    control; if it isn't installed, returns a friendly note. On recent macOS the
    CLI may require ``sudo``.
    """
    try:
        level = float(level)
    except (TypeError, ValueError):
        return "I need a brightness between 0 and 100."
    fraction = level / 100.0 if level > 1 else level
    fraction = max(0.0, min(1.0, fraction))
    try:
        result = subprocess.run(
            ["brightness", f"{fraction:.2f}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return f"Brightness set to {round(fraction * 100)}%."
        return (
            "I couldn't set the brightness — the `brightness` tool may need "
            "sudo on this Mac."
        )
    except FileNotFoundError:
        return (
            "I can't set an exact brightness without the `brightness` tool. "
            "Install it with `brew install brightness` for precise control."
        )
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't set the brightness ({exc})."


def _brightness_keys(key_code: int, steps: int, direction: str):
    """Tap the brightness up/down media key ``steps`` times via System Events."""
    steps = max(1, int(steps))
    lines = ['tell application "System Events"']
    lines += [f"key code {key_code}"] * steps
    lines.append("end tell")
    out, err = _run_applescript("\n".join(lines))
    if err:
        return f"I couldn't change the brightness ({err})."
    return f"Turned the brightness {direction}."


def brighter(steps: int = 2):
    """Increase brightness using the brighter media key (key code 144)."""
    return _brightness_keys(144, steps, "up")


def dimmer(steps: int = 2):
    """Decrease brightness using the dimmer media key (key code 145)."""
    return _brightness_keys(145, steps, "down")


# --------------------------------------------------------------------------- #
# Focus / Do Not Disturb, timers, pomodoro
# --------------------------------------------------------------------------- #


def enable_focus_mode(minutes=None, notifier=None):
    """Turn on Do Not Disturb / a Focus via the Shortcuts app (best-effort).

    Looks for a Shortcut named "Do Not Disturb" or "Focus". If ``minutes`` and a
    ``notifier`` are given, also schedules a reminder for when the session ends.
    """
    out, err = _run_shortcut("Do Not Disturb")
    if err:
        out, err = _run_shortcut("Focus")
    if err:
        return (
            "I couldn't switch on Do Not Disturb automatically. If you create a "
            'Shortcut named "Do Not Disturb" or "Focus" that toggles a Focus, '
            "I'll be able to run it."
        )
    message = "Do Not Disturb is on."
    if minutes and notifier:
        try:
            mins = int(minutes)
            if mins > 0:
                notifier(
                    mins * 60,
                    f"Your {mins}-minute focus session is over. "
                    "You can turn off Do Not Disturb.",
                )
                message = f"Do Not Disturb is on for {mins} minutes."
        except (TypeError, ValueError):
            pass
    return message


def disable_focus_mode():
    """Turn off Do Not Disturb / Focus via the Shortcuts app (best-effort)."""
    out, err = _run_shortcut("Do Not Disturb")
    if err:
        out, err = _run_shortcut("Focus")
    if err:
        return "I couldn't switch off Do Not Disturb automatically."
    return "Do Not Disturb is off."


def start_timer(minutes, label: str = "Focus session", notifier=None):
    """Start a timer that speaks a notification after ``minutes`` minutes.

    ``notifier`` is a callable ``(delay_seconds, text)`` provided by the server
    that schedules the spoken alert. Without it, the timer can't fire.
    """
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return "How many minutes should the timer run for?"
    if mins <= 0:
        return "The timer needs to be at least one minute."
    if notifier is None:
        return f"I can't run a live timer right now, but I'd set one for {mins} minute(s)."
    label = (label or "Focus session").strip() or "Focus session"
    notifier(mins * 60, f"Your {label} is complete!")
    return (
        f"Okay, a {mins}-minute timer is running for your {label.lower()}. "
        "I'll let you know when it's done."
    )


def pomodoro(notifier=None, focus_minutes: int = 25, break_minutes: int = 5):
    """Run one pomodoro cycle: a focus block then a break, announcing each phase."""
    if notifier is None:
        return "I can't run a live pomodoro timer right now."
    focus_secs = focus_minutes * 60
    notifier(
        focus_secs,
        f"Focus time is up — take a {break_minutes}-minute break.",
    )
    notifier(
        focus_secs + break_minutes * 60,
        "Break's over — time to focus again.",
    )
    return (
        f"Pomodoro started: {focus_minutes} minutes of focus, then a "
        f"{break_minutes}-minute break. I'll announce each phase."
    )


# --------------------------------------------------------------------------- #
# App management (close / switch / list)
# --------------------------------------------------------------------------- #


def close_app(name: str):
    """Quit a macOS application by name (resolving common aliases)."""
    if not name or not name.strip():
        return "No application name provided."
    app = _resolve_app(name)
    out, err = _run_applescript(f'quit app "{_esc_as(app)}"')
    if err:
        return f"Could not close {app}: {err}"
    return f"Closed {app}."


def switch_to_app(name: str):
    """Bring a macOS application to the foreground (resolving common aliases)."""
    if not name or not name.strip():
        return "No application name provided."
    app = _resolve_app(name)
    out, err = _run_applescript(f'tell application "{_esc_as(app)}" to activate')
    if err:
        return f"Could not switch to {app}: {err}"
    return f"Switched to {app}."


def list_running_apps():
    """Return the names of the user's foreground (non-background) apps."""
    out, err = _run_applescript(
        "tell application \"System Events\" to get name of every process "
        "where background only is false"
    )
    if err:
        return f"I couldn't list the running apps ({err})."
    apps = [a.strip() for a in (out or "").split(",") if a.strip()]
    if not apps:
        return "No foreground apps appear to be running."
    return "Currently open apps: " + ", ".join(apps) + "."
