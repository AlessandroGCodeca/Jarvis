"""macOS Spotify control via AppleScript (osascript).

All calls are wrapped to fail gracefully when Spotify isn't installed/running,
automation permission is denied, or we're off-macOS.

Note on play(query): the Spotify AppleScript API can't search the catalog
directly, so play() opens a Spotify search URI and starts playback (best-effort).
The other controls (pause/resume/skip/volume/current) are exact.
"""

import subprocess
from urllib.parse import quote


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
        return None, "Spotify request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def play(query: str = ""):
    """Search Spotify for ``query`` and start playback. Empty query resumes."""
    if not query or not query.strip():
        return resume()
    # AppleScript can't play arbitrary catalog results, so open the search URI
    # in the app and then start playback.
    uri = "spotify:search:" + quote(query.strip())
    script = f'''
    tell application "Spotify"
        activate
        open location "{uri}"
    end tell
    delay 2
    tell application "Spotify" to play
    return "ok"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not play '{query}': {err}"
    return f"Searching Spotify for '{query}' and starting playback."


def pause():
    """Pause playback."""
    out, err = _run_applescript('tell application "Spotify" to pause')
    if err:
        return f"Could not pause: {err}"
    return "Paused Spotify."


def resume():
    """Resume playback."""
    out, err = _run_applescript('tell application "Spotify" to play')
    if err:
        return f"Could not resume: {err}"
    return "Resumed Spotify."


def skip():
    """Skip to the next track."""
    out, err = _run_applescript('tell application "Spotify" to next track')
    if err:
        return f"Could not skip: {err}"
    return get_current_track()


def previous():
    """Go to the previous track."""
    out, err = _run_applescript('tell application "Spotify" to previous track')
    if err:
        return f"Could not go to previous track: {err}"
    return get_current_track()


def get_current_track():
    """Return the current track as 'Song — Artist', or a friendly message."""
    script = '''
    tell application "Spotify"
        if player state is playing or player state is paused then
            set trackName to name of current track
            set trackArtist to artist of current track
            return trackName & " |::| " & trackArtist
        else
            return ""
        end if
    end tell
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not read current track: {err}"
    if not out:
        return "Nothing is playing on Spotify right now."
    parts = out.split("|::|")
    if len(parts) >= 2:
        return f"Now playing: {parts[0].strip()} by {parts[1].strip()}."
    return f"Now playing: {out}."


def get_volume():
    """Return the current Spotify volume (0-100), or None on failure."""
    out, err = _run_applescript(
        'tell application "Spotify" to get sound volume'
    )
    if err:
        return None
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


def set_volume(level: int):
    """Set Spotify volume to ``level`` (0-100)."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 50
    level = max(0, min(100, level))
    out, err = _run_applescript(
        f'tell application "Spotify" to set sound volume to {level}'
    )
    if err:
        return f"Could not set volume: {err}"
    return f"Spotify volume set to {level}."


def adjust_volume(direction: str, step: int = 15):
    """Nudge the volume up or down by ``step`` (default 15)."""
    current = get_volume()
    if current is None:
        return set_volume(50)
    new_level = current + step if direction == "up" else current - step
    return set_volume(max(0, min(100, new_level)))
