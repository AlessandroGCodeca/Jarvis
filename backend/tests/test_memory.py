"""Tests for the SQLite FTS5 memory store.

The autouse ``_isolated_state`` fixture points ``memory.DB_PATH`` at a temp
file, so these never touch the real jarvis_memory.db.
"""

import sqlite3
import time

import pytest

import memory


def _save_at(content: str, tag: str, days_ago: float) -> None:
    """Insert a memory with a backdated timestamp."""
    memory.init_db()
    conn = sqlite3.connect(memory.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO memories (content, tag, timestamp) VALUES (?, ?, ?)",
            (content, tag, str(time.time() - days_ago * 86400)),
        )
        conn.commit()
    finally:
        conn.close()


def _all_contents():
    return {r["content"] for r in memory.get_recent(100)}


def test_save_and_search_roundtrip():
    memory.save_memory("The garage door code is 4821", tag="note")
    rows = memory.search_memory("garage")
    assert len(rows) == 1
    assert "4821" in rows[0]["content"]
    assert rows[0]["tag"] == "note"


def test_save_records_a_timestamp():
    memory.save_memory("anything")
    assert float(memory.get_recent(1)[0]["timestamp"]) > 0


def test_empty_content_is_not_stored():
    memory.save_memory("")
    memory.save_memory(None)
    assert memory.get_recent(10) == []


def test_search_returns_at_most_five_results():
    for i in range(12):
        memory.save_memory(f"recurring topic entry {i}")
    assert len(memory.search_memory("recurring")) == 5


def test_search_with_no_match_is_empty():
    memory.save_memory("something unrelated")
    assert memory.search_memory("zebra") == []


@pytest.mark.parametrize("query", ["", "   ", None])
def test_blank_search_returns_no_rows(query):
    memory.save_memory("content")
    assert memory.search_memory(query) == []


@pytest.mark.parametrize(
    "query",
    [
        'he said "hello"',   # embedded double quotes
        "AND OR NOT",        # FTS5 boolean keywords
        "wildcard*",
        "paren(thesis)",
        "colon:value",
        "-leading-dash",
        "^caret",
    ],
)
def test_search_never_raises_on_fts_metacharacters(query):
    """User speech goes straight into MATCH; raw FTS syntax must not blow up."""
    memory.save_memory("ordinary content")
    assert isinstance(memory.search_memory(query), list)


def test_search_finds_a_phrase_containing_quotes():
    memory.save_memory('She replied "yes" immediately')
    assert memory.search_memory('"yes"') != []


def test_get_recent_is_newest_first():
    for text in ["first", "second", "third"]:
        memory.save_memory(text)
    contents = [r["content"] for r in memory.get_recent(3)]
    assert contents == ["third", "second", "first"]


def test_get_recent_respects_the_limit():
    for i in range(10):
        memory.save_memory(f"entry {i}")
    assert len(memory.get_recent(4)) == 4


def test_get_recent_on_an_empty_store():
    assert memory.get_recent(5) == []


# --- corrections ------------------------------------------------------------


def test_save_and_read_back_a_correction():
    memory.save_correction("Your meeting is at 3", "No, it's at 4", topic="calendar")
    rows = memory.get_corrections()
    assert len(rows) == 1
    assert rows[0]["correct_info"] == "No, it's at 4"
    assert rows[0]["wrong_assumption"] == "Your meeting is at 3"
    assert rows[0]["topic"] == "calendar"


def test_a_correction_with_no_replacement_is_dropped():
    memory.save_correction("wrong", "")
    assert memory.get_corrections() == []


def test_a_correction_with_no_prior_assumption_is_stored_as_empty():
    memory.save_correction(None, "the right answer")
    assert memory.get_corrections()[0]["wrong_assumption"] == ""


def test_corrections_are_newest_first_and_limited():
    for i in range(6):
        memory.save_correction(f"wrong {i}", f"right {i}")
    rows = memory.get_corrections(limit=3)
    assert [r["correct_info"] for r in rows] == ["right 5", "right 4", "right 3"]


def test_init_db_is_idempotent():
    memory.init_db()
    memory.init_db()
    memory.save_memory("still works")
    assert len(memory.get_recent(1)) == 1


# --- pruning ----------------------------------------------------------------


def test_old_conversations_are_pruned():
    _save_at("ancient small talk", "conversation", days_ago=200)
    assert memory.prune_old_memories() == 1
    assert _all_contents() == set()


def test_recent_conversations_are_kept():
    _save_at("yesterday's chat", "conversation", days_ago=1)
    assert memory.prune_old_memories() == 0
    assert "yesterday's chat" in _all_contents()


def test_a_conversation_just_inside_the_window_is_kept():
    _save_at("borderline", "conversation", days_ago=memory.CONVERSATION_RETENTION_DAYS - 1)
    memory.prune_old_memories()
    assert "borderline" in _all_contents()


def test_notes_are_never_pruned_however_old():
    """Explicit notes are the things actually worth recalling years later."""
    _save_at("the garage code is 4821", "note", days_ago=5000)
    assert memory.prune_old_memories() == 0
    assert "the garage code is 4821" in _all_contents()


def test_corrections_are_never_pruned_however_old():
    _save_at("CORRECTION: the meeting is at 4", "correction", days_ago=5000)
    memory.prune_old_memories()
    assert "CORRECTION: the meeting is at 4" in _all_contents()


def test_pruning_reports_how_many_it_removed():
    for i in range(3):
        _save_at(f"old chat {i}", "conversation", days_ago=300)
    _save_at("recent chat", "conversation", days_ago=1)
    assert memory.prune_old_memories() == 3


def test_a_custom_retention_window_is_honoured():
    _save_at("two weeks ago", "conversation", days_ago=14)
    assert memory.prune_old_memories(days=7) == 1
    assert _all_contents() == set()


def test_a_zero_day_window_prunes_every_conversation():
    _save_at("just now", "conversation", days_ago=0)
    assert memory.prune_old_memories(days=0) == 1


@pytest.mark.parametrize("days", [None, -1])
def test_a_nonsensical_window_prunes_nothing(days):
    _save_at("old chat", "conversation", days_ago=500)
    assert memory.prune_old_memories(days=days) == 0
    assert "old chat" in _all_contents()


def test_pruning_with_no_eligible_tags_does_nothing():
    _save_at("old chat", "conversation", days_ago=500)
    assert memory.prune_old_memories(tags=()) == 0
    assert "old chat" in _all_contents()


def test_pruning_an_empty_store_is_a_no_op():
    assert memory.prune_old_memories() == 0


def test_pruning_is_idempotent():
    _save_at("old chat", "conversation", days_ago=500)
    assert memory.prune_old_memories() == 1
    assert memory.prune_old_memories() == 0


def test_search_still_works_after_pruning():
    """The FTS index is compacted after a bulk delete; it must stay queryable."""
    _save_at("old chat about penguins", "conversation", days_ago=500)
    memory.save_memory("a note about penguins", tag="note")
    memory.prune_old_memories()
    hits = memory.search_memory("penguins")
    assert len(hits) == 1
    assert hits[0]["tag"] == "note"


def test_pruning_leaves_the_corrections_table_alone():
    """Corrections live in their own table, not the FTS one."""
    memory.save_correction("wrong", "right")
    memory.prune_old_memories(days=0)
    assert len(memory.get_corrections()) == 1
