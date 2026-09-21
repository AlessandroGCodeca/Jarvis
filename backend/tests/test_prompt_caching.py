"""Tests for the prompt-cache layout.

Caching is a prefix match over ``tools`` -> ``system`` -> ``messages``: one
byte changing anywhere in the prefix invalidates everything after it. The
value here is entirely in *what sits before the breakpoints*, so these tests
pin that layout rather than any API behaviour.
"""

import json

import pytest

import claude_client


# Minimum cacheable prefix, per model. Below it the API silently declines to
# cache: no error, just cache_creation_input_tokens: 0. The value is not
# monotonic across generations, so it has to be looked up rather than assumed.
MIN_CACHEABLE_PREFIX_TOKENS = {
    "claude-haiku-4-5": 4096,
    "claude-opus-4-6": 4096,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-opus-5": 512,
}
# An unknown/overridden CLAUDE_MODEL is held to the largest known minimum.
STRICTEST_MINIMUM = max(MIN_CACHEABLE_PREFIX_TOKENS.values())

# messages.count_tokens is the exact answer, but it needs a network call and
# this suite forbids those, so the check uses the usual four-chars-per-token
# rule of thumb — and demands enough headroom that the approximation can be
# well wrong and the prefix still cache.
CHARS_PER_TOKEN = 4
REQUIRED_MARGIN = 1.25


def _blocks(brain, **kw):
    return brain._system_prompt(**kw)


def _approx_tokens(obj) -> float:
    return len(json.dumps(obj)) / CHARS_PER_TOKEN


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
    """The configured model won't cache a prefix shorter than its minimum, and
    says nothing when it declines — so trimming the tool list could silently
    switch caching off."""
    minimum = MIN_CACHEABLE_PREFIX_TOKENS.get(
        claude_client.MODEL, STRICTEST_MINIMUM
    )
    assert _approx_tokens(claude_client.TOOLS) > minimum * REQUIRED_MARGIN


def test_the_configured_model_has_a_known_cache_minimum():
    """If CLAUDE_MODEL moves to a model that isn't in the table, the test above
    silently falls back to the strictest value — flag that rather than hide it."""
    assert claude_client.MODEL in MIN_CACHEABLE_PREFIX_TOKENS, (
        f"{claude_client.MODEL} has no recorded minimum cacheable prefix; add "
        "it to MIN_CACHEABLE_PREFIX_TOKENS so the margin check stays honest."
    )


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
