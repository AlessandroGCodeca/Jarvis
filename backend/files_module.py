"""File finding for JARVIS via macOS Spotlight (`mdfind`), stdlib only.

Searches by name, recency, or content and formats the top results naturally for
voice. Every call degrades to a friendly message off-macOS (where `mdfind` /
`open` aren't available) instead of raising.
"""

import os
import re
import subprocess
import time

# file_type keyword -> Spotlight metadata clause.
_CONTENT_TYPES = {
    "documents": 'kMDItemContentTypeTree == "public.document"',
    "document": 'kMDItemContentTypeTree == "public.document"',
    "images": 'kMDItemContentTypeTree == "public.image"',
    "image": 'kMDItemContentTypeTree == "public.image"',
    "photos": 'kMDItemContentTypeTree == "public.image"',
    "pdfs": 'kMDItemContentType == "com.adobe.pdf"',
    "pdf": 'kMDItemContentType == "com.adobe.pdf"',
}

_OFF_MAC = "Spotlight search isn't available — this only works on macOS."


def _run_mdfind(args):
    """Run `mdfind` with the given argv list. Returns (lines, error)."""
    try:
        result = subprocess.run(
            ["mdfind", *args], capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None, (result.stderr or "mdfind error").strip()
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        return lines, None
    except FileNotFoundError:
        return None, _OFF_MAC
    except subprocess.TimeoutExpired:
        return None, "the file search timed out"
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return None, str(exc)


def _relative_date(ts: float) -> str:
    """Turn a modification timestamp into a spoken relative date."""
    try:
        days = int((time.time() - ts) // 86400)
    except Exception:  # noqa: BLE001
        return "unknown date"
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    if days < 31:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def _format_results(paths, descriptor: str) -> str:
    """Format up to five result paths into a natural-language sentence."""
    if not paths:
        return f"I couldn't find any files {descriptor}."
    top = paths[:5]
    parts = []
    for path in top:
        name = os.path.basename(path) or path
        folder = os.path.basename(os.path.dirname(path)) or "/"
        try:
            when = _relative_date(os.path.getmtime(path))
        except OSError:
            when = "unknown date"
        parts.append(f"{name} (modified {when}, in {folder})")
    noun = "file" if len(top) == 1 else "files"
    return f"I found {len(top)} {noun} {descriptor}: " + "; ".join(parts) + "."


def _days_from_range(time_range) -> int:
    """Coerce a time_range (int or phrase like 'last week') into a day count."""
    if time_range is None:
        return 7
    if isinstance(time_range, (int, float)):
        return max(1, int(time_range))
    s = str(time_range).strip().lower()
    mapping = {
        "today": 1, "yesterday": 2, "week": 7, "this week": 7,
        "last week": 14, "month": 30, "this month": 30, "year": 365,
    }
    if s in mapping:
        return mapping[s]
    m = re.search(r"(\d+)", s)
    return max(1, int(m.group(1))) if m else 7


def find_file(query: str, time_range=None) -> str:
    """Find files whose name matches ``query``, optionally within a time range."""
    query = (query or "").strip().replace('"', "")
    if not query:
        return "What file should I look for?"
    if time_range:
        days = _days_from_range(time_range)
        spotlight = (
            f'kMDItemFSName == "*{query}*"c '
            f"&& kMDItemFSCreationDate >= $time.today(-{days})"
        )
        lines, err = _run_mdfind([spotlight])
        descriptor = f"matching '{query}' from the last {days} days"
    else:
        lines, err = _run_mdfind(["-name", query])
        descriptor = f"matching '{query}'"
    if err:
        return f"I couldn't search for files ({err})."
    return _format_results(lines, descriptor)


def find_recent_files(file_type=None, days: int = 7) -> str:
    """Find files created in the last ``days``, optionally filtered by type."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 7
    clauses = [f"kMDItemFSCreationDate >= $time.today(-{days})"]
    type_label = ""
    if file_type:
        clause = _CONTENT_TYPES.get(str(file_type).strip().lower())
        if clause:
            clauses.append(clause)
            type_label = f" {str(file_type).strip().lower()}"
    lines, err = _run_mdfind([" && ".join(clauses)])
    if err:
        return f"I couldn't search for recent files ({err})."
    return _format_results(lines, f"created in the last {days} days{type_label}")


def find_by_content(query: str) -> str:
    """Full-content Spotlight search (matches inside files too)."""
    query = (query or "").strip()
    if not query:
        return "What should I search for?"
    lines, err = _run_mdfind([query])
    if err:
        return f"I couldn't search file contents ({err})."
    return _format_results(lines, f"mentioning '{query}'")


def open_file(path: str) -> str:
    """Open a file in its default app via `open`."""
    path = (path or "").strip()
    if not path:
        return "Which file should I open?"
    try:
        result = subprocess.run(
            ["open", path], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return f"I couldn't open that file: {(result.stderr or '').strip()}"
        return f"Opening {os.path.basename(path) or path}."
    except FileNotFoundError:
        return _OFF_MAC
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't open that file ({exc})."
