"""macOS Mail bridge via AppleScript (osascript).

All calls fail gracefully when Mail or automation permission is unavailable.
"""

import subprocess


def _run_applescript(script: str):
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=35,
        )
        if result.returncode != 0:
            return None, (result.stderr or "AppleScript error").strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "osascript not available (not running on macOS)"
    except subprocess.TimeoutExpired:
        return None, "Mail request timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def get_unread_emails(limit: int = 5):
    """Return up to ``limit`` unread messages as dicts of subject/sender/preview.

    For speed we scan only a bounded recent slice of the inbox (not the whole
    mailbox with a ``whose`` filter) and read only header fields (subject and
    sender) — fetching each message's body over IMAP is what made this slow.
    The preview is therefore left empty.
    """
    # Records are emitted with field separators so we can parse them reliably.
    script = f'''
    set output to ""
    set foundCount to 0
    with timeout of 30 seconds
        tell application "Mail"
            try
                set recentMsgs to messages 1 thru 60 of inbox
            on error
                set recentMsgs to messages of inbox
            end try
            repeat with msg in recentMsgs
                if foundCount is greater than or equal to {int(limit)} then exit repeat
                if (read status of msg) is false then
                    try
                        set theSubject to subject of msg
                        set theSender to sender of msg
                        set output to output & theSubject & "|::|" & theSender & "|::|" & "|##|"
                        set foundCount to foundCount + 1
                    end try
                end if
            end repeat
        end tell
    end timeout
    return output
    '''
    out, err = _run_applescript(script)
    if err:
        return [{"subject": "Mail unavailable", "sender": "system", "preview": err}]
    if not out:
        return []
    emails = []
    for record in out.split("|##|"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("|::|")
        if len(parts) >= 3:
            emails.append(
                {
                    "subject": parts[0].strip(),
                    "sender": parts[1].strip(),
                    "preview": " ".join(parts[2].split()).strip(),
                }
            )
    return emails


def send_email(to: str, subject: str, body: str):
    """Compose and send an email through Mail."""
    safe_subject = subject.replace('"', "'")
    safe_body = body.replace('"', "'")
    script = f'''
    tell application "Mail"
        set newMsg to make new outgoing message with properties {{subject:"{safe_subject}", content:"{safe_body}", visible:false}}
        tell newMsg
            make new to recipient at end of to recipients with properties {{address:"{to}"}}
            send
        end tell
    end tell
    return "sent"
    '''
    out, err = _run_applescript(script)
    if err:
        return f"Could not send email: {err}"
    return f"Email sent to {to}."
