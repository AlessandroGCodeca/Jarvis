"""Terminal and OS actions for JARVIS.

``run_command`` executes shell commands with a hard timeout; ``open_app``
launches a macOS application. Both fail gracefully off-macOS or on error.
"""

import subprocess


def run_command(cmd: str):
    """Run a shell command with a 10s timeout. Returns stdout/stderr text."""
    if not cmd or not cmd.strip():
        return "No command provided."
    try:
        result = subprocess.run(
            cmd,
            shell=True,
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
