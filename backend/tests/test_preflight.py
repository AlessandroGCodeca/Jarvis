"""Tests for the boot-time credential checks.

A wrong key used to be invisible until first use — and each service hides it
differently, so "why is it silent?" cost a debugging session per key. These
cover what the preflight says about each credential, and (just as important)
what it *doesn't* say: an unreachable service or a 5xx must never be reported
as a bad key, and no probe may be able to hold or crash the boot.
"""

import asyncio
import base64
import json

import anthropic
import httpx
import pytest

import claude_client
import offline_module
import preflight


# --- stand-ins --------------------------------------------------------------


class _FakeResponse:
    """The part of an httpx.Response the checks actually touch."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("response body is not JSON")
        return self._payload


class _FakeHttp:
    """Stand-in for httpx.AsyncClient, routing by URL fragment."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []  # (method, url, kwargs) per request made

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, outcome in self.routes.items():
            if fragment in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"unrouted request: {method} {url}")

    def urls(self):
        return [url for _, url, _ in self.calls]


class _FakeModels:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requested = []

    async def retrieve(self, model_id, **kwargs):
        self.requested.append(model_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeAnthropic:
    def __init__(self, outcome):
        self.models = _FakeModels(outcome)
        self.closed = False

    async def close(self):
        self.closed = True


class _ModelInfo:
    def __init__(self, model_id):
        self.id = model_id


def _api_error(cls, status, message):
    """A real SDK exception, carrying a real Anthropic error body."""
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": message},
    }
    response = httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "https://api.anthropic.com/v1/models/x"),
    )
    # The SDK's own message is the dict repr — exactly what we don't print.
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def _run(coro):
    return asyncio.run(coro)


