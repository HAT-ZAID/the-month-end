"""Finiteness contract for the solver (CR-03 tail).

A forecast the kernel cannot value is not a zero and not an empty list: it is
an unknown projection.  ``solve_request`` must fail closed on it with a named
reason instead of ranking plan candidates built on a `NaN`/`Infinity` balance.

The oracle resolves ``build_forecast`` in its own namespace (``from
code.forecast import build_forecast``), so a test that wants the ledger to see a
non-finite entry has to patch ``code.oracle.build_forecast``: patching
``code.forecast.build_forecast`` only rebinds the module attribute and never
reaches the ledger path.
"""
from datetime import date
from decimal import Decimal

import pandas as pd

import code.forecast as forecast
import code.oracle as oracle
import code.solver as solver_mod
from code.solver import solve_request


profile = {
    "home_currency": "USD",
    "current_available_balance": "10000",
    "minimum_balance_to_keep": "2000",
    "expense_categories_to_protect": "",
    "payment_methods_user_will_consider":
        "full_payment|partial_payment|installments|wait",
}

events: list[dict] = []

options = pd.DataFrame(columns=[
    "payment_option_id", "request_id", "payment_method", "payment_amount",
    "number_of_payments", "first_payment_date", "payment_frequency_days",
    "financing_fee", "total_payable_amount",
])

messages = None


def _forecast_with(amount):
    return forecast.ForecastResult(entries=[
        forecast.ForecastEntry(
            date=date(2026, 1, 2),
            amount=amount,
            category="salary",
            kind="income",
            source="history",
            reason="finiteness fixture",
        )
    ])


def _request():
    return solve_request("r1", "u1", "2026-01-01", "100", profile, events, [],
                         options, messages, None, True)


def test_non_finite_forecast_entry_fails_the_request(monkeypatch):
    monkeypatch.setattr(oracle, "build_forecast",
                        lambda *a, **k: _forecast_with(float("nan")))
    result = _request()
    assert result["affordability_status"] == "not_affordable"
    assert "FORECAST_INVALID" in result["decision_explanation"]


def test_non_finite_forecast_amount_never_reaches_ranking(monkeypatch):
    # A stub can hand the solver a finite ledger next to a non-finite forecast:
    # the explicit finite check must reject it before any candidate is built.
    monkeypatch.setattr(solver_mod, "solve_safe_amount", lambda *a, **k: {
        "safe_amount": Decimal("0"),
        "earliest_date": None,
        "ledger": [Decimal("9000")] * 91,
        "fail_closed": False,
        "forecast": [{
            "date": "2026-01-02", "amount": "NaN", "kind": "income",
            "category": "salary", "source": "history",
        }],
    })
    result = _request()
    assert result["affordability_status"] == "not_affordable"
    assert result["recommended_payment_method"] == "not_recommended"
    assert result["payment_plan"] == "none"
    assert result["amount_safe_to_pay"] == "0"
    assert "FORECAST_INVALID" in result["decision_explanation"]


def test_non_finite_ledger_balance_never_reaches_ranking(monkeypatch):
    ledger = [Decimal("9000")] * 91
    ledger[40] = Decimal("Infinity")
    monkeypatch.setattr(solver_mod, "solve_safe_amount", lambda *a, **k: {
        "safe_amount": Decimal("0"),
        "earliest_date": None,
        "ledger": ledger,
        "fail_closed": False,
    })
    result = _request()
    assert result["affordability_status"] == "not_affordable"
    assert "FORECAST_INVALID" in result["decision_explanation"]


def test_raising_forecast_fails_the_request(monkeypatch):
    def boom(*_args, **_kwargs):
        raise ValueError("forecast cannot be valued")

    monkeypatch.setattr(oracle, "build_forecast", boom)
    result = _request()
    assert result["affordability_status"] == "not_affordable"
    assert result["recommended_payment_method"] == "not_recommended"
    assert "FORECAST_INVALID" in result["decision_explanation"]


def test_finite_forecast_still_decides(monkeypatch):
    # Over-eager guard check: a finite projection must keep its normal path.
    monkeypatch.setattr(oracle, "build_forecast",
                        lambda *a, **k: _forecast_with(Decimal("2500")))
    result = _request()
    assert result["affordability_status"] == "affordable_now"
    assert result["recommended_payment_method"] == "full_payment"
