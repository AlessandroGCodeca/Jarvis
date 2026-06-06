"""macOS Notes bridge via AppleScript (osascript).

All calls fail gracefully when Notes or automation permission is unavailable.
"""

import subprocess


def _run_applescript(script: str):
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None, (result.stderr or "AppleScript error").strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "osascript not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "Notes request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def create_note(title: str, body: str):
    """Create a note. The first line becomes the title in the Notes app."""
    safe_title = title.replace('"', "'")
    # Notes renders HTML; combine title + body into the note body.
    safe_body = body.replace('"', "'").replace("\n", "<br>")
    script = f'''
    tell application "Notes"
        tell account 1
            make new note with properties {{name:"{safe_title}", body:"<div><b>{safe_title}</b></div><div>{safe_body}</div>"}}
        end tell
    end tell
    return "created"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not create note: {err}"
    return f'Created note "{title}".'


def search_notes(query: str):
    """Return note titles whose name contains ``query``."""
    safe_query = query.replace('"', "'")
    script = f'''
    set output to ""
    tell application "Notes"
        set matches to (notes whose name contains "{safe_query}")
        repeat with n in matches
            set output to output & (name of n) & linefeed
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return [f"(Notes unavailable: {err})"]
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def get_recent_notes(limit: int = 5):
    """Return the names of the most recently modified notes."""
    script = f'''
    set output to ""
    set theCount to 0
    tell application "Notes"
        repeat with n in notes
            if theCount is greater than or equal to {int(limit)} then exit repeat
            set output to output & (name of n) & linefeed
            set theCount to theCount + 1
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return [f"(Notes unavailable: {err})"]
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]
