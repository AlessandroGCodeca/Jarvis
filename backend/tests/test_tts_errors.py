"""Tests for surfacing speech-synthesis failures.

A TTS failure is invisible from the outside: JARVIS answers in text and simply
never speaks. Before these, every exception was swallowed and returned as
None, so diagnosing "why is it silent?" meant hand-writing a probe script
against the ElevenLabs API. The reason now reaches the log.
"""

import httpx
import pytest

import tts_module


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        if self._body:
            yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, *args, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


@pytest.fixture
def elevenlabs(monkeypatch):
    """Configure credentials and stub the HTTP client."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")

    def _install(response=None, raises=None):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kw: _FakeClient(response, raises)
        )

    return _install


# --- extracting the reason --------------------------------------------------


def test_the_message_is_pulled_out_of_an_error_body():
    """The real one that cost a debugging session."""
    resp = _FakeResponse(
        400,
        text='{"detail":{"type":"authentication_error","code":"invalid_api_key",'
        '"message":"API key ID used as API key — only valid API keys can be used."}}',
    )
    assert tts_module._reason(resp) == (
        "API key ID used as API key — only valid API keys can be used."
    )


def test_a_string_detail_is_used_directly():
    resp = _FakeResponse(422, text='{"detail":"voice not found"}')
    assert tts_module._reason(resp) == "voice not found"


def test_a_detail_without_a_message_falls_back_to_its_status():
    resp = _FakeResponse(429, text='{"detail":{"status":"quota_exceeded"}}')
    assert tts_module._reason(resp) == "quota_exceeded"


def test_a_non_json_body_is_passed_through():
    resp = _FakeResponse(502, text="Bad Gateway")
    assert tts_module._reason(resp) == "Bad Gateway"


def test_an_empty_body_still_yields_something():
    assert tts_module._reason(_FakeResponse(500, text="")) == "no details"


def test_a_long_body_is_truncated():
    assert len(tts_module._reason(_FakeResponse(500, text="x" * 900))) <= 200


# --- reporting --------------------------------------------------------------


def test_a_failure_is_printed(capsys):
    tts_module._report_failure("HTTP 400: bad key")
    out = capsys.readouterr().out
    assert "TTS unavailable" in out
    assert "bad key" in out


def test_the_same_failure_is_only_printed_once(capsys):
    """A bad key fails on every sentence of every reply; repeating it would
    bury everything else in the log."""
    for _ in range(5):
        tts_module._report_failure("HTTP 400: bad key")
    assert capsys.readouterr().out.count("TTS unavailable") == 1


def test_a_different_failure_is_printed_separately(capsys):
    tts_module._report_failure("HTTP 400: bad key")
    tts_module._report_failure("HTTP 429: rate limited")
    assert capsys.readouterr().out.count("TTS unavailable") == 2


# --- through the synthesis path ---------------------------------------------


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, '{"detail":{"message":"invalid api key"}}', "invalid api key"),
        (401, '{"detail":{"message":"unauthorized"}}', "unauthorized"),
        (429, '{"detail":{"status":"quota_exceeded"}}', "quota_exceeded"),
    ],
)
def test_an_http_error_is_reported_and_returns_no_audio(
    elevenlabs, capsys, status, body, expected
):
    elevenlabs(response=_FakeResponse(status, text=body))
    assert _run(tts_module._synth_elevenlabs("Hello.")) is None
    out = capsys.readouterr().out
    assert expected in out
    assert str(status) in out


def test_an_empty_audio_response_is_reported(elevenlabs, capsys):
    elevenlabs(response=_FakeResponse(200, body=b""))
    assert _run(tts_module._synth_elevenlabs("Hello.")) is None
    assert "no audio" in capsys.readouterr().out


def test_a_network_error_is_reported(elevenlabs, capsys):
    elevenlabs(raises=httpx.ConnectError("no route to host"))
    assert _run(tts_module._synth_elevenlabs("Hello.")) is None
    out = capsys.readouterr().out
    assert "TTS unavailable" in out
    assert "ConnectError" in out


def test_a_successful_synthesis_says_nothing(elevenlabs, capsys):
    elevenlabs(response=_FakeResponse(200, body=b"ID3audio"))
    assert _run(tts_module._synth_elevenlabs("Hello.")) == b"ID3audio"
    assert capsys.readouterr().out == ""


def test_synthesize_chunk_reports_and_returns_none(elevenlabs, capsys):
    elevenlabs(response=_FakeResponse(400, text='{"detail":{"message":"bad key"}}'))
    assert _run(tts_module.synthesize_chunk("Hello.")) is None
    assert "bad key" in capsys.readouterr().out


def test_synthesize_chunk_encodes_successful_audio(elevenlabs):
    elevenlabs(response=_FakeResponse(200, body=b"ID3audio"))
    import base64

    assert _run(tts_module.synthesize_chunk("Hello.")) == base64.b64encode(
        b"ID3audio"
    ).decode("ascii")


def test_missing_credentials_need_no_request(monkeypatch):
    """Unconfigured is a normal state, not a failure worth reporting."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    assert _run(tts_module._synth_elevenlabs("Hello.")) is None


def _run(coro):
    import asyncio

    return asyncio.run(coro)
