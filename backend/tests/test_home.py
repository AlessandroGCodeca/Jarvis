"""Tests for HomeKit control via the macOS Shortcuts CLI.

The autouse ``_no_os_automation`` guard blocks the real ``shortcuts`` binary,
so nothing here can touch a real accessory; tests that need a particular CLI
result stub ``home_module._run_shortcuts`` and record what it was asked to do.
"""

import pytest

import home_module
import preferences_module


@pytest.fixture
def shortcuts_cli(monkeypatch):
    """Stub the CLI. Returns (calls, configure) — configure(list_output=..., ...)."""
    calls = []
    state = {"list": "Living Room Lights\nGood Night\nMovie Time\n", "run_error": None}

    def fake_run(args, timeout=None):
        calls.append(list(args))
        if args[0] == "list":
            out = state["list"]
            return (out, None) if isinstance(out, str) else (None, "no such folder")
        if args[0] == "run":
            if state["run_error"]:
                return None, state["run_error"]
            return "", None
        return "", None

    monkeypatch.setattr(home_module, "_run_shortcuts", fake_run)

    def configure(**kw):
        state.update(kw)

    return calls, configure


def _run_args(calls):
    """Just the `run` invocations the CLI received."""
    return [c for c in calls if c and c[0] == "run"]


# --- discovery --------------------------------------------------------------


def test_available_devices_lists_the_home_folder(shortcuts_cli):
    calls, _ = shortcuts_cli
    assert home_module.available_devices() == [
        "Living Room Lights",
        "Good Night",
        "Movie Time",
    ]
    assert calls[0] == ["list", "-f", "Home"]


def test_blank_lines_in_the_listing_are_ignored(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(list="Good Night\n\n   \nMovie Time\n")
    assert home_module.available_devices() == ["Good Night", "Movie Time"]


def test_the_folder_comes_from_a_preference(shortcuts_cli):
    calls, _ = shortcuts_cli
    preferences_module.set_preference("home_shortcuts_folder", "Smart Home")
    home_module.available_devices()
    assert calls[0] == ["list", "-f", "Smart Home"]


def test_a_missing_folder_falls_back_to_every_shortcut(monkeypatch):
    """A user who hasn't made a "Home" folder still gets their shortcuts."""
    outputs = iter([(None, "no such folder"), ("Good Night\n", None)])
    seen = []

    def fake_run(args, timeout=None):
        seen.append(list(args))
        return next(outputs)

    monkeypatch.setattr(home_module, "_run_shortcuts", fake_run)
    assert home_module.available_devices() == ["Good Night"]
    assert seen[0] == ["list", "-f", "Home"]
    assert seen[1] == ["list"]  # no folder filter


def test_an_empty_folder_falls_back_to_every_shortcut(monkeypatch):
    outputs = iter([("", None), ("Good Night\n", None)])
    seen = []

    def fake_run(args, timeout=None):
        seen.append(list(args))
        return next(outputs)

    monkeypatch.setattr(home_module, "_run_shortcuts", fake_run)
    assert home_module.available_devices() == ["Good Night"]
    assert seen[1] == ["list"]


def test_the_listing_is_cached_between_calls(shortcuts_cli):
    calls, _ = shortcuts_cli
    home_module.available_devices()
    home_module.available_devices()
    assert len([c for c in calls if c[0] == "list"]) == 1


def test_refresh_bypasses_the_cache(shortcuts_cli):
    calls, _ = shortcuts_cli
    home_module.available_devices()
    home_module.available_devices(refresh=True)
    assert len([c for c in calls if c[0] == "list"]) == 2


def test_invalidating_the_cache_forces_a_fresh_listing(shortcuts_cli):
    calls, _ = shortcuts_cli
    home_module.available_devices()
    home_module.invalidate_cache()
    home_module.available_devices()
    assert len([c for c in calls if c[0] == "list"]) == 2


def test_changing_the_folder_preference_invalidates_the_cache(shortcuts_cli):
    calls, _ = shortcuts_cli
    home_module.available_devices()
    preferences_module.set_preference("home_shortcuts_folder", "Elsewhere")
    home_module.available_devices()
    assert [c for c in calls if c[0] == "list"][-1] == ["list", "-f", "Elsewhere"]


def test_an_unavailable_cli_is_reported_not_raised():
    """Off-macOS (and on macOS 11 and earlier) the binary simply isn't there."""
    out = home_module.list_home_devices()
    assert "macOS 12" in out or "shortcuts" in out.lower()


# --- the spoken device list -------------------------------------------------


def test_the_device_list_is_spoken_naturally(shortcuts_cli):
    out = home_module.list_home_devices()
    assert "3 home controls" in out
    assert "Living Room Lights" in out


def test_a_single_device_is_spoken_in_the_singular(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(list="Good Night\n")
    assert "1 home control:" in home_module.list_home_devices()


def test_no_devices_explains_how_to_add_one(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(list="")
    out = home_module.list_home_devices()
    assert "Shortcuts app" in out


# --- name matching ----------------------------------------------------------


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("Good Night", "Good Night"),
        ("good night", "Good Night"),          # case-insensitive
        ("  good night  ", "Good Night"),      # padded
        ("lights", "Living Room Lights"),      # substring of the shortcut
        ("run the Movie Time scene", "Movie Time"),  # shortcut inside the phrase
        ("living room", "Living Room Lights"),
    ],
)
def test_a_device_is_matched_loosely(shortcuts_cli, spoken, expected):
    calls, _ = shortcuts_cli
    assert "Done" in home_module.run_home_shortcut(spoken)
    assert _run_args(calls)[0] == ["run", expected]


def test_word_overlap_matches_when_no_substring_does(shortcuts_cli):
    calls, configure = shortcuts_cli
    configure(list="Kitchen Spotlights\nGood Night\n")
    home_module.run_home_shortcut("kitchen")
    assert _run_args(calls)[0] == ["run", "Kitchen Spotlights"]


def test_an_ambiguous_name_asks_instead_of_guessing(shortcuts_cli):
    calls, configure = shortcuts_cli
    configure(list="Bedroom Lights\nKitchen Lights\n")
    out = home_module.run_home_shortcut("lights")
    assert "Which one?" in out
    assert "Bedroom Lights" in out and "Kitchen Lights" in out
    assert _run_args(calls) == []  # nothing was run


def test_an_unknown_name_lists_what_is_available(shortcuts_cli):
    calls, _ = shortcuts_cli
    out = home_module.run_home_shortcut("teleporter")
    assert "don't have a home control" in out
    assert "Good Night" in out
    assert _run_args(calls) == []


@pytest.mark.parametrize("name", [None, "", "   "])
def test_running_with_no_name_asks_for_one(shortcuts_cli, name):
    calls, _ = shortcuts_cli
    assert "Which home control" in home_module.run_home_shortcut(name)
    assert _run_args(calls) == []


def test_running_with_no_devices_configured(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(list="")
    assert "Shortcuts app" in home_module.run_home_shortcut("lights")


# --- the security gate ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Unlock Front Door",
        "Lock Up",
        "Open Garage",
        "Garage Door",
        "Front Gate",
        "Arm Alarm",
        "Disarm Security",
        "Back Door Deadbolt",
    ],
)
def test_security_sensitive_controls_need_confirmation(name):
    assert home_module.needs_confirmation(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Living Room Lights",
        "Movie Time",
        "Good Night",
        "Warm White Bedroom",  # "warm" must not be read as "arm"
        "Charm Lamp",          # nor "charm"
        "Farmhouse Kitchen",   # nor "farm"
        "Alarming Nobody",     # nor a word merely starting with "alarm"
    ],
)
def test_ordinary_controls_run_without_confirmation(name):
    """The gate matches whole words, so everyday scene names stay ungated."""
    assert home_module.needs_confirmation(name) is False


