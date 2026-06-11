"""Document management for JARVIS (stdlib + python-docx).

Create plain-text, Markdown and Word documents, read/append to files, find
recent documents via Spotlight, and open files in their default app. File
writes use plain Python I/O; search/open use macOS `mdfind`/`open` and degrade
gracefully elsewhere.
"""

import os
import subprocess
from pathlib import Path

_LOCATIONS = {
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
}
_SEARCH_DIRS = [_LOCATIONS["desktop"], _LOCATIONS["documents"], _LOCATIONS["downloads"]]


def _resolve_dir(location: str) -> Path:
    return _LOCATIONS.get((location or "desktop").strip().lower(), _LOCATIONS["desktop"])


def _ensure_ext(filename: str, ext: str) -> str:
    name = (filename or "untitled").strip()
    if not name.lower().endswith(ext):
        name += ext
    return name


def _open(path: str):
    """Open ``path`` in the default app (best-effort, macOS)."""
    try:
        result = subprocess.run(
            ["open", str(path)], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return False, (result.stderr or "").strip()
        return True, None
    except FileNotFoundError:
        return False, "not running on macOS"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def create_text_file(filename: str, content: str, location: str = "Desktop"):
    """Create a .txt file with ``content`` in Desktop/Documents/Downloads."""
    directory = _resolve_dir(location)
    name = _ensure_ext(filename, ".txt")
    path = directory / name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"Could not create the file: {exc}"
    return f"Created {name} in {directory.name}."


def create_markdown_note(title: str, content: str):
    """Create a Markdown note on the Desktop and open it."""
    name = _ensure_ext(title, ".md")
    path = _LOCATIONS["desktop"] / name
    body = f"# {title}\n\n{content or ''}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"Could not create the note: {exc}"
    _open(path)  # best-effort; ignore failures off-macOS
    return f"Created note {name} on the Desktop."


def create_word_doc(filename: str, content: str):
    """Create a .docx on the Desktop using python-docx."""
    try:
        from docx import Document
    except Exception:  # noqa: BLE001
        return (
            "Word documents need python-docx installed "
            "(pip install python-docx)."
        )
    name = _ensure_ext(filename, ".docx")
    path = _LOCATIONS["desktop"] / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = Document()
        # Each blank-line-separated block becomes its own paragraph.
        for block in (content or "").split("\n\n"):
            doc.add_paragraph(block.strip())
        doc.save(str(path))
    except Exception as exc:  # noqa: BLE001
        return f"Could not create the Word document: {exc}"
    return f"Created Word document {name} on the Desktop."


def _find_path(path_or_name: str):
    """Resolve a path directly, else fuzzy-find by name (mdfind, then dirs)."""
    if not path_or_name:
        return None
    candidate = Path(path_or_name).expanduser()
    if candidate.is_file():
        return candidate

    # Spotlight search by name.
    try:
        result = subprocess.run(
            ["mdfind", "-name", path_or_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.strip() and Path(line).is_file():
                return Path(line)
    except FileNotFoundError:
        pass  # not macOS — fall back to a directory scan
    except Exception:  # noqa: BLE001
        pass

    lower = path_or_name.lower()
    for directory in _SEARCH_DIRS:
        try:
            for entry in directory.iterdir():
                if entry.is_file() and lower in entry.name.lower():
                    return entry
        except OSError:
            continue
    return None


def read_file(path_or_name: str, max_chars: int = 4000):
    """Read a file's text content, searching common folders if needed."""
    path = _find_path(path_or_name)
    if path is None:
        return f"I couldn't find a file called '{path_or_name}'."
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"Could not read {path.name}: {exc}"
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return f"{path.name}:\n{text}"


def append_to_file(filename: str, content: str):
    """Append ``content`` to an existing file (found by path or name)."""
    path = _find_path(filename)
    if path is None:
        return f"I couldn't find a file called '{filename}' to append to."
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if content else "") + (content or ""))
    except Exception as exc:  # noqa: BLE001
        return f"Could not append to {path.name}: {exc}"
    return f"Appended to {path.name}."


def list_recent_documents(days: int = 7, file_type: str = None):
    """List documents created in the last ``days`` via Spotlight."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 7
    query = f"kMDItemFSCreationDate >= $time.today(-{days})"
    type_map = {
        "documents": 'kMDItemContentTypeTree == "public.document"',
        "pdf": 'kMDItemContentType == "com.adobe.pdf"',
        "pdfs": 'kMDItemContentType == "com.adobe.pdf"',
    }
    if file_type and type_map.get(file_type.lower()):
        query += " && " + type_map[file_type.lower()]
    try:
        result = subprocess.run(
            ["mdfind", query], capture_output=True, text=True, timeout=12
        )
    except FileNotFoundError:
        return "Recent-document search only works on macOS."
    except Exception as exc:  # noqa: BLE001
        return f"Could not list recent documents ({exc})."
    paths = [ln for ln in result.stdout.splitlines() if ln.strip()][:10]
    if not paths:
        return f"No documents created in the last {days} days."
    names = ", ".join(os.path.basename(p) for p in paths)
    return f"Recent documents ({len(paths)}): {names}."


def open_file(path: str):
    """Open a file in its default app (resolving by name if needed)."""
    resolved = _find_path(path) or Path(path).expanduser()
    ok, err = _open(resolved)
    if not ok:
        return f"Could not open {Path(str(path)).name}: {err}"
    return f"Opening {Path(str(resolved)).name}."
