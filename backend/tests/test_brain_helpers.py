"""Tests for JarvisBrain's pure helpers: result formatters, the correction
detector, and history trimming.

These turn raw module results into the text Claude sees (and the user hears),
so they have to cope with every shape a bridge can return — including the
error *strings* the macOS bridges hand back instead of a list.
"""

import pytest

import claude_client
from claude_client import (
    _format_calendar,
    _format_emails,
    _format_list,
    _format_memories,
    _format_messages,
    _format_reminders,
    _format_web,
    _is_correction,
)


# --- every formatter passes an error string straight through -----------------


@pytest.mark.parametrize(
    "formatter",
    [
        _format_calendar,
        _format_emails,
        _format_web,
        _format_list,
        _format_memories,
        _format_reminders,
        _format_messages,
    ],
)
def test_an_error_string_reaches_the_user_verbatim(formatter):
    """Bridges return a plain string when unavailable; wrapping it in list
    formatting would produce nonsense like "- Calendar is unavailable"."""
    assert formatter("Calendar is unavailable (not running on macOS).") == (
        "Calendar is unavailable (not running on macOS)."
    )


@pytest.mark.parametrize(
    "formatter,expected",
    [
        (_format_calendar, "No events found."),
        (_format_emails, "No unread emails."),
        (_format_web, "No results found."),
        (_format_list, "Nothing found."),
        (_format_memories, "No matching memories."),
        (_format_reminders, "You have no pending reminders."),
        (_format_messages, "No recent messages found."),
    ],
)
def test_an_empty_result_has_a_spoken_fallback(formatter, expected):
    assert formatter([]) == expected


# --- individual formatters --------------------------------------------------


def test_calendar_events_become_a_bulleted_list():
    assert _format_calendar(["Standup at 9", "Lunch at 12"]) == (
        "- Standup at 9\n- Lunch at 12"
    )


def test_emails_show_sender_subject_and_preview():
    out = _format_emails(
        [{"sender": "Ada", "subject": "Re: orbit", "preview": "Looks good"}]
    )
    assert "From: Ada" in out
    assert "Subject: Re: orbit" in out
    assert "Looks good" in out


def test_an_email_with_missing_fields_still_formats():
    assert "?" in _format_emails([{}])


def test_web_results_show_title_snippet_and_url():
    out = _format_web(
        [{"title": "Python", "snippet": "A language", "url": "https://python.org"}]
    )
    assert "Python" in out and "A language" in out and "https://python.org" in out


def test_a_custom_empty_message_can_be_supplied():
    assert _format_list([], empty="No notes yet.") == "No notes yet."


def test_memories_are_flattened_onto_single_lines():
    """Newlines inside a stored memory would break the bulleted list."""
    out = _format_memories([{"content": "line one\nline two"}])
    assert out == "- line one line two"


def test_memories_that_are_all_blank_fall_back_to_the_empty_message():
    assert _format_memories([{"content": "   "}, {"content": ""}]) == (
        "No matching memories."
    )


def test_a_single_reminder_is_phrased_in_the_singular():
    out = _format_reminders([{"title": "Call mum"}])
    assert out == "You have 1 reminder: Call mum."


def test_several_reminders_are_phrased_in_the_plural():
    out = _format_reminders([{"title": "A"}, {"title": "B"}])
    assert out.startswith("You have 2 reminders:")


def test_a_reminder_due_date_is_included():
    assert "(due Friday)" in _format_reminders([{"title": "A", "due": "Friday"}])


def test_a_reminder_with_no_title_is_labelled_untitled():
    assert "untitled" in _format_reminders([{}])


def test_messages_are_listed_as_sender_and_content():
    out = _format_messages([{"sender": "Ada", "content": "on my way"}])
    assert "Ada: on my way" in out


# --- correction detection ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "no, it's at four",
        "nope",
        "No that's wrong",
        "that's not right",
        "not quite",
        "incorrect",
        "wrong",
        "I meant tomorrow",
        "actually, make it 5pm",
        "wait, that's the wrong day",
        "noooo",
    ],
)
def test_a_correction_opener_is_detected(text):
    assert _is_correction(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "what's on my calendar",
        "play some music",
        "notify me when it's done",      # starts with "no" but isn't "no"
        "nothing else, thanks",
        "I actually like that",          # "actually" mid-sentence, not an opener
        "tell me if that's wrong",       # "wrong" mid-sentence
        "",
    ],
)
def test_ordinary_speech_is_not_treated_as_a_correction(text):
    """A false positive writes a bogus "correction" into long-term memory."""
    assert _is_correction(text) is False


def test_a_correction_is_detected_regardless_of_case_or_leading_space():
    assert _is_correction("   ACTUALLY, it's Tuesday") is True


def test_none_is_not_a_correction():
    assert _is_correction(None) is False


# --- name / remember extraction ---------------------------------------------


@pytest.mark.parametrize(
    "text,name",
    [
        ("my name is Sandro", "Sandro"),
        ("call me Boss", "Boss"),
        ("Actually, my name is Alessandro", "Alessandro"),
        ("my name is Zoë", "Zoë"),
    ],
)
def test_a_preferred_name_is_extracted(text, name):
    assert claude_client._NAME_RE.search(text).group(1) == name


def test_a_remember_instruction_captures_the_note():
    match = claude_client._REMEMBER_RE.search("remember that the wifi code is 1234")
    assert match.group(1) == "the wifi code is 1234"


def test_remember_works_without_the_word_that():
    match = claude_client._REMEMBER_RE.search("remember the bins go out on Tuesday")
    assert match.group(1) == "the bins go out on Tuesday"
