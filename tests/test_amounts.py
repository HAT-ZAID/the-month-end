"""P1 amount router + lexicon (TDD): parse locale amounts + extract currency-coded figures."""
from decimal import Decimal

from code.amounts import extract_amounts, parse_amount


def test_us_style_parse():
    assert parse_amount("1,302.40", "EUR") == Decimal("1302.40")


def test_id_es_decimal_parse():
    assert parse_amount("1.050,32", "EUR", "es") == Decimal("1050.32")


def test_idr_grouping_parse():
    assert parse_amount("43.339.000", "IDR") == Decimal("43339000")


def test_extract_iso_coded():
    out = extract_amounts("Send USD 1,574.40 now")
    assert out == [{"currency": "USD", "amount": Decimal("1574.40")}]


def test_extract_symbol_maps_to_ccy():
    out = extract_amounts("transfer ₹1,00,000 today")
    assert out == [{"currency": "INR", "amount": Decimal("100000")}]


def test_extract_idr_grouping():
    out = extract_amounts("I need to send IDR 15,656,000 to my family")
    assert out == [{"currency": "IDR", "amount": Decimal("15656000")}]


def test_extract_strips_id_figure_from_cue_sentence():
    out = extract_amounts("event_1815 totalling USD 1574.40 due")
    assert out == [{"currency": "USD", "amount": Decimal("1574.40")}]


def test_extract_es_decimal_after_code():
    out = extract_amounts("Precio total: 1.050,32 EUR", lang="es")
    assert out == [{"currency": "EUR", "amount": Decimal("1050.32")}]


def test_extract_ignores_plain_count_noun():
    assert extract_amounts("3 installments available") == []


def test_extract_multiple_in_order():
    out = extract_amounts("USD 10 first, then ZAR 5")
    assert out == [
        {"currency": "USD", "amount": Decimal("10")},
        {"currency": "ZAR", "amount": Decimal("5")},
    ]


def test_boundaryless_currency():
    out = extract_amounts("BalanceUSD 100")
    assert out == [{"currency": "USD", "amount": Decimal("100")}]
    out = extract_amounts("Amount€ 50")
    assert out == [{"currency": "EUR", "amount": Decimal("50")}]


def test_mixed_separators():
    assert parse_amount("1.200,50", "EUR", "es") == Decimal("1200.50")
    assert parse_amount("1,200.50", "EUR", "en") == Decimal("1200.50")


def test_extreme_grouping():
    assert parse_amount("1.000.000,00", "EUR", "es") == Decimal("1000000.00")
    assert parse_amount("1,000,000.00", "USD", "en") == Decimal("1000000.00")


def test_parse_nan_returns_none():
    assert parse_amount("nan", "USD") is None
    assert parse_amount("NaN", "USD") is None
    assert parse_amount(" nan ", "USD") is None
    # non-finite amounts must be rejected like NaN (see code/amounts.py _parse)
    assert parse_amount("Infinity", "USD") is None
    assert parse_amount("-Infinity", "USD") is None
    assert parse_amount("inf", "USD") is None
    assert parse_amount("-inf", "USD") is None
    assert parse_amount(" Infinity ", "USD") is None