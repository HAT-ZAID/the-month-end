"""Forecast policy defect D3: the incomplete request month is not a budget month.

D3 is G1-neutral and shipped.  The CR-13 D1/D2 corrected first-bucket
arithmetic (unspent remainder, real month length) was removed from the code
rather than left behind a default-off switch: an environment variable inside
the shipped decision path made ``output.csv`` bytes depend on the shell.
"""
from datetime import date
from decimal import Decimal

import code.forecast as forecast
from code.forecast import _monthly_budget_history, build_forecast


def _event(
    event_id,
    day,
    category,
    amount,
    *,
    event_type="expense",
    direction="debit",
    status="settled",
    description="",
    user="u1",
    currency="USD",
):
    return {
        "event_id": event_id,
        "user_id": user,
        "event_type": event_type,
        "category": category,
        "direction": direction,
        "amount": amount,
        "currency": currency,
        "event_date": day,
        "settlement_date": day,
        "status": status,
        "description": description,
        "linked_event_id": "",
        "flexibility": "fixed",
        "minimum_allowed_amount": "",
    }


def _salary_history():
    """Four monthly salary rows so the forecast projects a salary."""
    return [
        _event(
            f"salary_{month}",
            f"{month}-05",
            "salary",
            "2000",
            event_type="income",
            direction="credit",
            description="Payroll credit",
        )
        for month in ("2025-10", "2025-11", "2025-12", "2026-01")
    ]


def _budget_entries(result, day=None):
    """The monthly essential-budget rows, optionally only those on ``day``."""
    rows = [
        entry
        for entry in result.entries
        if entry.kind == "expense" and entry.reason == "monthly_accrual"
    ]
    if day is not None:
        rows = [entry for entry in rows if entry.date == day]
    return rows


# --- D3: the incomplete request month is not a budget month ------------------


def test_partial_request_month_is_not_history():
    # One complete month plus one settled row inside the request month: the
    # partial month cannot promote the category to a budget.
    budgets = _monthly_budget_history(
        [
            _event("groceries_dec", "2025-12-05", "groceries", "300"),
            _event("groceries_jan", "2026-01-03", "groceries", "300"),
        ],
        date(2026, 1, 10),
        home_currency="USD",
        fx_rows=[],
        warnings=[],
    )
    assert budgets == {}

    # With two complete months the partial month must not move the median:
    # median(100, 300) = 200, where including January's 50 would give 100.
    budgets = _monthly_budget_history(
        [
            _event("groceries_nov", "2025-11-05", "groceries", "100"),
            _event("groceries_dec", "2025-12-05", "groceries", "300"),
            _event("groceries_jan", "2026-01-03", "groceries", "50"),
        ],
        date(2026, 1, 10),
        home_currency="USD",
        fx_rows=[],
        warnings=[],
    )
    assert budgets == {"groceries": Decimal("200")}


# --- The corrected arithmetic was removed, not merely defaulted off ---------


def test_the_corrected_arithmetic_is_gone_and_an_env_value_cannot_move_it(monkeypatch):
    assert not hasattr(forecast, "FORECAST_CORRECTIONS_ENV")

    history = _salary_history() + [
        _event("groceries_nov", "2025-11-05", "groceries", "930"),
        _event("groceries_dec", "2025-12-05", "groceries", "930"),
    ]
    spent = _event("groceries_jan", "2026-01-05", "groceries", "100")

    monkeypatch.setenv("BUY_WAIT_FORECAST_CORRECTIONS", "1")
    result = build_forecast(history + [spent], "2026-01-20")

    first = _budget_entries(result, date(2026, 1, 20))
    # The shipped conservative charge: 930 * (12 + 1) / 30 = 403, with no
    # month-to-date credit and no real-month proration.
    assert first and first[0].amount == Decimal("-403")
