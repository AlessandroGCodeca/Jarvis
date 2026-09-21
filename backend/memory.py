"""SQLite FTS5-backed persistent memory for JARVIS.

Stores conversations and notes with a timestamp and tag, and exposes
full-text search over them. A fresh connection is opened per call so the
store is safe to use from FastAPI's threadpool.
"""

import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(__file__), "jarvis_memory.db")

# Every exchange is saved as a "conversation" memory, so the store grows for as
# long as JARVIS is used. Beyond disk, an FTS index full of years of small talk
# crowds out the things worth recalling. Conversations age out after a season;
# explicit notes and learned corrections are kept forever.
CONVERSATION_RETENTION_DAYS = 90
PRUNABLE_TAGS = ("conversation",)

_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the FTS5 + corrections tables if they don't exist. Idempotent."""
    global _initialized
    conn = _connect()
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories "
            "USING fts5(content, tag, timestamp UNINDEXED)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY,
                wrong_assumption TEXT,
                correct_info TEXT,
                timestamp REAL,
                topic TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    _initialized = True


def _ensure_init() -> None:
    if not _initialized:
        init_db()


def save_memory(content: str, tag: str = "note") -> None:
    """Persist a memory with the current epoch timestamp."""
    if not content:
        return
    _ensure_init()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO memories (content, tag, timestamp) VALUES (?, ?, ?)",
            (content, tag, str(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def _fts_query(query: str) -> str:
    """Wrap raw user text as a quoted FTS5 phrase to avoid syntax errors."""
    safe = query.replace('"', '""').strip()
    return f'"{safe}"'


def search_memory(query: str):
    """Full-text search; returns up to the top 5 matching memories."""
    if not query or not query.strip():
        return []
    _ensure_init()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT content, tag, timestamp FROM memories "
            "WHERE memories MATCH ? ORDER BY rank LIMIT 5",
            (_fts_query(query),),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # Malformed FTS expression — fail gracefully with no results.
        return []
    finally:
        conn.close()


def get_recent(n: int = 10):
    """Return the most recent ``n`` memories, newest first."""
    _ensure_init()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT content, tag, timestamp FROM memories "
            "ORDER BY rowid DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prune_old_memories(
    days: int = CONVERSATION_RETENTION_DAYS, tags=PRUNABLE_TAGS
) -> int:
    """Delete conversation memories older than ``days``; return how many went.

    Only the tags in ``tags`` are eligible — notes and corrections are never
    pruned, however old. Called once at server startup; safe to call again.
    """
    if not tags or days is None or days < 0:
        return 0
    cutoff = time.time() - days * 86400
    _ensure_init()
    conn = _connect()
    try:
        placeholders = ",".join("?" for _ in tags)
        # timestamp is stored as TEXT and UNINDEXED, so compare numerically.
        removed = conn.execute(
            f"DELETE FROM memories WHERE tag IN ({placeholders}) "
            "AND CAST(timestamp AS REAL) < ?",
            (*tags, cutoff),
        ).rowcount
        conn.commit()
        if removed:
            # Compact the FTS index after a bulk delete.
            conn.execute("INSERT INTO memories(memories) VALUES('optimize')")
            conn.commit()
        return max(removed, 0)
    except sqlite3.OperationalError:
        return 0  # pruning is housekeeping; never break startup over it
    finally:
        conn.close()


def save_correction(wrong: str, correct: str, topic: str = None) -> None:
    """Persist a learned correction (what was wrong vs. what's right)."""
    if not correct:
        return
    _ensure_init()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO corrections (wrong_assumption, correct_info, "
            "timestamp, topic) VALUES (?, ?, ?, ?)",
            (wrong or "", correct, time.time(), topic),
        )
        conn.commit()
    finally:
        conn.close()


def get_corrections(limit: int = 10):
    """Return the most recent corrections, newest first."""
    _ensure_init()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT wrong_assumption, correct_info, timestamp, topic "
            "FROM corrections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
