"""Tests for the JSON-backed user preferences store.

``_isolated_state`` redirects the preferences file into a temp directory, so
the user's real user_preferences.json is never read or written.
"""

import json

import pytest

import preferences_module


def _prefs_file():
    return preferences_module._PREFS_PATH


def test_first_read_creates_the_file_from_defaults():
    assert not _prefs_file().exists()
    prefs = preferences_module.get_all_preferences()
    assert prefs == preferences_module.DEFAULT_PREFERENCES
    assert _prefs_file().exists()


def test_defaults_are_not_shared_with_the_stored_copy():
    """A mutation of the returned dict must not corrupt DEFAULT_PREFERENCES."""
    preferences_module.get_all_preferences()["name"] = "Mutated"
    assert preferences_module.DEFAULT_PREFERENCES["name"] == "Sandro"


def test_set_then_get_roundtrip():
    preferences_module.set_preference("weather_city", "Bratislava")
    assert preferences_module.get_preference("weather_city") == "Bratislava"


def test_a_set_preference_survives_a_fresh_read():
    preferences_module.set_preference("language", "sk")
    assert preferences_module.get_all_preferences()["language"] == "sk"


def test_setting_a_new_key_keeps_the_existing_ones():
    preferences_module.set_preference("nickname", "Boss")
    prefs = preferences_module.get_all_preferences()
    assert prefs["nickname"] == "Boss"
    assert prefs["name"] == "Sandro"


def test_set_preference_confirms_what_it_saved():
    out = preferences_module.set_preference("weather_unit", "fahrenheit")
    assert "weather_unit" in out and "fahrenheit" in out


@pytest.mark.parametrize("key", [None, ""])
def test_setting_a_preference_with_no_key_is_refused(key):
    assert "need a preference name" in preferences_module.set_preference(key, "x")


def test_get_preference_returns_the_default_for_an_unset_key():
    assert preferences_module.get_preference("nope", "fallback") == "fallback"


def test_get_preference_with_no_default_returns_none():
    assert preferences_module.get_preference("nope") is None


def test_non_string_values_survive_the_json_roundtrip():
    preferences_module.set_preference("quiet_hours", [22, 7])
    assert preferences_module.get_preference("quiet_hours") == [22, 7]


def test_a_corrupt_preferences_file_falls_back_to_defaults():
    _prefs_file().write_text("{not valid json", encoding="utf-8")
    assert preferences_module.get_all_preferences() == preferences_module.DEFAULT_PREFERENCES


def test_a_non_dict_preferences_file_falls_back_to_defaults():
    _prefs_file().write_text("[1, 2, 3]", encoding="utf-8")
    assert preferences_module.get_all_preferences() == preferences_module.DEFAULT_PREFERENCES


def test_the_file_is_written_as_readable_utf8_json():
    preferences_module.set_preference("name", "Sandro Čech")
    data = json.loads(_prefs_file().read_text(encoding="utf-8"))
    assert data["name"] == "Sandro Čech"


def test_an_unwritable_location_reports_failure_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(
        preferences_module, "_PREFS_PATH", tmp_path / "missing-dir" / "prefs.json"
    )
    assert "couldn't save" in preferences_module.set_preference("name", "X")
