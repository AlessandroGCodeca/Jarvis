"""iMessage and WhatsApp bridges via AppleScript (osascript).

Reading is best-effort (Messages' AppleScript surface is limited and WhatsApp
needs fragile UI scripting); both degrade to a clear explanation rather than
raising. SENDING always goes through an explicit confirmation gate enforced in
claude_client — these functions perform the action and assume the caller has
already confirmed.
"""

import subprocess

_ACCESSIBILITY_HINT = (
    "WhatsApp automation requires Accessibility permissions — enable JARVIS "
    "(or your terminal) in System Settings → Privacy & Security → Accessibility."
)


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _run_applescript(script: str, timeout: int = 20):
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
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


# --------------------------------------------------------------------------- #
# iMessage
# --------------------------------------------------------------------------- #


def get_imessages(contact: str = None, limit: int = 5):
    """Best-effort read of recent iMessages (optionally for one contact)."""
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    name_filter = (
        f'whose name contains "{_esc(contact)}"' if contact else ""
    )
    script = f'''
    set output to ""
    set theCount to 0
    tell application "Messages"
        repeat with c in (chats {name_filter})
            try
                set msgs to messages of c
            on error
                set msgs to {{}}
            end try
            set n to count of msgs
            set startIdx to n - {limit} + 1
            if startIdx < 1 then set startIdx to 1
            repeat with i from startIdx to n
                set m to item i of msgs
                try
                    set sndr to (handle of sender of m) as string
                on error
                    set sndr to "me"
                end try
                set output to output & sndr & "|::|" & (text of m) & linefeed
                set theCount to theCount + 1
            end repeat
            if theCount is greater than or equal to {limit} then exit repeat
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return (
            "I couldn't read iMessages — Messages automation may be "
            f"unavailable ({err})."
        )
    if not out:
        return "No recent iMessages found."
    msgs = []
    for line in out.splitlines():
        parts = line.split("|::|")
        if len(parts) >= 2:
            msgs.append({"sender": parts[0].strip(), "content": parts[1].strip()})
    if not msgs:
        return "No recent iMessages found."
    return msgs


def send_imessage(contact: str, message: str):
    """Send an iMessage to ``contact`` (a name, phone number, or email)."""
    if not contact or not message:
        return "I need both a recipient and a message to send."
    script = f'''
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        set theBuddy to participant "{_esc(contact)}" of svc
        send "{_esc(message)}" to theBuddy
    end tell
    return "sent"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"I couldn't send the iMessage ({err})."
    return f'Sent "{message}" to {contact}.'


def get_imessage_contacts():
    """List recent iMessage chat participants (best-effort)."""
    script = '''
    set output to ""
    tell application "Messages"
        repeat with c in chats
            try
                set output to output & (name of c) & linefeed
            end try
        end repeat
    end tell
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return f"I couldn't list iMessage contacts ({err})."
    names = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return names or "No recent iMessage chats found."


# --------------------------------------------------------------------------- #
# WhatsApp (fragile UI scripting)
# --------------------------------------------------------------------------- #


def get_whatsapp_messages(contact: str = None, limit: int = 5):
    """Best-effort WhatsApp read via UI scripting; needs Accessibility access."""
    script = '''
    tell application "System Events"
        if not (exists process "WhatsApp") then return "NOT_RUNNING"
        tell process "WhatsApp"
            set frontmost to true
        end tell
    end tell
    return "OK"
    '''
    out, err = _run_applescript(script)
    if err:
        return _ACCESSIBILITY_HINT
    if out == "NOT_RUNNING":
        return "WhatsApp isn't running — open the WhatsApp desktop app first."
    # Reading individual messages reliably via UI scripting isn't feasible.
    return (
        "WhatsApp is open, but reading individual messages needs the WhatsApp "
        "desktop app focused. I can't reliably extract its message text via "
        "automation."
    )


def send_whatsapp(contact: str, message: str):
    """Best-effort WhatsApp send via the click-to-chat URL scheme."""
    if not contact or not message:
        return "I need both a recipient and a message to send."
    # whatsapp:// send works when the desktop app is installed; UI confirms send.
    from urllib.parse import quote

    uri = f"whatsapp://send?text={quote(message)}"
    if contact.strip().lstrip("+").isdigit():
        uri = f"whatsapp://send?phone={contact.strip().lstrip('+')}&text={quote(message)}"
    try:
        result = subprocess.run(
            ["open", uri], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return _ACCESSIBILITY_HINT
    except FileNotFoundError:
        return "WhatsApp sending only works on macOS with the desktop app."
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't open WhatsApp to send ({exc})."
    return (
        f'Opened WhatsApp with your message to {contact}. Press Enter in '
        "WhatsApp to send it."
    )
