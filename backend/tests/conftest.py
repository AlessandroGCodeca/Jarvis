"""Shared fixtures for the JARVIS backend test suite.

Three autouse fixtures make the suite safe to run anywhere — including on the
Mac that JARVIS actually runs on, where the modules under test would otherwise
write to the real Reminders app and the real memory database:

* ``_isolated_state`` points the SQLite store and the preferences file at a
  per-test temporary directory.
* ``_no_applescript`` makes every ``osascript`` call behave exactly as it does
  off-macOS (graceful "not available"), so no test can mutate Calendar,
  Reminders, Notes, Mail or Messages.
* ``_no_network`` turns any unmocked HTTP call into a loud failure, so a test
  can never silently depend on the network.

Tests that need a stubbed HTTP response or AppleScript reply monkeypatch the
specific function they care about; a later monkeypatch wins over these guards.
"""

import os
import subprocess

import pytest

# anthropic.Anthropic() reads this at construction time. A dummy value is
# enough: no test is allowed to reach the network (see _no_network).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

import httpx  # noqa: E402

import memory  # noqa: E402
import offline_module  # noqa: E402
import preferences_module  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Redirect the SQLite DB and preferences file into a temp directory."""
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "test_memory.db"))
    # memory caches "have I created the tables yet" in a module global; reset
    # it so each test re-initialises against its own fresh database file.
    monkeypatch.setattr(memory, "_initialized", False)
    monkeypatch.setattr(
        preferences_module, "_PREFS_PATH", tmp_path / "test_preferences.json"
    )
    # offline_module keeps the connectivity flag and the last-known-good cache
    # in module globals; give each test its own so state can't leak between
    # tests (a stray offline=True would silently reroute tool dispatch).
    monkeypatch.setattr(
        offline_module, "_state", {"offline": False, "checked_at": 0.0}
    )
    monkeypatch.setattr(offline_module, "_cache", {})
    yield


@pytest.fixture(autouse=True)
def _no_applescript(monkeypatch):
    """Make osascript calls fail the way they do off-macOS.

    Every bridge module funnels through ``subprocess.run(["osascript", ...])``
    and already handles FileNotFoundError as "not running on macOS", so raising
    it here exercises the real graceful-degradation path without touching any
    app. Non-osascript subprocess calls pass through untouched.
    """
    real_run = subprocess.run

    def guarded_run(args, *pos, **kwargs):
        argv0 = args[0] if isinstance(args, (list, tuple)) and args else args
        if isinstance(argv0, str) and "osascript" in argv0:
            raise FileNotFoundError("osascript blocked in tests")
        return real_run(args, *pos, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    yield


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Turn any unmocked outbound HTTP call into an explicit test failure."""

    def blocked(*args, **kwargs):
        raise AssertionError(
            "Unmocked network call in a test — monkeypatch the httpx call "
            "this code path uses instead."
        )

    for attr in ("get", "post", "head", "request", "Client", "AsyncClient"):
        monkeypatch.setattr(httpx, attr, blocked, raising=False)
    yield


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin ``time_parser.now_prague()`` to a fixed moment.

    Returns a setter so a test can choose the instant it wants; defaults to
    Wednesday 2026-06-10 12:00, which is mid-week and mid-day so no assertion
    accidentally depends on a weekend or a day boundary.
    """
    import datetime

    import time_parser

    def _set(dt=datetime.datetime(2026, 6, 10, 12, 0)):
        monkeypatch.setattr(time_parser, "now_prague", lambda: dt)
        return dt

    _set()
    return _set
