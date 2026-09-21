"""Tests for JarvisBrain._execute_tool — the router between Claude's tool
calls and the backend modules.

Every tool Claude can invoke lands here, so this covers the properties that
hold across all of them: failures stay contained, unknown names are reported,
offline mode short-circuits internet tools, and sending a message to a real
human requires explicit confirmation.
"""

import pytest

import threading

import calendar_module
import claude_client
import home_module
import messages_module
import offline_module
import spotify_module


@pytest.fixture
def brain():
    """A JarvisBrain with no network use — only _execute_tool is exercised."""
    return claude_client.JarvisBrain()


# --- failure containment ----------------------------------------------------


def test_an_unknown_tool_is_reported_rather_than_raising(brain):
    assert brain._execute_tool("teleport", {}) == "Unknown tool: teleport"


def test_a_module_exception_is_turned_into_text(brain, monkeypatch):
    """The tool loop must survive a broken bridge — an exception escaping here
    would kill the whole turn."""

    def explode():
        raise RuntimeError("Calendar is on fire")

    monkeypatch.setattr(calendar_module, "get_today_events", explode)
    out = brain._execute_tool("get_calendar", {"range": "today"})
    assert "failed" in out
    assert "Calendar is on fire" in out


def test_every_dispatch_result_is_a_string(brain, monkeypatch):
    """Claude's tool_result content must be text, never a list or None."""
    monkeypatch.setattr(calendar_module, "get_today_events", lambda: ["Standup at 9"])
    for name, payload in [
        ("get_calendar", {"range": "today"}),
        ("unknown_tool", {}),
    ]:
        assert isinstance(brain._execute_tool(name, payload), str)


@pytest.mark.parametrize("payload", [None, {}])
def test_a_missing_tool_input_falls_back_to_defaults(brain, monkeypatch, payload):
    """Claude sometimes omits optional arguments entirely."""
    monkeypatch.setattr(calendar_module, "get_today_events", lambda: ["Standup"])
    assert "Standup" in brain._execute_tool("get_calendar", payload)


# --- routing ----------------------------------------------------------------


def test_the_calendar_range_selects_the_right_query(brain, monkeypatch):
    monkeypatch.setattr(calendar_module, "get_today_events", lambda: ["today event"])
    monkeypatch.setattr(calendar_module, "get_week_events", lambda: ["week event"])
    assert "today event" in brain._execute_tool("get_calendar", {"range": "today"})
    assert "week event" in brain._execute_tool("get_calendar", {"range": "week"})


def test_the_calendar_defaults_to_today(brain, monkeypatch):
    monkeypatch.setattr(calendar_module, "get_today_events", lambda: ["today event"])
    monkeypatch.setattr(calendar_module, "get_week_events", lambda: ["week event"])
    assert "today event" in brain._execute_tool("get_calendar", {})


@pytest.mark.parametrize(
    "action,expected_call",
    [("pause", "pause"), ("resume", "resume"), ("skip", "skip")],
)
def test_playback_actions_route_to_the_matching_call(
    brain, monkeypatch, action, expected_call
):
    called = []
    for name in ("pause", "resume", "skip"):
        monkeypatch.setattr(
            spotify_module, name, lambda n=name: called.append(n) or f"did {n}"
        )
    brain._execute_tool("spotify_playback", {"action": action})
    assert called == [expected_call]


# --- confirmation before messaging a human ---------------------------------


@pytest.mark.parametrize(
    "tool,module_fn",
    [("send_imessage", "send_imessage"), ("send_whatsapp", "send_whatsapp")],
)
def test_sending_a_message_without_confirmation_does_not_send(
    brain, monkeypatch, tool, module_fn
):
    """A misheard command must never reach a real contact. Without the
    confirmed flag the module call has to be skipped entirely."""
    sent = []
    monkeypatch.setattr(
        messages_module, module_fn, lambda *a, **k: sent.append(a) or "SENT"
    )
    out = brain._execute_tool(tool, {"contact": "Mum", "message": "on my way"})
    assert sent == []
    assert "confirm" in out.lower()


@pytest.mark.parametrize(
    "tool,module_fn",
    [("send_imessage", "send_imessage"), ("send_whatsapp", "send_whatsapp")],
)
def test_the_confirmation_prompt_quotes_the_message_and_contact(
    brain, monkeypatch, tool, module_fn
):
    monkeypatch.setattr(messages_module, module_fn, lambda *a, **k: "SENT")
    out = brain._execute_tool(tool, {"contact": "Mum", "message": "on my way"})
    assert "Mum" in out and "on my way" in out


