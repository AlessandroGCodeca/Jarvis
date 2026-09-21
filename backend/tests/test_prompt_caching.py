"""Tests for the prompt-cache layout.

Caching is a prefix match over ``tools`` -> ``system`` -> ``messages``: one
byte changing anywhere in the prefix invalidates everything after it. The
value here is entirely in *what sits before the breakpoints*, so these tests
pin that layout rather than any API behaviour.
"""

import json

import pytest

import claude_client


def _blocks(brain, **kw):
    return brain._system_prompt(**kw)


@pytest.fixture
def brain():
    return claude_client.JarvisBrain()


# --- the tool schemas -------------------------------------------------------


def test_the_tool_block_carries_a_cache_breakpoint():
    """~6k tokens of schema re-sent on every hop of the tool loop."""
    assert claude_client.TOOLS[-1]["cache_control"] == {"type": "ephemeral"}


def test_only_the_final_tool_carries_the_breakpoint():
    """A breakpoint caches everything before it, so one at the end is enough —
    and each extra one spends a scarce per-request slot."""
    marked = [t for t in claude_client.TOOLS if "cache_control" in t]
    assert len(marked) == 1
    assert marked[0] is claude_client.TOOLS[-1]


def test_the_tool_block_clears_the_minimum_cacheable_prefix():
    """Haiku 4.5 needs 4096 tokens before it will cache at all; below that it
    silently doesn't, with no error."""
    approx_tokens = len(json.dumps(claude_client.TOOLS)) / 4
    assert approx_tokens > 4096


def test_the_tool_schemas_are_stable_across_turns():
    """Any per-turn mutation of TOOLS would invalidate the cache every time."""
    before = json.dumps(claude_client.TOOLS, sort_keys=True)
    claude_client.JarvisBrain()._system_prompt(memories="x", context_block="y")
    assert json.dumps(claude_client.TOOLS, sort_keys=True) == before


# --- the system prompt split ------------------------------------------------


def test_the_system_prompt_is_split_into_two_blocks(brain):
    blocks = _blocks(brain)
    assert len(blocks) == 2
    assert all(b["type"] == "text" for b in blocks)


def test_the_first_block_is_cached_and_the_second_is_not(brain):
    stable, volatile = _blocks(brain)
    assert stable["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in volatile


def test_the_cached_block_is_identical_across_different_turns(brain):
    """The whole point: whatever the turn carries, the cached prefix is the
    same bytes, so it keeps hitting."""
    first = _blocks(brain, memories="alpha", context_block="It's morning")[0]
    second = _blocks(
        brain, memories="beta", language_instruction="Respond in Czech.",
        context_block="It's night",
    )[0]
    assert first == second


def test_the_cached_block_holds_no_clock_or_date(brain):
    """A timestamp in the cached block would invalidate it every minute —
    which is exactly why the split exists."""
    stable = _blocks(brain, context_block="Current time: 14:32")[0]["text"]
    assert "Current date" not in stable
    assert "Current time" not in stable
    assert "14:32" not in stable


def test_the_cached_block_still_carries_the_operating_rules(brain):
    """Splitting must not drop the safety rules into the volatile half."""
    stable = _blocks(brain)[0]["text"]
    assert "You are JARVIS" in stable
    assert "confirmed=true" in stable  # the message-send confirmation rule


def test_the_volatile_block_carries_everything_that_moves(brain):
    volatile = _blocks(
        brain,
        memories="- the wifi code is 1234",
        language_instruction="Respond entirely in Czech.",
        context_block="Current context: It's evening.",
    )[1]["text"]
    assert "Current date" in volatile
    assert "User preferences (JSON)" in volatile
    assert "the wifi code is 1234" in volatile
    assert "Respond entirely in Czech." in volatile
    assert "It's evening" in volatile


def test_a_preference_change_does_not_disturb_the_cached_block(brain):
    """Preferences move into the volatile half, so editing them is free."""
    before = _blocks(brain)[0]
    brain.preferences = dict(brain.preferences, weather_city="Bratislava")
    after = _blocks(brain)[0]
    assert before == after
    assert "Bratislava" in _blocks(brain)[1]["text"]


def test_neither_block_is_empty(brain):
    """An empty text block is rejected by the API."""
    for block in _blocks(brain):
        assert block["text"].strip()
