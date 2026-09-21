"""Tests for JarvisBrain._execute_tool — the router between Claude's tool
calls and the backend modules.

Every tool Claude can invoke lands here, so this covers the properties that
hold across all of them: failures stay contained, unknown names are reported,
offline mode short-circuits internet tools, and sending a message to a real
human requires explicit confirmation.
"""

import pytest

import calendar_module
import claude_client
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


# --- history trimming -------------------------------------------------------


def test_history_under_the_cap_is_left_alone(brain):
    brain.history = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
    brain._trim()
    assert len(brain.history) == 5


def test_history_is_capped(brain):
    brain.history = [
        {"role": "user", "content": f"msg {i}"}
        for i in range(claude_client.MAX_HISTORY + 10)
    ]
    brain._trim()
    assert len(brain.history) <= claude_client.MAX_HISTORY


def test_trimming_never_leaves_a_dangling_tool_result_first(brain):
    """A history starting on a tool_result (or an assistant turn) is rejected
    by the API — the trim has to drop them."""
    brain.history = [
        {"role": "assistant", "content": [{"type": "tool_use"}]},
        {"role": "user", "content": [{"type": "tool_result"}]},
    ] * (claude_client.MAX_HISTORY)
    brain.history.append({"role": "user", "content": "a real question"})
    brain._trim()
    assert brain.history
    assert brain.history[0]["role"] == "user"
    assert isinstance(brain.history[0]["content"], str)