@pytest.mark.parametrize(
    "tool,module_fn",
    [("send_imessage", "send_imessage"), ("send_whatsapp", "send_whatsapp")],
)
def test_sending_a_message_with_confirmation_goes_through(
    brain, monkeypatch, tool, module_fn
):
    sent = []
    monkeypatch.setattr(
        messages_module,
        module_fn,
        lambda contact, message: sent.append((contact, message)) or "Sent.",
    )
    out = brain._execute_tool(
        tool, {"contact": "Mum", "message": "on my way", "confirmed": True}
    )
    assert sent == [("Mum", "on my way")]
    assert out == "Sent."


@pytest.mark.parametrize("falsy", [False, None, 0, ""])
def test_a_falsy_confirmation_flag_still_blocks_the_send(brain, monkeypatch, falsy):
    sent = []
    monkeypatch.setattr(
        messages_module, "send_imessage", lambda *a, **k: sent.append(a) or "SENT"
    )
    brain._execute_tool(
        "send_imessage",
        {"contact": "Mum", "message": "hi", "confirmed": falsy},
    )
    assert sent == []


# --- home control -----------------------------------------------------------


def test_listing_home_devices_routes_to_the_module(brain, monkeypatch):
    monkeypatch.setattr(
        home_module, "list_home_devices", lambda: "I can run 2 home controls: A, B."
    )
    assert "2 home controls" in brain._execute_tool("list_home_devices", {})


def test_home_control_passes_the_name_through(brain, monkeypatch):
    seen = []
    monkeypatch.setattr(
        home_module,
        "run_home_shortcut",
        lambda name, confirmed=False: seen.append((name, confirmed)) or "Done.",
    )
    brain._execute_tool("home_control", {"name": "Movie Time"})
    assert seen == [("Movie Time", False)]


def test_home_control_forwards_an_explicit_confirmation(brain, monkeypatch):
    seen = []
    monkeypatch.setattr(
        home_module,
        "run_home_shortcut",
        lambda name, confirmed=False: seen.append((name, confirmed)) or "Done.",
    )
    brain._execute_tool(
        "home_control", {"name": "Unlock Front Door", "confirmed": True}
    )
    assert seen == [("Unlock Front Door", True)]


@pytest.mark.parametrize("falsy", [False, None, 0, "", "no"])
def test_only_a_real_confirmation_reaches_the_module(brain, monkeypatch, falsy):
    """Anything Claude sends that isn't truthy must arrive as confirmed=False,
    so the module's own lock gate still applies."""
    seen = []
    monkeypatch.setattr(
        home_module,
        "run_home_shortcut",
        lambda name, confirmed=False: seen.append(confirmed) or "Done.",
    )
    brain._execute_tool(
        "home_control", {"name": "Unlock Front Door", "confirmed": falsy}
    )
    assert seen == [bool(falsy)]


def test_an_unconfirmed_lock_is_refused_end_to_end(brain, monkeypatch):
    """Dispatch + module together: the Shortcuts CLI is never invoked."""
    ran = []
    monkeypatch.setattr(
        home_module,
        "_run_shortcuts",
        lambda args, timeout=None: ran.append(args)
        or (("Unlock Front Door\n", None) if args[0] == "list" else ("", None)),
    )
    out = brain._execute_tool("home_control", {"name": "unlock front door"})
    assert "confirm" in out.lower()
    assert [a for a in ran if a[0] == "run"] == []


def test_home_control_still_works_while_offline(brain, offline, monkeypatch):
    """HomeKit is local, so offline mode must not short-circuit it."""
    monkeypatch.setattr(
        home_module, "run_home_shortcut", lambda name, confirmed=False: "Done."
    )
    assert brain._execute_tool("home_control", {"name": "Movie Time"}) == "Done."


# --- offline degradation ----------------------------------------------------


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setitem(offline_module._state, "offline", True)


@pytest.mark.parametrize(
    "tool", ["search_web", "get_news", "search_wikipedia", "get_weather"]
)
def test_internet_tools_short_circuit_when_offline(brain, offline, tool):
    """These must not attempt a request — the autouse network guard would
    fail the test if they did."""
    out = brain._execute_tool(tool, {"query": "anything"})
    assert "offline" in out.lower()


def test_weather_offline_serves_the_last_known_value(brain, offline):
    offline_module.remember("weather", "18C and clear")
    out = brain._execute_tool("get_weather", {})
    assert "18C and clear" in out
    assert "Offline" in out


