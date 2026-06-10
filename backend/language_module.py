"""Lightweight language detection for JARVIS (no dependencies).

Detects English, Slovak, Italian, and Czech from a short message using a mix of
distinctive-character signals and weighted marker words. It's deliberately
conservative: ambiguous English-colliding words (e.g. "come", "dove") don't flip
the result to a foreign language without corroborating evidence.
"""

import re

LANGUAGE_NAMES = {"en": "English", "sk": "Slovak", "it": "Italian", "cs": "Czech"}

# Distinctive single-language characters (strong signals).
_CZECH_CHARS = set("řůě")
_SLOVAK_CHARS = set("äľôťďĺŕ")
_ITALIAN_CHARS = set("àèìòù")
# Carons shared by Czech and Slovak (Slavic, but not decisive on their own).
_SLAVIC_CARON = set("čšžň")

# Marker words -> weight. Weight 2 = fairly unambiguous even without accents;
# weight 1 = could collide with English/another language, needs corroboration.
_SLOVAK_WORDS = {
    "ahoj": 2, "ďakujem": 2, "prosím": 2, "prečo": 2, "čo": 2, "áno": 2,
    "máš": 2, "nie": 1, "ako": 1, "kde": 1, "kedy": 1, "viem": 1, "som": 1,
}
_ITALIAN_WORDS = {
    "ciao": 2, "grazie": 2, "perché": 2, "prego": 2, "sì": 2, "stai": 2,
    "cosa": 1, "come": 1, "dove": 1, "quando": 1, "quello": 1, "sono": 1,
}
_CZECH_WORDS = {
    "ahoj": 2, "děkuji": 2, "díky": 2, "prosím": 2, "proč": 2, "nevím": 2,
    "dobře": 2, "jak": 1, "kde": 1, "ano": 1, "jako": 1,
}

# Names/codes -> ISO 639-1 code, for honouring an explicit language request.
_NAME_TO_CODE = {
    "english": "en", "en": "en", "anglicky": "en",
    "slovak": "sk", "slovensky": "sk", "slovenčina": "sk", "sk": "sk",
    "italian": "it", "italiano": "it", "it": "it",
    "czech": "cs", "česky": "cs", "čeština": "cs", "cs": "cs", "cz": "cs",
}


def normalize_language(value):
    """Map a language name or code to an ISO code (en/sk/it/cs), or None."""
    if not value:
        return None
    return _NAME_TO_CODE.get(str(value).strip().lower())


def detect_language(text: str) -> str:
    """Best-effort detect the language of ``text``; defaults to English."""
    if not text or not text.strip():
        return "en"
    lower = text.lower()
    words = set(re.findall(r"\w+", lower, flags=re.UNICODE))

    def split_score(word_weights):
        # "strong" = fairly unambiguous markers (weight 2); "weak" = markers
        # that also occur in English (weight 1), e.g. "come"/"dove"/"kde".
        strong = sum(
            w for word, w in word_weights.items() if word in words and w >= 2
        )
        weak = sum(
            w for word, w in word_weights.items() if word in words and w < 2
        )
        return strong, weak

    sk_strong, sk_weak = split_score(_SLOVAK_WORDS)
    it_strong, it_weak = split_score(_ITALIAN_WORDS)
    cs_strong, cs_weak = split_score(_CZECH_WORDS)

    has_cz_char = any(c in lower for c in _CZECH_CHARS)
    has_sk_char = any(c in lower for c in _SLOVAK_CHARS)
    has_it_char = any(c in lower for c in _ITALIAN_CHARS)
    has_caron = any(c in lower for c in _SLAVIC_CARON)

    if has_cz_char:
        cs_strong += 3
    if has_sk_char:
        sk_strong += 3
    if has_it_char:
        it_strong += 1
    # A caron without Italian accents means Slavic; nudge both and let the
    # words / distinctive chars decide between Czech and Slovak.
    if has_caron and not has_it_char:
        cs_strong += 1
        sk_strong += 1

    # Weak (English-colliding) markers only count once there's a strong signal
    # for that language, so an English sentence with "come"/"dove" stays English.
    sk = sk_strong + (sk_weak if sk_strong else 0)
    it = it_strong + (it_weak if it_strong else 0)
    cs = cs_strong + (cs_weak if cs_strong else 0)

    scores = {"sk": sk, "it": it, "cs": cs}
    best = max(scores, key=scores.get)
    # Require minimum confidence so nothing weak alone overrides English.
    if scores[best] < 2:
        return "en"
    return best
