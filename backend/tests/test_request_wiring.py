"""End-to-end tests for the request JarvisBrain actually sends.

The unit tests elsewhere check the pieces — that ``_system_prompt`` splits
correctly and that ``_execute_tools`` runs concurrently. None of them check
that ``process()`` still *uses* those pieces: revert the loop in ``process()``
to sequential ``_execute_tool`` calls, or stop passing the split system
blocks, and every one of those tests keeps passing.

So these drive a real turn through ``process()`` against a fake Anthropic
client that records each outgoing request, and assert on what went out.
"""

import copy
import threading

import pytest

import claude_client
import preferences_module


# --- a recording stand-in for the Anthropic client --------------------------


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name, block_id, tool_input=None):
        self.name = name
        self.id = block_id
        self.input = tool_input or {}


class _Usage:
    def __init__(self, uncached=0, cache_read=0, cache_write=0, output=0):
        self.input_tokens = uncached
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output


class _Response:
    def __init__(self, content, stop_reason, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        if usage is not None:
            self.usage = usage


def _final(text, usage=None):
    return _Response([_TextBlock(text)], "end_turn", usage)


def _tool_turn(*blocks, usage=None):
    return _Response(list(blocks), "tool_use", usage)


class _RecordingClient:
    """Captures every messages.create() call and replays scripted responses."""

    def __init__(self, responses):
        self.requests = []
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        # Deep-copy: `messages` is the brain's live history list, which keeps
        # being mutated after the call returns. Storing the reference would
        # mean asserting on the end state of the turn, not what was sent.
        self.requests.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("the brain made more API calls than expected")
        return self._responses.pop(0)


@pytest.fixture
def brain_with():
    """Build a brain whose API calls are captured instead of sent."""

    def _build(*responses):
        brain = claude_client.JarvisBrain()
        client = _RecordingClient(responses)
        brain.client = client
        return brain, client

    return _build


# --- what the request carries ----------------------------------------------


def test_a_turn_sends_the_split_system_blocks(brain_with):
    brain, client = brain_with(_final("hello"))
    brain.process("hi")
    system = client.requests[0]["system"]
    assert isinstance(system, list)
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]