def test_currency_offline_serves_the_last_known_rate(brain, offline):
    offline_module.remember("currency", "1 EUR = 25 CZK")
    assert "25 CZK" in brain._execute_tool(
        "convert_currency", {"amount": 10, "from_currency": "EUR", "to_currency": "CZK"}
    )


def test_currency_offline_without_a_cached_rate_says_so(brain, offline):
    out = brain._execute_tool(
        "convert_currency", {"amount": 10, "from_currency": "EUR", "to_currency": "CZK"}
    )
    assert "unavailable while you're offline" in out


def test_translation_offline_uses_the_phrase_book(brain, offline):
    out = brain._execute_tool(
        "translate", {"text": "thank you", "target_language": "italian"}
    )
    assert "grazie" in out


def test_translation_offline_outside_the_phrase_book_says_so(brain, offline):
    out = brain._execute_tool(
        "translate", {"text": "quantum entanglement", "target_language": "italian"}
    )
    assert "unavailable offline" in out


def test_local_tools_still_work_while_offline(brain, offline, monkeypatch):
    """Offline mode must only short-circuit internet-dependent tools."""
    monkeypatch.setattr(calendar_module, "get_today_events", lambda: ["Standup at 9"])
    assert "Standup at 9" in brain._execute_tool("get_calendar", {"range": "today"})


def test_internet_tools_run_normally_when_online(brain, monkeypatch):
    monkeypatch.setattr(
        claude_client.browser_module, "search_web", lambda q: [{"title": "hit"}]
    )
    assert "hit" in brain._execute_tool("search_web", {"query": "python"})


# --- parallel tool execution ------------------------------------------------


class _ToolBlock:
    """Minimal stand-in for the SDK's tool_use content block."""

    type = "tool_use"

    def __init__(self, name, tool_input=None, block_id=None):
        self.name = name
        self.input = tool_input or {}
        self.id = block_id or f"tu_{name}"


def test_results_come_back_in_request_order(brain, monkeypatch):
    """tool_result blocks are matched to tool_use ids by position."""
    monkeypatch.setattr(
        brain, "_execute_tool", lambda name, inp: f"result of {name}"
    )
    blocks = [_ToolBlock(f"tool_{i}") for i in range(4)]
    assert brain._execute_tools(blocks) == [f"result of tool_{i}" for i in range(4)]


def test_several_tools_really_do_run_at_the_same_time(brain, monkeypatch):
    """The barrier only releases once all of them are in flight together — if
    execution were sequential the first would wait there until it times out.

    Its size is derived from the block list so the two can't drift apart; a
    hand-written count would turn an edited list into a five-second hang
    instead of a clear failure.
    """
    blocks = [_ToolBlock("a"), _ToolBlock("b"), _ToolBlock("c")]
    barrier = threading.Barrier(len(blocks), timeout=5)

    def gated(name, inp):
        barrier.wait()
        return f"ran {name}"

    monkeypatch.setattr(brain, "_execute_tool", gated)
    assert brain._execute_tools(blocks) == ["ran a", "ran b", "ran c"]


def test_a_single_tool_runs_inline(brain, monkeypatch):
    """No thread pool for the common one-tool turn."""
    seen = []
    monkeypatch.setattr(
        brain,
        "_execute_tool",
        lambda name, inp: seen.append(threading.current_thread().name) or "ok",
    )
    brain._execute_tools([_ToolBlock("only")])
    assert seen == [threading.current_thread().name]


def test_no_tools_is_an_empty_result(brain):
    assert brain._execute_tools([]) == []


def test_one_failing_tool_does_not_take_down_the_others(brain, monkeypatch):
    real = brain._execute_tool

    def sometimes_explodes(name, inp):
        if name == "get_calendar":
            raise RuntimeError("bridge died")
        return "fine"

    monkeypatch.setattr(brain, "_execute_tool", sometimes_explodes)
    blocks = [_ToolBlock("get_emails"), _ToolBlock("get_calendar")]
    with pytest.raises(RuntimeError):
        brain._execute_tools(blocks)
    # ...but through the real dispatcher, the failure is already text:
    monkeypatch.setattr(brain, "_execute_tool", real)
    monkeypatch.setattr(
        calendar_module, "get_today_events", lambda: (_ for _ in ()).throw(
            RuntimeError("bridge died")
        )
    )
    results = brain._execute_tools(
        [_ToolBlock("get_calendar", {"range": "today"}), _ToolBlock("unknown_tool")]
    )
    assert "failed" in results[0]
    assert results[1] == "Unknown tool: unknown_tool"


