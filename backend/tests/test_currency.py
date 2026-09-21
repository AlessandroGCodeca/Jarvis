"""Tests for currency conversion.

The Frankfurter HTTP call is always stubbed — the autouse ``_no_network``
guard turns any real request into a failure.
"""

import httpx
import pytest

import currency_module


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._ok = status_ok

    def raise_for_status(self):
        if not self._ok:
            raise httpx.HTTPError("boom")

    def json(self):
        return self._payload


@pytest.fixture
def stub_rates(monkeypatch):
    """Stub httpx.get; returns the list that records each call's params."""
    calls = []

    def _install(payload, status_ok=True):
        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(params or {})
            return _FakeResponse(payload, status_ok)

        monkeypatch.setattr(httpx, "get", fake_get)
        return calls

    return _install


# --- currency name resolution -----------------------------------------------


@pytest.mark.parametrize(
    "name,code",
    [
        ("euros", "EUR"), ("euro", "EUR"), ("EUR", "EUR"), ("  eur  ", "EUR"),
        ("crowns", "CZK"), ("koruna", "CZK"), ("czech crowns", "CZK"),
        ("dollars", "USD"), ("pounds", "GBP"), ("sterling", "GBP"),
        ("forints", "HUF"), ("zloty", "PLN"), ("swiss franc", "CHF"),
    ],
)
def test_spoken_currency_names_resolve_to_iso_codes(name, code):
    assert currency_module._resolve_currency(name) == code


def test_the_retired_slovak_koruna_maps_to_the_euro():
    assert currency_module._resolve_currency("slovak crowns") == "EUR"


def test_an_unknown_three_letter_code_is_passed_through():
    """Lets the API answer for currencies not in the spoken-name table."""
    assert currency_module._resolve_currency("SEK") == "SEK"


@pytest.mark.parametrize("name", [None, "", "bananas", "money", "xy"])
def test_unrecognisable_currencies_resolve_to_none(name):
    assert currency_module._resolve_currency(name) is None


# --- amount formatting ------------------------------------------------------


@pytest.mark.parametrize(
    "value,text",
    [
        (1000, "1,000"),
        (1000000, "1,000,000"),
        (1234.567, "1,234.57"),
        (1000.001, "1,000"),    # within the rounding tolerance
        (0.5, "0.50"),
        (0, "0"),
        (-1500, "-1,500"),
    ],
)
def test_amount_formatting(value, text):
    assert currency_module._fmt_amount(value) == text


# --- convert_currency -------------------------------------------------------


def test_successful_conversion_is_phrased_naturally(stub_rates):
    stub_rates({"rates": {"CZK": 2500.0}})
    out = currency_module.convert_currency(100, "euros", "crowns")
    assert "100 euros" in out
    assert "2,500 Czech crowns" in out


def test_conversion_sends_the_resolved_codes_and_amount(stub_rates):
    calls = stub_rates({"rates": {"CZK": 250.0}})
    currency_module.convert_currency(10, "euros", "crowns")
    assert calls[0]["from"] == "EUR"
    assert calls[0]["to"] == "CZK"
    assert calls[0]["amount"] == 10.0


def test_converting_a_currency_to_itself_skips_the_network():
    """No stub installed: reaching the network here would fail the test."""
    out = currency_module.convert_currency(50, "EUR", "euros")
    assert "same amount" in out


def test_a_legacy_skk_conversion_explains_the_euro_changeover(stub_rates):
    stub_rates({"rates": {"CZK": 250.0}})
    out = currency_module.convert_currency(10, "skk", "crowns")
    assert "2009" in out


@pytest.mark.parametrize("amount", ["not a number", None, "", [1]])
def test_a_non_numeric_amount_is_rejected_before_any_request(amount):
    assert "numeric amount" in currency_module.convert_currency(amount, "EUR", "CZK")


def test_a_numeric_string_amount_is_accepted(stub_rates):
    stub_rates({"rates": {"CZK": 250.0}})
    assert "250" in currency_module.convert_currency("10", "EUR", "CZK")


@pytest.mark.parametrize("bad", ["bananas", "", None])
def test_an_unknown_source_currency_is_reported(bad):
    assert "recognise" in currency_module.convert_currency(10, bad, "EUR")


def test_an_unknown_target_currency_is_reported():
    assert "recognise" in currency_module.convert_currency(10, "EUR", "bananas")


def test_a_network_failure_degrades_to_a_spoken_apology(monkeypatch):
    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", exploding_get)
    out = currency_module.convert_currency(10, "EUR", "CZK")
    assert "couldn't reach" in out


def test_a_missing_rate_in_the_response_is_reported(stub_rates):
    stub_rates({"rates": {}})
    assert "couldn't get" in currency_module.convert_currency(10, "EUR", "CZK")


def test_an_http_error_status_degrades_gracefully(stub_rates):
    stub_rates({"rates": {"CZK": 1}}, status_ok=False)
    assert "couldn't reach" in currency_module.convert_currency(10, "EUR", "CZK")


# --- get_exchange_rate ------------------------------------------------------


def test_exchange_rate_is_reported_per_unit(stub_rates):
    stub_rates({"rates": {"CZK": 25.3}})
    out = currency_module.get_exchange_rate("euros", "crowns")
    assert "1 EUR" in out
    assert "25.30 CZK" in out


def test_exchange_rate_for_a_currency_against_itself_skips_the_network():
    assert "1 EUR" in currency_module.get_exchange_rate("EUR", "eur")


def test_exchange_rate_with_an_unknown_currency_is_reported():
    assert "recognise" in currency_module.get_exchange_rate("bananas", "EUR")


def test_exchange_rate_network_failure_degrades_gracefully(monkeypatch):
    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", exploding_get)
    assert "couldn't reach" in currency_module.get_exchange_rate("EUR", "CZK")