def test_the_word_alarm_itself_is_still_gated():
    assert home_module.needs_confirmation("Morning Alarm") is True


def test_an_unconfirmed_lock_is_never_actually_run(shortcuts_cli):
    """The whole point of the gate: a misheard command must not reach HomeKit."""
    calls, configure = shortcuts_cli
    configure(list="Unlock Front Door\n")
    out = home_module.run_home_shortcut("unlock front door")
    assert "confirm" in out.lower()
    assert _run_args(calls) == []


def test_the_confirmation_question_names_the_exact_control(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(list="Unlock Front Door\n")
    assert "Unlock Front Door" in home_module.run_home_shortcut("unlock")


@pytest.mark.parametrize("falsy", [False, None, 0, ""])
def test_a_falsy_confirmation_still_blocks_the_run(shortcuts_cli, falsy):
    calls, configure = shortcuts_cli
    configure(list="Unlock Front Door\n")
    home_module.run_home_shortcut("unlock front door", confirmed=falsy)
    assert _run_args(calls) == []


def test_a_confirmed_lock_runs(shortcuts_cli):
    calls, configure = shortcuts_cli
    configure(list="Unlock Front Door\n")
    out = home_module.run_home_shortcut("unlock front door", confirmed=True)
    assert "Done" in out
    assert _run_args(calls)[0] == ["run", "Unlock Front Door"]


def test_confirmation_is_not_demanded_for_ordinary_controls(shortcuts_cli):
    calls, _ = shortcuts_cli
    home_module.run_home_shortcut("Movie Time")
    assert _run_args(calls)[0] == ["run", "Movie Time"]


# --- failures ---------------------------------------------------------------


def test_a_failing_shortcut_is_reported_with_its_error(shortcuts_cli):
    _, configure = shortcuts_cli
    configure(run_error="accessory not responding")
    out = home_module.run_home_shortcut("Movie Time")
    assert "couldn't run" in out
    assert "accessory not responding" in out


def test_the_successful_confirmation_names_what_ran(shortcuts_cli):
    assert home_module.run_home_shortcut("Movie Time") == "Done — ran 'Movie Time'."
