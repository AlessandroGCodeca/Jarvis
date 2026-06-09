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
    """Open a macOS application by name via `open -a`."""
    if not name or not name.strip():
        return "No application name provided."
    try:
        result = subprocess.run(
            ["open", "-a", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return f"Could not open {name}: {(result.stderr or '').strip()}"
        return f"Opened {name}."
    except FileNotFoundError:
        return "`open` not available (not running on macOS)."
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"Could not open {name}: {exc}"
