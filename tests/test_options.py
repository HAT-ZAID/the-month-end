"""Direct unit tests for code/options.py payment-option filtering."""
from decimal import Decimal

import pandas as pd

from code.options import _build_payment_plan, get_valid_options


def _df(rows):
    cols = ["payment_option_id", "request_id", "payment_method", "payment_amount",
            "number_of_payments", "first_payment_date", "payment_frequency_days",
            "financing_fee", "total_payable_amount"]
    return pd.DataFrame(rows, columns=cols)


def _inst_rows(n=6, freq=30, total="27276.48", first="2024-03-10"):
    return [
        {"payment_option_id": "opt_full", "request_id": "r1", "payment_method": "full_payment",
         "payment_amount": 25256.0, "number_of_payments": 1, "first_payment_date": "2024-03-03",
         "payment_frequency_days": float("nan"), "financing_fee": 0.0, "total_payable_amount": 25256.0},
        {"payment_option_id": "opt_inst", "request_id": "r1", "payment_method": "installments",
         "payment_amount": 4546.08, "number_of_payments": n, "first_payment_date": first,
         "payment_frequency_days": float(freq), "financing_fee": 2020.48, "total_payable_amount": total},
    ]


def _profile(max_months):
    return {"home_currency": "USD", "max_installment_months": max_months}


def test_blank_max_rejects_installments():
    df = _df(_inst_rows())
    res = get_valid_options("r1", _profile(""), df)
    methods = [o["payment_method"] for o in res]
    assert "installments" not in methods
    assert "full_payment" in methods


def test_nan_max_rejects_installments():
    df = _df(_inst_rows())
    res = get_valid_options("r1", _profile(float("nan")), df)
    methods = [o["payment_method"] for o in res]
    assert "installments" not in methods
    assert "full_payment" in methods


def test_long_plan_vs_small_max_rejected():
    # 18 x 30d = 540d ~= 18mo > max 7 -> rejected; full_payment still valid.
    df = _df(_inst_rows(n=18, freq=30))
    res = get_valid_options("r1", _profile("7"), df)
    ids = [o["option_id"] for o in res]
    assert "opt_inst" not in ids
    assert "opt_full" in ids


def test_total_payable_nan_falls_back_to_amount_times_n():
    rows = _inst_rows(n=6, freq=30, total=float("nan"))
    df = _df(rows)
    res = get_valid_options("r1", _profile("12"), df)
    inst = next(o for o in res if o["option_id"] == "opt_inst")
    assert inst["total_payable"] == Decimal("4546.08") * 6


def test_build_payment_plan_dates_byte_match():
    opt = {"payment_method": "installments", "payment_amount": "100",
           "number_of_payments": 3, "payment_frequency_days": 30}
    assert _build_payment_plan(opt, "2026-01-01") == "2026-01-01:100|2026-01-31:100|2026-03-02:100"
