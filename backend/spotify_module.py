"""macOS Spotify control via AppleScript (osascript).

All calls are wrapped to fail gracefully when Spotify isn't installed/running,
automation permission is denied, or we're off-macOS.

Note on play(query): the AppleScript `play` verb only resumes the current
track — it cannot pick a search result. Playing the *requested* music requires
an exact Spotify URI, which we resolve through the Web API search endpoint
(needs SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET) and hand to
`play track "<uri>"`. Without credentials we can only open the in-app search
and resume playback (best-effort), and we say so honestly.
"""

import base64
import os
import time
from urllib.parse import quote

import httpx
import subprocess

# Cached client-credentials token for the Spotify Web API search endpoint.
_token_cache = {"token": None, "expires": 0.0}


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


def _get_access_token():
    """Client-credentials token for the Spotify Web API, or None if unconfigured.

    Requires SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in the environment.
    The token (valid ~1h) is cached to avoid re-fetching on every call.
    """
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"] - 30:
        return _token_cache["token"]

    try:
        auth = base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        resp = httpx.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires"] = now + data.get("expires_in", 3600)
        return _token_cache["token"]
    except Exception:  # noqa: BLE001 - treat as "no Web API available"
        return None


def _api_get(endpoint: str, params: dict):
    """GET a Spotify Web API endpoint. Returns parsed JSON, or None."""
    token = _get_access_token()
    if not token:
        return None
    try:
        resp = httpx.get(
            f"https://api.spotify.com/v1/{endpoint}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def _track_info(track: dict):
    """Return (uri, "Song by Artist") for a Web API track object."""
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    label = f"{track['name']} by {artists}" if artists else track["name"]
    return track["uri"], label


def _resolve_query(query: str):
    """Resolve a query to (uri, label, expected_track_uri) via the Web API.

    - "lo-fi playlist"            -> the top matching playlist
    - "Kanye West" (bare artist)  -> that artist's current top track
    - anything else               -> the top matching track

    expected_track_uri is the track we expect to see as "current track" once
    playback starts (None for playlists). Returns None when the Web API is
    unconfigured/unreachable or nothing matched.
    """
    lowered = query.lower()

    if "playlist" in lowered:
        name = " ".join(w for w in query.split() if w.lower() != "playlist")
        data = _api_get(
            "search", {"q": name or query, "type": "playlist", "limit": 1}
        )
        # The search endpoint can return null entries in playlist results.
        items = [
            p
            for p in ((data or {}).get("playlists", {}).get("items") or [])
            if p
        ]
        if items:
            playlist = items[0]
            return playlist["uri"], f"the playlist {playlist['name']}", None

    data = _api_get("search", {"q": query, "type": "track,artist", "limit": 1})
    if not data:
        return None
    artists = [a for a in (data.get("artists", {}).get("items") or []) if a]
    tracks = [t for t in (data.get("tracks", {}).get("items") or []) if t]

    # A bare artist name ("Kanye West") should play that artist's top track,
    # not whichever track happens to match the words best.
    if artists and artists[0]["name"].lower() == lowered:
        artist = artists[0]
        top = _api_get(f"artists/{artist['id']}/top-tracks", {"market": "US"})
        top_tracks = (top or {}).get("tracks") or []
        if top_tracks:
            uri, label = _track_info(top_tracks[0])
            return uri, label, uri

    if tracks:
        uri, label = _track_info(tracks[0])
        return uri, label, uri
    return None


def _play_uri(uri: str):
    """Tell the desktop app to play ``uri``. Returns an error message or None.

    Launches Spotify first if needed: `play track` sent to a non-running app
    errors out, which is one way playback used to silently fall back to
    resuming the previous track.
    """
    if not uri or any(ch in uri for ch in '"\\'):
        return "invalid Spotify URI"
    script = f'''
    if application "Spotify" is not running then
        tell application "Spotify" to launch
        delay 3
    end if
    tell application "Spotify" to play track "{uri}"
    '''
    out, err = _run_applescript(script)
    return err


def _confirm_playback(label: str, expected_track_uri: str = None):
    """Wait for the player to switch tracks, then report what's playing.

    Polls the app briefly so the confirmation reflects the *new* track rather
    than whatever was playing before. Falls back to the search label if the
    player can't be read in time.
    """
    for _ in range(4):
        time.sleep(0.5)
        out, err = _run_applescript(
            'tell application "Spotify" to return (id of current track) '
            '& " |::| " & (name of current track) & " |::| " '
            '& (artist of current track)'
        )
        if err or not out:
            continue
        parts = [p.strip() for p in out.split("|::|")]
        if len(parts) >= 3 and (
            expected_track_uri is None or parts[0] == expected_track_uri
        ):
            return f"Now playing {parts[1]} by {parts[2]} on Spotify."
    return f"Playing {label} on Spotify."


def play(query: str = ""):
    """Search Spotify for ``query`` and start playback. Empty query resumes.

    If SPOTIFY_CLIENT_ID/SECRET are configured, the query is resolved to an
    exact URI (track, artist top track, or playlist) and played by URI.
    Otherwise it falls back to opening a search URI in the app and resuming
    playback (best-effort) — and the reply makes that limitation clear.
    """
    if not query or not query.strip():
        return resume()
    query = query.strip()

    # Accurate path: resolve the exact URI, then play it on the desktop app.
    resolved = _resolve_query(query)
    if resolved:
        uri, label, expected_track_uri = resolved
        err = _play_uri(uri)
        if not err:
            return _confirm_playback(label, expected_track_uri)
        # If AppleScript playback failed, fall through to the best-effort path.

    # Fallback: open the search results in the app and resume playback.
    search_uri = "spotify:search:" + quote(query)
    script = f'''
    tell application "Spotify"
        activate
        open location "{search_uri}"
    end tell
    delay 2
    tell application "Spotify" to play
    return "ok"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not play '{query}': {err}"
    message = f"I opened Spotify search results for '{query}'."
    now = get_current_track()
    if now.startswith("Now playing"):
        message += f" {now}"
    if not os.getenv("SPOTIFY_CLIENT_ID") or not os.getenv("SPOTIFY_CLIENT_SECRET"):
        message += (
            " To let me play the exact song you ask for, add "
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to backend/.env."
        )
    return message


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
    """Return the current track as 'Now playing Song by Artist.', or a friendly message."""
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
        return f"Now playing {parts[0].strip()} by {parts[1].strip()}."
    return f"Now playing {out}."


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
