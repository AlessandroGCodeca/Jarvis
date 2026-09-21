"""Tests for offline detection and the offline degradation helpers."""

import asyncio

import httpx
import pytest

import offline_module


def test_a_fresh_session_is_assumed_online():
    assert offline_module.is_offline() is False
    assert offline_module.status_label() == "ONLINE"


def test_the_status_label_flags_being_offline():
    offline_module._state["offline"] = True
    assert offline_module.is_offline() is True
    assert "OFFLINE" in offline_module.status_label()


# --- the last-known-good cache ---------------------------------------------


def test_remember_then_recall():
    offline_module.remember("weather", "18C and clear")
    assert offline_module.recall("weather") == "18C and clear"


def test_recall_of_an_unset_key_returns_the_default():
    assert offline_module.recall("never-set", "fallback") == "fallback"


def test_recall_of_an_unset_key_with_no_default_is_none():
    assert offline_module.recall("never-set") is None


def test_remember_overwrites_the_previous_value():
    offline_module.remember("weather", "old")
    offline_module.remember("weather", "new")
    assert offline_module.recall("weather") == "new"


def test_the_cache_does_not_leak_between_tests():
    """Pairs with the test above: if isolation broke, "weather" would still
    hold "new" here."""
    assert offline_module.recall("weather") is None


# --- the offline phrase book ------------------------------------------------


@pytest.mark.parametrize(
    "text,code,expected",
    [
        ("hello", "sk", "ahoj"),
        ("hello", "it", "ciao"),
        ("hello", "cs", "ahoj"),
        ("thank you", "it", "grazie"),
        ("good morning", "cs", "dobré ráno"),
        ("how are you", "it", "come stai"),
    ],
)
def test_common_phrases_translate_offline(text, code, expected):
    assert offline_module.offline_translate(text, code) == expected


@pytest.mark.parametrize("text", ["Hello", "HELLO", "  hello  ", "hello?", "hello!", "hello."])
def test_phrase_lookup_ignores_case_padding_and_punctuation(text):
    assert offline_module.offline_translate(text, "it") == "ciao"


def test_a_phrase_outside_the_book_returns_none():
    assert offline_module.offline_translate("quantum entanglement", "it") is None


def test_a_language_outside_the_book_returns_none():
    assert offline_module.offline_translate("hello", "de") is None


@pytest.mark.parametrize(
    "text,code", [(None, "it"), ("", "it"), ("hello", None), ("hello", "")]
)
def test_missing_arguments_return_none(text, code):
    assert offline_module.offline_translate(text, code) is None


# --- the connectivity probe -------------------------------------------------


def test_a_reachable_host_marks_the_session_online(monkeypatch):
    class _OKClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def head(self, url):
            return "ok"

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _OKClient())
    offline_module._state["offline"] = True  # start pessimistic
    assert asyncio.run(offline_module.check_connectivity()) is True
    assert offline_module.is_offline() is False


def test_an_unreachable_host_marks_the_session_offline(monkeypatch):
    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def head(self, url):
            raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FailingClient())
    assert asyncio.run(offline_module.check_connectivity()) is False
    assert offline_module.is_offline() is True
    assert "OFFLINE" in offline_module.status_label()


def test_the_probe_records_when_it_last_ran(monkeypatch):
    class _OKClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def head(self, url):
            return "ok"

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _OKClient())
    asyncio.run(offline_module.check_connectivity())
    assert offline_module._state["checked_at"] > 0
