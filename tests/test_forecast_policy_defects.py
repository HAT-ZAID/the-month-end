"""Forecast policy defects (Task 13, CR-13): partial month, MTD, proration.

Three defects confirmed in ``docs/g2-hypothesis-report.md`` (D1-D3) are pinned
here as policy rules, never as dataset rows:

* D3 - the incomplete request month is not a budget month, because a handful of
  days at the start of a month is not a monthly total;
* D1 - the first bucket charges only the unspent remainder of the current month,
  ``full - month_to_date``, clamped at zero so it can never go negative, and the
  month-to-date rows are not charged a second time as in-window coverage;
* D2 - proration uses the real days in the month and excludes the request day
  (``actual_excl``: numerator ``days_left - 1``, denominator the real month
  length), still clamped at one month's budget.

D1 and D2 lower the day-0 charge, which raises the amount declared safe, so they
are opt-in behind ``BUY_WAIT_FORECAST_CORRECTIONS`` and the shipped default keeps
the conservative pre-correction charge.  D3 is G1-neutral and always on.
"""
from datetime import date
from decimal import Decimal

from code.forecast import (
    FORECAST_CORRECTIONS_ENV,
    _monthly_budget_history,
    build_forecast,
)


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


def test_partial_request_month_is_not_history(monkeypatch):
    # Rule 1 is the shipped default; it is G1-neutral and needs no flag.
    monkeypatch.delenv(FORECAST_CORRECTIONS_ENV, raising=False)
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


# --- D1: the first bucket is only the unspent remainder of the month ---------


def test_first_bucket_charges_only_the_unspent_remainder(monkeypatch):
    # Rules 2 and 3 are opt-in: the corrected arithmetic raises the safe amount
    # above the reference, so it is gated behind the explicit flag.
    monkeypatch.setenv(FORECAST_CORRECTIONS_ENV, "1")
    # Two complete months set a 300 monthly budget; no salary is projected, so
    # the first bucket is not prorated and the rule is visible on its own.
    history = [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
    ]
    spent = _event("groceries_jan", "2026-01-05", "groceries", "120")
    result = build_forecast(history + [spent], "2026-01-20")

    first = _budget_entries(result, date(2026, 1, 20))
    # 300 owed for the month, 120 already paid before the request: 180 left. The
    # month-to-date row is charged exactly once, not again as in-window cover.
    assert first and first[0].amount == Decimal("-180")

    # Spending more than the budget cannot make the bucket negative: the first
    # bucket disappears and the later buckets keep the full budget.
    overspent = _event("groceries_jan", "2026-01-05", "groceries", "400")
    result = build_forecast(history + [overspent], "2026-01-20")
    assert _budget_entries(result, date(2026, 1, 20)) == []
    assert {entry.amount for entry in _budget_entries(result)} == {Decimal("-300")}
    assert all(entry.amount < 0 for entry in result.entries)


# --- D2: proration uses the real month length and excludes the request day ---


def test_proration_uses_real_month_length(monkeypatch):
    monkeypatch.setenv(FORECAST_CORRECTIONS_ENV, "true")
    history = _salary_history() + [
        _event("groceries_nov", "2025-11-05", "groceries", "930"),
        _event("groceries_dec", "2025-12-05", "groceries", "930"),
    ]

    # 20 January has 11 days after the request day in a 31-day month:
    # 930 * 11 / 31 = 330. A fixed /30 month would post 930 * 13 / 30 = 403.
    january = build_forecast(history, "2026-01-20")
    assert [entry for entry in january.entries if entry.kind == "income"], (
        "the fixture must project a salary, otherwise the bucket is not prorated"
    )
    first = _budget_entries(january, date(2026, 1, 20))
    assert first and first[0].amount == Decimal("-330")

    # The same day of April has 10 days after the request day in a 30-day month:
    # 930 * 10 / 30 = 310. A real month length makes the two months differ.
    april = build_forecast(history, "2026-04-20")
    first = _budget_entries(april, date(2026, 4, 20))
    assert first and first[0].amount == Decimal("-310")


# --- The corrected arithmetic is opt-in --------------------------------------


def test_forecast_corrections_are_off_by_default(monkeypatch):
    monkeypatch.delenv(FORECAST_CORRECTIONS_ENV, raising=False)
    assert FORECAST_CORRECTIONS_ENV == "BUY_WAIT_FORECAST_CORRECTIONS"

    history = _salary_history() + [
        _event("groceries_nov", "2025-11-05", "groceries", "930"),
        _event("groceries_dec", "2025-12-05", "groceries", "930"),
    ]
    # 100 already spent this month would be subtracted only by the corrected
    # rule; the default keeps the pre-correction charge.
    spent = _event("groceries_jan", "2026-01-05", "groceries", "100")
    result = build_forecast(history + [spent], "2026-01-20")

    first = _budget_entries(result, date(2026, 1, 20))
    # Default policy: 930 * (12 + 1) / 30 = 403, with no month-to-date credit.
    assert first and first[0].amount == Decimal("-403")

    # The same fixture under the flag takes both corrections: 930 * 11 / 31 = 330
    # less the 100 already spent gives 230.
    monkeypatch.setenv(FORECAST_CORRECTIONS_ENV, "on")
    corrected = build_forecast(history + [spent], "2026-01-20")
    first = _budget_entries(corrected, date(2026, 1, 20))
    assert first and first[0].amount == Decimal("-230")

    # Turning the flag back off restores the default, so the read is per call.
    monkeypatch.setenv(FORECAST_CORRECTIONS_ENV, "0")
    restored = build_forecast(history + [spent], "2026-01-20")
    first = _budget_entries(restored, date(2026, 1, 20))
    assert first and first[0].amount == Decimal("-403")