def _by_label(checks):
    return {check.label: check for check in checks}


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    """Start every test from an environment with nothing set."""
    for name in (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "JARVIS_SKIP_PREFLIGHT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def http(monkeypatch):
    """Install a routed stand-in for every outbound HTTP call."""

    def _install(routes=None):
        fake = _FakeHttp(routes or {})
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
        return fake

    return _install


@pytest.fixture
def anthropic_api(monkeypatch):
    """Make the Anthropic client return (or raise) whatever a test wants."""

    def _install(outcome):
        fake = _FakeAnthropic(outcome)
        monkeypatch.setattr(preflight, "_new_anthropic_client", lambda: fake)
        return fake

    return _install


# --- Anthropic --------------------------------------------------------------


def test_a_missing_key_fails_before_any_request(monkeypatch):
    """Nothing to validate, and the row says which variable is empty."""

    def _never(*args, **kwargs):
        raise AssertionError("no client should be built without a key")

    monkeypatch.setattr(preflight, "_new_anthropic_client", _never)

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.FAIL
    assert "ANTHROPIC_API_KEY" in check.detail


def test_an_empty_key_counts_as_missing(monkeypatch, anthropic_api):
    """CI exports ANTHROPIC_API_KEY="" — set, but no more useful than unset."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    anthropic_api(_ModelInfo("claude-haiku-4-5"))

    assert _run(preflight.check_anthropic()).status == preflight.FAIL


def test_a_valid_key_reports_the_model(monkeypatch, anthropic_api):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(claude_client, "MODEL", "claude-haiku-4-5")
    anthropic_api(_ModelInfo("claude-haiku-4-5"))

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.OK
    assert check.detail == "key valid, model claude-haiku-4-5"


def test_an_alias_shows_what_it_resolved_to(monkeypatch, anthropic_api):
    """Which build answered is worth knowing when replies change character."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(claude_client, "MODEL", "claude-haiku-4-5")
    anthropic_api(_ModelInfo("claude-haiku-4-5-20251001"))

    check = _run(preflight.check_anthropic())
    assert check.detail == (
        "key valid, model claude-haiku-4-5 → claude-haiku-4-5-20251001"
    )


def test_the_model_checked_is_the_one_the_brain_will_send(
    monkeypatch, anthropic_api
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(claude_client, "MODEL", "claude-opus-4-1")
    fake = anthropic_api(_ModelInfo("claude-opus-4-1"))

    _run(preflight.check_anthropic())
    assert fake.models.requested == ["claude-opus-4-1"]


def test_a_rejected_key_reports_the_api_message(monkeypatch, anthropic_api):
    """The sentence from the body, not the SDK's dict repr of it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")
    anthropic_api(
        _api_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    )

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.FAIL
    assert check.detail == "HTTP 401 — invalid x-api-key"


def test_an_unknown_model_names_the_variable(monkeypatch, anthropic_api):
    """A typo in CLAUDE_MODEL fails at the first message today."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(claude_client, "MODEL", "claude-haiku-4.5")
    anthropic_api(_api_error(anthropic.NotFoundError, 404, "not_found"))

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.FAIL
    assert "CLAUDE_MODEL" in check.detail
    assert "claude-haiku-4.5" in check.detail


def test_an_unreachable_api_is_not_a_bad_key(monkeypatch, anthropic_api):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    anthropic_api(anthropic.APIConnectionError(request=None))

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.UNKNOWN
    assert "couldn't check" in check.detail


def test_a_server_error_is_not_a_bad_key(monkeypatch, anthropic_api):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    anthropic_api(_api_error(anthropic.APIStatusError, 503, "overloaded"))

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.UNKNOWN


def test_an_unexpected_status_is_reported_with_its_reason(
    monkeypatch, anthropic_api
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    anthropic_api(
        _api_error(anthropic.APIStatusError, 429, "rate limit exceeded")
    )

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.FAIL
    assert check.detail == "HTTP 429 — rate limit exceeded"


@pytest.mark.parametrize(
    "outcome",
    [
        _ModelInfo("claude-haiku-4-5"),
        _api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"),
    ],
    ids=["success", "failure"],
)
def test_the_client_is_closed_either_way(monkeypatch, anthropic_api, outcome):
    """A leaked connection pool at boot would outlive every request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    fake = anthropic_api(outcome)

    _run(preflight.check_anthropic())
    assert fake.closed is True


def test_the_real_client_cannot_reach_the_network_from_a_test(monkeypatch):
    """The conftest guard covers the SDK's vendored httpx too.

    Without it, a check that forgot its stand-in would quietly call the real
    API with whatever key the machine running the suite happens to hold.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")

    check = _run(preflight.check_anthropic())
    assert check.status == preflight.UNKNOWN


# --- ElevenLabs -------------------------------------------------------------


def test_no_elevenlabs_config_is_a_documented_fallback(http):
    """Unconfigured is a supported mode, not a mistake: macOS `say` speaks."""
    http()

    checks = _run(preflight.check_elevenlabs())
    assert [c.status for c in checks] == [preflight.UNKNOWN]
    assert "say" in checks[0].detail


def test_a_voice_without_a_key_is_a_failure(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    http()

    checks = _run(preflight.check_elevenlabs())
    assert checks[0].status == preflight.FAIL
    assert "ELEVENLABS_API_KEY" in checks[0].detail


def test_a_key_without_a_voice_is_a_failure(monkeypatch, http):
    """Cloud speech needs both; half of it is silence with a paid key."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    http({"user/subscription": _FakeResponse(200, {})})

    checks = _by_label(_run(preflight.check_elevenlabs()))
    assert checks["ElevenLabs"].status == preflight.OK
    assert checks["Voice ID"].status == preflight.FAIL
    assert "ELEVENLABS_VOICE_ID" in checks["Voice ID"].detail


def test_a_working_key_and_voice(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "d3VVY8daAbCdEf")
    http(
        {
            "user/subscription": _FakeResponse(200, {}),
            "voices/": _FakeResponse(200, {"name": "El Flash V2"}),
        }
    )

    checks = _by_label(_run(preflight.check_elevenlabs()))
    assert checks["ElevenLabs"].detail == "key valid"
    assert checks["Voice ID"].status == preflight.OK
    assert checks["Voice ID"].detail == 'd3VVY8da... resolves to "El Flash V2"'


def test_the_remaining_quota_is_reported(monkeypatch, http):
    """An exhausted quota is a 429 mid-reply — indistinguishable from a bad key."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    http(
        {
            "user/subscription": _FakeResponse(
                200, {"character_count": 12345, "character_limit": 100000}
            )
        }
    )

    check = _run(preflight._check_elevenlabs_key("sk_real"))
    assert check.detail == "key valid, 12,345 of 100,000 characters used"


def test_the_key_id_mistake_is_spelled_out(monkeypatch, http):
    """The one that cost a debugging session: the key ID pasted as the key."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "abc123")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    http(
        {
            "user/subscription": _FakeResponse(
                400,
                {
                    "detail": {
                        "status": "invalid_api_key",
                        "message": (
                            "API key ID used as API key — only valid API keys "
                            "can be used. API keys start with 'sk_'."
                        ),
                    }
                },
            )
        }
    )

    checks = _by_label(_run(preflight.check_elevenlabs()))
    assert checks["ElevenLabs"].status == preflight.FAIL
    assert checks["ElevenLabs"].detail.startswith("HTTP 400 — API key ID used")
    assert "sk_" in checks["ElevenLabs"].detail


def test_a_bad_key_does_not_also_blame_the_voice(monkeypatch, http):
    """One root cause, one ✗ — and one request, since the second is pointless."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_wrong")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    fake = http({"user/subscription": _FakeResponse(401, {"detail": "bad key"})})

    checks = _by_label(_run(preflight.check_elevenlabs()))
    assert checks["Voice ID"].status == preflight.UNKNOWN
    assert len(fake.calls) == 1


def test_an_unknown_voice_names_the_id(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "stale-voice")
    http(
        {
            "user/subscription": _FakeResponse(200, {}),
            "voices/": _FakeResponse(404, {"detail": "voice not found"}),
        }
    )

    check = _by_label(_run(preflight.check_elevenlabs()))["Voice ID"]
    assert check.status == preflight.FAIL
    assert "stale-voice" in check.detail


def test_an_elevenlabs_outage_is_not_a_bad_key(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    http({"user/subscription": _FakeResponse(503, text="Service Unavailable")})

    check = _run(preflight._check_elevenlabs_key("sk_real"))
    assert check.status == preflight.UNKNOWN
    assert "503" in check.detail


def test_a_moved_endpoint_is_not_a_bad_key(monkeypatch, http):
    """If the probe itself is wrong, don't send someone to re-issue a key."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    http({"user/subscription": _FakeResponse(404, text="Not Found")})

    check = _run(preflight._check_elevenlabs_key("sk_real"))
    assert check.status == preflight.UNKNOWN
    assert "404" in check.detail


def test_a_network_error_is_not_a_bad_key(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    http({"user/subscription": httpx.ConnectError("name resolution failed")})

    check = _run(preflight._check_elevenlabs_key("sk_real"))
    assert check.status == preflight.UNKNOWN
    assert "ConnectError" in check.detail


def test_the_key_is_sent_as_elevenlabs_expects(monkeypatch, http):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    fake = http({"user/subscription": _FakeResponse(200, {})})

    _run(preflight._check_elevenlabs_key("sk_real"))
    _, _, kwargs = fake.calls[0]
    assert kwargs["headers"] == {"xi-api-key": "sk_real"}


# --- Spotify ----------------------------------------------------------------


def test_spotify_unconfigured_is_fine(http):
    http()

    check = _run(preflight.check_spotify())
    assert check.status == preflight.UNKNOWN
    assert "search" in check.detail


@pytest.mark.parametrize(
    "present, missing",
    [
        ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"),
        ("SPOTIFY_CLIENT_SECRET", "SPOTIFY_CLIENT_ID"),
    ],
)
def test_half_configured_spotify_names_the_missing_half(
    monkeypatch, http, present, missing
):
    monkeypatch.setenv(present, "value")
    http()

    check = _run(preflight.check_spotify())
    assert check.status == preflight.FAIL
    assert missing in check.detail


def test_valid_spotify_credentials(monkeypatch, http):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    fake = http({"accounts.spotify.com": _FakeResponse(200, {"access_token": "t"})})

    check = _run(preflight.check_spotify())
    assert check.status == preflight.OK

    _, _, kwargs = fake.calls[0]
    expected = base64.b64encode(b"id:secret").decode()
    assert kwargs["headers"]["Authorization"] == f"Basic {expected}"
    assert kwargs["data"] == {"grant_type": "client_credentials"}


def test_rejected_spotify_credentials(monkeypatch, http):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "wrong")
    http(
        {
            "accounts.spotify.com": _FakeResponse(
                400,
                {"error": "invalid_client", "error_description": "Invalid client"},
            )
        }
    )

    check = _run(preflight.check_spotify())
    assert check.status == preflight.FAIL
    assert check.detail == "HTTP 400 — Invalid client"


def test_a_spotify_error_without_json_still_reports_something(monkeypatch, http):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "wrong")
    http({"accounts.spotify.com": _FakeResponse(403, text="Forbidden")})

    check = _run(preflight.check_spotify())
    assert check.detail == "HTTP 403 — Forbidden"


# --- the report -------------------------------------------------------------


def test_the_report_is_aligned_and_marked():
    lines = preflight.format_report(
        [
            preflight.Check("Anthropic", preflight.OK, "key valid"),
            preflight.Check("ElevenLabs", preflight.FAIL, "HTTP 401 — nope"),
            preflight.Check("Voice ID", preflight.UNKNOWN, "not checked"),
        ]
    )
    assert lines[0] == "JARVIS preflight:"
    assert lines[1] == "  ✓ Anthropic   key valid"
    assert lines[2] == "  ✗ ElevenLabs  HTTP 401 — nope"
    assert lines[3] == "  – Voice ID    not checked"


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ([preflight.OK, preflight.UNKNOWN], None),
        ([preflight.FAIL, preflight.OK], "1 check failed"),
        ([preflight.FAIL, preflight.FAIL], "2 checks failed"),
    ],
)
def test_failures_are_counted_in_a_closing_line(statuses, expected):
    checks = [preflight.Check("X", status, "why") for status in statuses]
    last = preflight.format_report(checks)[-1]
    if expected is None:
        assert "failed" not in last
    else:
        assert last.strip().startswith(expected)


def test_an_empty_report_prints_nothing():
    assert preflight.format_report([]) == []


# --- running the lot --------------------------------------------------------


def test_rows_keep_a_fixed_order_whatever_finishes_first(monkeypatch, http):
    """Concurrent checks, deterministic output."""

    async def _slow():
        await asyncio.sleep(0.02)
        return preflight.Check("Anthropic", preflight.OK, "key valid")

    monkeypatch.setattr(preflight, "check_anthropic", _slow)
    http()

    labels = [check.label for check in _run(preflight.run_checks())]
    assert labels == ["Anthropic", "ElevenLabs", "Spotify"]


def test_a_hanging_service_cannot_hold_the_boot(monkeypatch, http):
    async def _hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(preflight, "CHECK_TIMEOUT", 0.01)
    monkeypatch.setattr(preflight, "check_anthropic", _hang)
    http()

    check = _by_label(_run(preflight.run_checks()))["Anthropic"]
    assert check.status == preflight.UNKNOWN
    assert "timed out" in check.detail


def test_a_broken_check_cannot_crash_the_boot(monkeypatch, http):
    async def _boom():
        raise RuntimeError("bug in the check itself")

    monkeypatch.setattr(preflight, "check_spotify", _boom)
    http()

    checks = _by_label(_run(preflight.run_checks()))
    assert checks["Spotify"].status == preflight.UNKNOWN
    assert "RuntimeError" in checks["Spotify"].detail
    # The other rows still made it through.
    assert "Anthropic" in checks and "ElevenLabs" in checks


def test_run_prints_the_report(monkeypatch, http, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_real")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    http(
        {
            "user/subscription": _FakeResponse(200, {}),
            "voices/": _FakeResponse(200, {"name": "El Flash V2"}),
        }
    )

    checks = _run(preflight.run())
    printed = capsys.readouterr().out
    assert "JARVIS preflight:" in printed
    assert 'resolves to "El Flash V2"' in printed
    # ANTHROPIC_API_KEY is unset here, so the boot says so out loud.
    assert "✗ Anthropic" in printed
    assert "1 check failed" in printed
    assert len(checks) == 4


def test_the_preflight_can_be_skipped(monkeypatch, http, capsys):
    monkeypatch.setenv("JARVIS_SKIP_PREFLIGHT", "1")
    fake = http()

    assert _run(preflight.run()) == []
    assert fake.calls == []
    assert capsys.readouterr().out == ""


def test_offline_skips_the_probes(monkeypatch, http, capsys):
    """Offline they'd all fail for the same uninteresting reason."""
    monkeypatch.setitem(offline_module._state, "offline", True)
    fake = http()

    assert _run(preflight.run()) == []
    assert fake.calls == []
    assert "no internet connection" in capsys.readouterr().out
