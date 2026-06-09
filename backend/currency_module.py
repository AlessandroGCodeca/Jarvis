"""Currency conversion via the free Frankfurter API (no API key needed).

Frankfurter serves ECB reference rates. We accept both ISO codes (EUR, CZK) and
common spoken names ("euros", "crowns", "forints"). Failures degrade to a
friendly spoken message.
"""

import httpx

FRANKFURTER_URL = "https://api.frankfurter.app/latest"

_HEADERS = {"User-Agent": "JARVIS/1.0 (voice assistant)"}

# Spoken/written name (or code) -> ISO 4217 code.
CURRENCY_CODES = {
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "crown": "CZK", "crowns": "CZK", "koruna": "CZK", "koruny": "CZK",
    "czk": "CZK", "czech crown": "CZK", "czech crowns": "CZK",
    "dollar": "USD", "dollars": "USD", "usd": "USD", "us dollar": "USD",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "sterling": "GBP",
    "forint": "HUF", "forints": "HUF", "huf": "HUF",
    "zloty": "PLN", "zlotys": "PLN", "zloties": "PLN", "pln": "PLN",
    "franc": "CHF", "francs": "CHF", "chf": "CHF", "swiss franc": "CHF",
    # Legacy Slovak koruna was replaced by the euro in 2009.
    "skk": "EUR", "slovak crown": "EUR", "slovak crowns": "EUR",
}

# Code -> spoken plural name, for natural phrasing.
CODE_DISPLAY = {
    "EUR": "euros",
    "CZK": "Czech crowns",
    "USD": "US dollars",
    "GBP": "British pounds",
    "HUF": "Hungarian forints",
    "PLN": "Polish zloty",
    "CHF": "Swiss francs",
}


def _resolve_currency(name: str):
    """Map a currency name or code to an ISO 4217 code, or None if unknown."""
    if not name:
        return None
    key = name.strip().lower()
    if key in CURRENCY_CODES:
        return CURRENCY_CODES[key]
    code = name.strip().upper()
    if len(code) == 3 and code.isalpha():
        return code
    return None


def _display(code: str) -> str:
    return CODE_DISPLAY.get(code, code)


def _fmt_amount(value) -> str:
    """Format a number with thousands separators; drop trailing .00."""
    value = float(value)
    if abs(value - round(value)) < 0.005:
        return f"{round(value):,}"
    return f"{value:,.2f}"


def _is_legacy_skk(name: str) -> bool:
    return (name or "").strip().lower() in ("skk", "slovak crown", "slovak crowns")


def convert_currency(amount, from_currency: str, to_currency: str) -> str:
    """Convert ``amount`` from one currency to another, phrased naturally."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return "I need a numeric amount to convert."

    src = _resolve_currency(from_currency)
    dst = _resolve_currency(to_currency)
    if not src:
        return f"I don't recognise the currency '{from_currency}'."
    if not dst:
        return f"I don't recognise the currency '{to_currency}'."

    note = ""
    if _is_legacy_skk(from_currency) or _is_legacy_skk(to_currency):
        note = " (The Slovak koruna was replaced by the euro in 2009.)"

    if src == dst:
        return f"{_fmt_amount(amount)} {_display(src)} is the same amount.{note}"

    try:
        resp = httpx.get(
            FRANKFURTER_URL,
            params={"amount": amount, "from": src, "to": dst},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        rates = resp.json().get("rates") or {}
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't reach the exchange-rate service right now ({exc})."

    if dst not in rates:
        return f"I couldn't get a {src} to {dst} rate right now."

    converted = rates[dst]
    return (
        f"{_fmt_amount(amount)} {_display(src)} is "
        f"{_fmt_amount(converted)} {_display(dst)}.{note}"
    )


def get_exchange_rate(from_currency: str, to_currency: str) -> str:
    """Return just the exchange rate between two currencies."""
    src = _resolve_currency(from_currency)
    dst = _resolve_currency(to_currency)
    if not src or not dst:
        return "I don't recognise one of those currencies."
    if src == dst:
        return f"1 {src} is, of course, 1 {dst}."

    try:
        resp = httpx.get(
            FRANKFURTER_URL,
            params={"from": src, "to": dst},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        rates = resp.json().get("rates") or {}
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't reach the exchange-rate service right now ({exc})."

    if dst not in rates:
        return f"I couldn't get a {src} to {dst} rate right now."
    return f"1 {src} is currently {_fmt_amount(rates[dst])} {dst}."
