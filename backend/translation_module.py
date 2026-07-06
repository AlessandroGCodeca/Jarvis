"""Language translation via the free MyMemory API (no API key needed).

MyMemory is free with a ~5000 characters/day limit and takes a ``langpair`` of
``source|target`` ISO codes. We map friendly language names to codes and support
translating both into and out of English. Failures degrade gracefully.
"""

import html

import httpx

MYMEMORY_URL = "https://api.mymemory.translated.net/get"

_HEADERS = {"User-Agent": "JARVIS/1.0 (voice assistant)"}

# Friendly language name -> ISO 639-1 code.
LANGUAGE_CODES = {
    "english": "en",
    "slovak": "sk",
    "italian": "it",
    "czech": "cs",
    "french": "fr",
    "spanish": "es",
    "german": "de",
    "hungarian": "hu",
}

# Code -> display name, for natural phrasing in the reply.
CODE_NAMES = {code: name.capitalize() for name, code in LANGUAGE_CODES.items()}


def _resolve_code(language: str):
    """Map a language name or code to an ISO code, or None if unknown."""
    if not language:
        return None
    key = language.strip().lower()
    if key in LANGUAGE_CODES:
        return LANGUAGE_CODES[key]
    if key in LANGUAGE_CODES.values():
        return key
    # Accept any bare 2-letter code we don't explicitly map.
    if len(key) == 2 and key.isalpha():
        return key
    return None


def translate(
    text: str, target_language: str, source_language: str = "English"
) -> str:
    """Translate ``text`` into ``target_language``.

    ``source_language`` defaults to English; set it (e.g. to "Slovak") to
    translate *into* English from another language. Returns natural-language
    text suitable for reading aloud.
    """
    text = (text or "").strip()
    if not text:
        return "What would you like me to translate?"

    target = _resolve_code(target_language)
    if not target:
        return f"I don't know how to translate into '{target_language}' yet."
    source = _resolve_code(source_language) or "en"
    if source == target:
        return text

    try:
        resp = httpx.get(
            MYMEMORY_URL,
            params={"q": text, "langpair": f"{source}|{target}"},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't reach the translation service right now ({exc})."

    status = data.get("responseStatus")
    if status in (403, "403"):
        return "The free translation quota for today has been reached."

    translated = (data.get("responseData") or {}).get("translatedText")
    if not translated:
        return "I couldn't translate that right now."
    translated = html.unescape(translated).strip()

    target_name = CODE_NAMES.get(target, target_language.strip().capitalize())
    return f'In {target_name}, that\'s "{translated}".'
