"""Tests for the dependency-free language detector.

JARVIS is spoken to in English, Slovak, Italian and Czech, and the detected
language decides both the reply language and the TTS voice — so a false
positive on an English sentence is very visible to the user.
"""

import pytest

import language_module


# --- normalize_language -----------------------------------------------------


@pytest.mark.parametrize(
    "value,code",
    [
        ("english", "en"), ("English", "en"), ("EN", "en"), ("anglicky", "en"),
        ("slovak", "sk"), ("slovensky", "sk"), ("slovenčina", "sk"), ("sk", "sk"),
        ("italian", "it"), ("italiano", "it"), ("it", "it"),
        ("czech", "cs"), ("česky", "cs"), ("čeština", "cs"), ("cz", "cs"),
        ("  Czech  ", "cs"),
    ],
)
def test_normalize_language_accepts_names_and_codes(value, code):
    assert language_module.normalize_language(value) == code


@pytest.mark.parametrize("value", [None, "", "klingon", "fr", "german"])
def test_normalize_language_rejects_anything_unsupported(value):
    assert language_module.normalize_language(value) is None


# --- detection: English is the safe default ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what's on my calendar today",
        "play some music",
        "remind me to call the dentist at four",
        "how are you",
        "",
        "   ",
    ],
)
def test_plain_english_is_detected_as_english(text):
    assert language_module.detect_language(text) == "en"


@pytest.mark.parametrize(
    "text",
    [
        "come over here",                      # "come" is also Italian
        "the dove landed on the roof",         # "dove" is also Italian
        "no, that is wrong",                   # "no" is also Slovak/Italian
        "I want to come and see where it is",  # "come" + "dove"-adjacent English
    ],
)
def test_english_sentences_with_ambiguous_words_stay_english(text):
    """Weak markers that collide with English must not flip the language on
    their own — otherwise ordinary English gets answered in Italian."""
    assert language_module.detect_language(text) == "en"


# --- detection: the three non-English languages -----------------------------


@pytest.mark.parametrize(
    "text",
    ["ciao come stai", "grazie mille", "perché non funziona", "sì, va bene"],
)
def test_italian_is_detected(text):
    assert language_module.detect_language(text) == "it"


@pytest.mark.parametrize(
    "text",
    ["ďakujem pekne", "ahoj ako sa máš", "prečo to nefunguje", "áno, prosím"],
)
def test_slovak_is_detected(text):
    assert language_module.detect_language(text) == "sk"


@pytest.mark.parametrize(
    "text",
    ["děkuji mnohokrát", "proč to nefunguje", "nevím co dělat", "dobře, díky"],
)
def test_czech_is_detected(text):
    assert language_module.detect_language(text) == "cs"


def test_distinctive_czech_characters_beat_shared_slavic_words():
    """"ahoj" is both Czech and Slovak; the Czech-only "ř" decides it."""
    assert language_module.detect_language("ahoj, jak se řekne") == "cs"


def test_distinctive_slovak_characters_beat_shared_slavic_words():
    assert language_module.detect_language("ahoj, ľúbim ťa") == "sk"


def test_every_detected_code_has_a_display_name():
    for text in ["hello", "ciao grazie", "ďakujem", "děkuji"]:
        code = language_module.detect_language(text)
        assert code in language_module.LANGUAGE_NAMES


def test_detection_is_case_insensitive():
    assert language_module.detect_language("CIAO COME STAI") == "it"