def test_concurrency_is_bounded(brain, monkeypatch):
    """A turn with many tool calls must not spawn an unbounded thread pool."""
    live = []
    peak = []
    lock = threading.Lock()

    def tracked(name, inp):
        with lock:
            live.append(name)
            peak.append(len(live))
        threading.Event().wait(0.01)
        with lock:
            live.remove(name)
        return "ok"

    monkeypatch.setattr(brain, "_execute_tool", tracked)
    brain._execute_tools([_ToolBlock(f"t{i}") for i in range(12)])
    assert max(peak) <= claude_client.MAX_PARALLEL_TOOLS


# --- history trimming -------------------------------------------------------


def _turn(text):
    return {"role": "user", "content": text}


def _tool_exchange(n=1):
    """One assistant tool_use turn plus its tool_result reply."""
    return [
        {"role": "assistant", "content": [{"type": "tool_use"}] * n},
        {"role": "user", "content": [{"type": "tool_result"}] * n},
    ]


def test_history_under_the_cap_is_left_alone(brain):
    brain.history = [_turn(f"msg {i}") for i in range(5)]
    brain._trim()
    assert len(brain.history) == 5


def test_history_is_capped_by_conversational_turns(brain):
    brain.history = [_turn(f"msg {i}") for i in range(claude_client.MAX_TURNS + 10)]
    brain._trim()
    assert len(brain.history) == claude_client.MAX_TURNS
    assert brain.history[0]["content"] == "msg 10"


def test_tool_heavy_turns_no_longer_evict_the_conversation(brain):
    """The regression this replaced: counting raw messages meant a few
    tool-heavy turns pushed the actual dialogue out of the window."""
    brain.history = []
    for i in range(4):
        brain.history.append(_turn(f"question {i}"))
        brain.history.extend(_tool_exchange(3))
        brain.history.append({"role": "assistant", "content": f"answer {i}"})
    brain._trim()
    spoken = [m["content"] for m in brain.history if brain._is_user_turn(m)]
    assert spoken == [f"question {i}" for i in range(4)]


def test_whole_turns_are_dropped_together(brain):
    """Cutting mid-turn would orphan a tool_result from its tool_use."""
    brain.history = []
    for i in range(claude_client.MAX_TURNS + 3):
        brain.history.append(_turn(f"q{i}"))
        brain.history.extend(_tool_exchange())
    brain._trim()
    assert brain._is_user_turn(brain.history[0])
    assert len(brain._turn_starts()) == claude_client.MAX_TURNS


def test_trimming_never_leaves_a_dangling_tool_result_first(brain):
    """A history starting on a tool_result (or an assistant turn) is rejected
    by the API — the trim has to drop them."""
    brain.history = _tool_exchange() * claude_client.MAX_HISTORY
    brain.history.append(_turn("a real question"))
    brain._trim()
    assert brain.history
    assert brain._is_user_turn(brain.history[0])


def test_the_absolute_ceiling_drops_turns_before_max_turns_is_reached(brain):
    """MAX_HISTORY is the backstop for tool-heavy turns: once the message
    count is over it, older turns go even though fewer than MAX_TURNS remain.

    The tool loop is bounded at 8 iterations, so one turn tops out around 17
    messages — the ceiling is only ever reached by several of them together.
    """
    brain.history = []
    for i in range(claude_client.MAX_TURNS):
        brain.history.append(_turn(f"q{i}"))
        brain.history.extend(_tool_exchange() * 8)  # the tool-loop bound
    brain._trim()
    assert len(brain.history) <= claude_client.MAX_HISTORY
    assert len(brain._turn_starts()) < claude_client.MAX_TURNS


def test_the_ceiling_bottoms_out_at_one_turn(brain):
    """It can never cut into the live turn: dropping an assistant tool_use
    without its tool_result would make the history unsendable."""
    brain.history = [_turn("q")] + _tool_exchange() * 100
    brain._trim()
    assert brain._turn_starts() == [0]
    assert len(brain.history) > claude_client.MAX_HISTORY  # kept whole anyway


def test_the_most_recent_turn_is_never_dropped(brain):
    """Even one oversized turn has to survive — it is the live conversation."""
    brain.history = [_turn("the only question")]
    brain.history.extend(_tool_exchange() * 200)
    brain._trim()
    assert brain._turn_starts() == [0]
    assert brain.history[0]["content"] == "the only question"
