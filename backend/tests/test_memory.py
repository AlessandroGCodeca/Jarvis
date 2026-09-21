"""Tests for the SQLite FTS5 memory store.

The autouse ``_isolated_state`` fixture points ``memory.DB_PATH`` at a temp
file, so these never touch the real jarvis_memory.db.
"""

import pytest

import memory


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