def test_a_turn_sends_the_tool_breakpoint(brain_with):
    brain, client = brain_with(_final("hello"))
    brain.process("hi")
    assert client.requests[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_the_cached_prefix_is_byte_identical_across_real_turns(brain_with):
    """The property the whole caching change rests on, measured end to end."""
    brain, client = brain_with(_final("one"), _final("two"))
    brain.process("what's the weather")
    brain.process("and tomorrow")
    first, second = client.requests
    assert first["tools"] == second["tools"]
    assert first["system"][0] == second["system"][0]


def test_the_cached_prefix_survives_a_tool_hop(brain_with):
    """The tool loop re-sends everything on every hop; the prefix must hold."""
    brain, client = brain_with(
        _tool_turn(_ToolUseBlock("get_battery_status", "tu1")), _final("done")
    )
    brain.process("battery?")
    assert len(client.requests) == 2
    assert client.requests[0]["tools"] == client.requests[1]["tools"]
    assert client.requests[0]["system"][0] == client.requests[1]["system"][0]


def test_the_volatile_block_is_the_one_that_moves(brain_with):
    """Whatever changes per turn has to land after the breakpoint, not before."""
    brain, client = brain_with(_final("one"), _final("two"))
    brain.process("remember that the wifi code is 1234")
    # Through the store, not the attribute: process() re-reads preferences at
    # the start of every turn, so an in-place assignment would be overwritten.
    preferences_module.set_preference("weather_city", "Bratislava")
    brain.process("and the weather")
    first, second = client.requests
    assert first["system"][0]["text"] == second["system"][0]["text"]
    assert "Bratislava" in second["system"][1]["text"]
    assert "Bratislava" not in first["system"][1]["text"]


# --- that the turn loop still runs tools concurrently -----------------------


def test_a_tool_turn_runs_its_calls_concurrently(brain_with, monkeypatch):
    """Through process(), not _execute_tools directly.

    If the loop in process() reverted to calling _execute_tool one block at a
    time, the first call would sit on the barrier until it times out — so this
    fails on the wiring, which the _execute_tools unit test cannot see.
    """
    blocks = [
        _ToolUseBlock("get_calendar", "tu1", {"range": "today"}),
        _ToolUseBlock("get_emails", "tu2"),
        _ToolUseBlock("get_battery_status", "tu3"),
    ]
    brain, client = brain_with(_tool_turn(*blocks), _final("all done"))
    barrier = threading.Barrier(len(blocks), timeout=5)

    def gated(name, tool_input):
        barrier.wait()
        return f"result of {name}"

    monkeypatch.setattr(brain, "_execute_tool", gated)
    assert brain.process("calendar, mail and battery please") == "all done"


def test_tool_results_carry_the_matching_tool_use_ids(brain_with, monkeypatch):
    """Running concurrently must not scramble which result answers which call."""
    blocks = [
        _ToolUseBlock("get_calendar", "tu1"),
        _ToolUseBlock("get_emails", "tu2"),
        _ToolUseBlock("get_battery_status", "tu3"),
    ]
    brain, client = brain_with(_tool_turn(*blocks), _final("done"))
    monkeypatch.setattr(
        brain, "_execute_tool", lambda name, inp: f"result of {name}"
    )
    brain.process("three things please")

    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu1", "tu2", "tu3"]
    assert [r["content"] for r in results] == [
        "result of get_calendar",
        "result of get_emails",
        "result of get_battery_status",
    ]


def test_every_tool_call_gets_a_result_block(brain_with, monkeypatch):
    """A dropped tool_result is rejected by the API on the next hop."""
    blocks = [_ToolUseBlock(f"tool_{i}", f"tu{i}") for i in range(5)]
    brain, client = brain_with(_tool_turn(*blocks), _final("done"))
    monkeypatch.setattr(brain, "_execute_tool", lambda name, inp: "ok")
    brain.process("many things")
    assert len(client.requests[1]["messages"][-1]["content"]) == len(blocks)


def test_tool_results_go_back_in_a_single_user_message(brain_with, monkeypatch):
    """Splitting them across messages trains Claude out of parallel calls."""
    blocks = [_ToolUseBlock("a", "tu1"), _ToolUseBlock("b", "tu2")]
    brain, client = brain_with(_tool_turn(*blocks), _final("done"))
    monkeypatch.setattr(brain, "_execute_tool", lambda name, inp: "ok")
    brain.process("two things")
    tail = client.requests[1]["messages"][-1]
    assert tail["role"] == "user"
    assert isinstance(tail["content"], list)
    assert all(b["type"] == "tool_result" for b in tail["content"])


# --- the model the request is actually sent to ------------------------------


def test_the_configured_model_is_the_one_used(brain_with):
    brain, client = brain_with(_final("hi"))
    brain.process("hello")
    assert client.requests[0]["model"] == claude_client.MODEL


# --- token usage reporting --------------------------------------------------


def test_a_turn_reports_its_token_usage(brain_with, capsys):
    """Without this line there is no way to tell whether the cached prefix is
    being hit — a silent invalidation looks exactly like a working cache."""
    brain, _ = brain_with(
        _final("hi", usage=_Usage(uncached=412, cache_read=6013, output=87))
    )
    brain.process("hello")
    out = capsys.readouterr().out
    assert "cache read 6,013" in out
    assert "in 412" in out
    assert "out 87" in out
    assert "1 call" in out


def test_usage_is_summed_across_the_hops_of_a_tool_turn(brain_with, monkeypatch, capsys):
    """Each hop re-sends the whole prefix, so per-call numbers mislead."""
    brain, _ = brain_with(
        _tool_turn(_ToolUseBlock("get_calendar", "tu1"), usage=_Usage(uncached=100, cache_read=6000, output=20)),
        _final("done", usage=_Usage(uncached=150, cache_read=6000, output=30)),
    )
    monkeypatch.setattr(brain, "_execute_tool", lambda name, inp: "ok")
    brain.process("what's on today")
    out = capsys.readouterr().out
    assert "2 calls" in out
    assert "in 250" in out          # 100 + 150
    assert "cache read 12,000" in out  # 6000 + 6000
    assert "out 50" in out          # 20 + 30


def test_the_first_turn_shows_a_cache_write(brain_with, capsys):
    """Writing the cache is what the first turn does; reads start after."""
    brain, _ = brain_with(
        _final("hi", usage=_Usage(uncached=400, cache_write=6013, output=20))
    )
    brain.process("hello")
    out = capsys.readouterr().out
    assert "cache write 6,013" in out
    assert "cache read 0 (0%)" in out


def test_the_reported_percentage_is_the_share_served_from_cache(brain_with, capsys):
    brain, _ = brain_with(
        _final("hi", usage=_Usage(uncached=1000, cache_read=9000, output=10))
    )
    brain.process("hello")
    assert "(90%)" in capsys.readouterr().out


def test_usage_reporting_can_be_silenced(brain_with, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_QUIET_USAGE", "1")
    brain, _ = brain_with(_final("hi", usage=_Usage(uncached=1, cache_read=2)))
    brain.process("hello")
    assert capsys.readouterr().out == ""


def test_a_response_without_usage_prints_nothing(brain_with, capsys):
    """Older SDKs or a stubbed client may not carry usage; that must not raise."""
    brain, _ = brain_with(_final("hi"))
    brain.process("hello")
    assert capsys.readouterr().out == ""


def test_usage_survives_the_tool_loop_running_out(brain_with, monkeypatch, capsys):
    """The safety-net exit reports too, so a runaway turn still shows its cost."""
    blocks = [_ToolUseBlock("get_calendar", "tu1")]
    brain, _ = brain_with(
        *[_tool_turn(*blocks, usage=_Usage(uncached=10, cache_read=100, output=5))
          for _ in range(8)]
    )
    monkeypatch.setattr(brain, "_execute_tool", lambda name, inp: "ok")
    reply = brain.process("loop forever")
    assert "rephrase" in reply
    out = capsys.readouterr().out
    assert "8 calls" in out
    assert "cache read 800" in out
