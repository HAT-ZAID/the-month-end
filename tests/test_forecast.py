"""Forecast policy boundaries and the explicit oracle fail-closed contract.

Each test pins one policy rule from a brief, not a dataset row: expenses are
projected as one monthly budget per category (Task 8), discretionary spending is
never projected, irregular income streams are not invented, an ended employment
stops salary projection, only one primary salary stream is chosen, evidence
expense facts move only their post-effective-date forecast, and an ambiguous
ledger fails closed instead of returning a synthetic flat ledger.
"""
from datetime import date
from decimal import Decimal

import pandas as pd

import code.solver as solver_mod
from code.forecast import build_forecast
from code.oracle import solve_safe_amount
from code.solver import solve_request


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


def _monthly(category, months, amount="100", **kwargs):
    return [
        _event(f"{category}_{month}", f"{month}-05", category, amount, **kwargs)
        for month in months
    ]


def _flags(*groups):
    return {(g[0], g[1], g[2]) for g in groups}


def _entries(result, kind):
    return [entry for entry in result.entries if entry.kind == kind]


def _budget_entries(result):
    """The monthly essential-budget rows (Task 8)."""
    return [
        entry
        for entry in result.entries
        if entry.kind == "expense" and entry.reason == "monthly_accrual"
    ]


# 1. Discretionary category is not forecast; essential category is.
def test_discretionary_category_is_not_forecast_but_essential_is():
    events = _monthly("dining", ("2025-10", "2025-11", "2025-12", "2026-01"))
    events += _monthly("groceries", ("2025-10", "2025-11", "2025-12", "2026-01"))
    result = build_forecast(
        events,
        "2026-02-01",
        recurrence_flags=_flags(
            ("u1", "dining", "expense"), ("u1", "groceries", "expense")
        ),
    )

    categories = {entry.category for entry in _entries(result, "expense")}
    assert "groceries" in categories
    assert "dining" not in categories


# 2. Gig/platform salary stream is not forecast.
def test_gig_platform_salary_stream_is_not_forecast():
    events = _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="1700",
        event_type="income",
        direction="credit",
        description="Weekly app earnings",
    )
    result = build_forecast(events, "2026-02-01")

    assert _entries(result, "income") == []
    assert result.income_stream is None


# 3. After employment_ended, no salary forecast is emitted.
def test_employment_ended_stops_salary_forecast():
    events = _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="2000",
        event_type="income",
        direction="credit",
        description="Payroll credit",
    )
    result = build_forecast(events, "2026-02-01", evidence_flags={"employment_ended"})

    assert _entries(result, "income") == []
    assert result.income_stream is None


# 4. Only one primary salary stream is selected when primary and secondary income exist.
def test_only_one_primary_salary_stream_is_forecast():
    events = _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="2000",
        event_type="income",
        direction="credit",
        description="Primary household salary",
    )
    events += _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="900",
        event_type="income",
        direction="credit",
        description="Second household income",
    )
    result = build_forecast(events, "2026-02-01")

    income = _entries(result, "income")
    assert result.income_stream == "primary household salary"
    assert income
    assert {entry.amount for entry in income} == {Decimal("2000")}


# 5. An expense_fact rent increase changes only the post-effective-date rent forecast.
def test_rent_increase_fact_changes_only_post_effective_date_forecast():
    events = _monthly("rent", ("2025-11", "2025-12", "2026-01"), amount="1000")
    fact = {
        "category": "rent",
        "kind": "increase",
        "amount": None,
        "currency": "",
        "effective_date": "2026-02-15",
        "percent_change": Decimal("10"),
        "source_message_id": "message_test",
    }
    result = build_forecast(
        events,
        "2026-01-15",
        recurrence_flags=_flags(("u1", "rent", "expense")),
        expense_facts=[fact],
    )

    rent = [entry for entry in _entries(result, "expense") if entry.category == "rent"]
    assert rent, "rent history should be forecast"
    before = [entry for entry in rent if entry.date < pd.Timestamp("2026-02-15").date()]
    after = [entry for entry in rent if entry.date >= pd.Timestamp("2026-02-15").date()]
    assert before and after
    assert {entry.amount for entry in before} == {Decimal("-1000")}
    assert {entry.amount for entry in after} == {Decimal("-1100")}
    assert {entry.source for entry in after} == {"evidence"}


# 6. Real in-window salary rows fill the occurrence cap and anchor the projection.
def test_salary_projection_counts_real_in_window_rows_toward_cap():
    history = _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="2000",
        event_type="income",
        direction="credit",
        description="Payroll credit",
    )
    real_first = _event(
        "salary_real_1",
        "2026-02-10",
        "salary",
        "2000",
        event_type="income",
        direction="credit",
        status="scheduled",
        description="Payroll credit",
    )
    # One real row leaves room for exactly one projection, and that projection
    # must be anchored after the real row, not on the cadence date from history.
    result = build_forecast(history + [real_first], "2026-02-01")
    projected = _entries(result, "income")
    assert len(projected) == 1
    assert projected[0].date > date(2026, 2, 10)
    assert all(entry.date != date(2026, 2, 10) for entry in projected)

    real_second = _event(
        "salary_real_2",
        "2026-02-27",
        "salary",
        "2000",
        event_type="income",
        direction="credit",
        status="scheduled",
        description="Payroll credit",
    )
    # Two real rows already fill the cap: no synthetic salary may be added.
    result = build_forecast(history + [real_first, real_second], "2026-02-01")
    assert _entries(result, "income") == []


# 7. An ineligible in-window bonus must not consume the cap or move the anchor.
def test_ineligible_in_window_bonus_does_not_change_salary_projection():
    history = _monthly(
        "salary",
        ("2025-10", "2025-11", "2025-12", "2026-01"),
        amount="2000",
        event_type="income",
        direction="credit",
        description="Payroll credit",
    )
    baseline = build_forecast(history, "2026-02-01")
    bonus = _event(
        "salary_bonus",
        "2026-02-25",
        "salary",
        "5000",
        event_type="income",
        direction="credit",
        status="scheduled",
        description="Annual bonus",
    )
    result = build_forecast(history + [bonus], "2026-02-01")

    baseline_dates = [entry.date for entry in _entries(baseline, "income")]
    result_dates = [entry.date for entry in _entries(result, "income")]
    assert result_dates == baseline_dates
    assert len(result_dates) == 2


# 8. Suppressing an essential-variable category removes its direct forecast and reserve.
def test_suppressed_category_removes_direct_forecast_and_reserve():
    events = [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
        _event("groceries_jan", "2026-01-05", "groceries", "300"),
    ]
    result = build_forecast(
        events,
        "2026-02-01",
        recurrence_flags=_flags(("u1", "groceries", "expense")),
        suppressed_categories={"groceries"},
    )

    groceries = [entry for entry in result.entries if entry.category == "groceries"]
    assert groceries == []


# 9. The monthly budget covers every bucket of the 90-day horizon.
def test_monthly_budget_covers_each_bucket_of_the_horizon():
    events = [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
        _event("groceries_jan", "2026-01-05", "groceries", "300"),
    ]
    result = build_forecast(events, "2026-02-01")

    budget = _budget_entries(result)
    assert [entry.date for entry in budget] == [
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    assert {entry.amount for entry in budget} == {Decimal("-300")}
    assert {entry.source for entry in budget} == {"history"}
    assert sum(abs(entry.amount) for entry in budget) == Decimal("1200")


# 10. A real in-window row offsets exactly one bucket: no double counting.
def test_in_window_row_offsets_the_monthly_bucket():
    events = [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
        _event("groceries_feb", "2026-02-20", "groceries", "300", status="scheduled"),
    ]
    result = build_forecast(events, "2026-02-01")

    budget = _budget_entries(result)
    # The bucket holding the real row is fully covered; the other three are not.
    assert [entry.date for entry in budget] == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    assert {entry.amount for entry in budget} == {Decimal("-300")}


# --- Task 8: monthly essential-budget accrual -------------------------------


def test_two_months_of_history_produce_a_monthly_budget_on_the_request_date():
    events = [
        _event("groceries_nov", "2025-11-05", "groceries", "100"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
    ]
    result = build_forecast(events, "2026-01-10")

    budget = _budget_entries(result)
    assert budget, "two months of history must produce a budget"
    first = budget[0]
    assert first.date == date(2026, 1, 10)
    assert first.category == "groceries"
    # Median of the monthly totals (100, 300), not the monthly sum.
    assert first.amount == Decimal("-200")
    assert [entry.date for entry in budget] == [
        date(2026, 1, 10),
        date(2026, 2, 10),
        date(2026, 3, 10),
        date(2026, 4, 10),
    ]


def test_a_single_month_of_history_is_not_projected():
    events = [_event("groceries_dec", "2025-12-05", "groceries", "300")]
    result = build_forecast(events, "2026-01-10")

    assert _budget_entries(result) == []


def test_real_in_window_rows_reduce_the_bucket_without_going_negative():
    history = [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
    ]

    def first_bucket(real_amount, status="pending"):
        real = _event("groceries_real", "2026-01-20", "groceries", real_amount, status=status)
        result = build_forecast(history + [real], "2026-01-10")
        return result, [entry for entry in _budget_entries(result) if entry.date == date(2026, 1, 10)]

    result, first = first_bucket("120")
    assert first and first[0].amount == Decimal("-180")

    # Coverage may not exceed the budget: the bucket disappears instead of
    # producing a negative expense, and later buckets stay at the full budget.
    result, first = first_bucket("400")
    assert first == []
    assert {entry.amount for entry in _budget_entries(result)} == {Decimal("-300")}
    assert all(entry.amount < 0 for entry in result.entries)


def _salary_history():
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


def _groceries_history():
    return [
        _event("groceries_nov", "2025-11-05", "groceries", "300"),
        _event("groceries_dec", "2025-12-05", "groceries", "300"),
    ]


def test_first_bucket_is_prorated_when_a_salary_is_projected():
    result = build_forecast(_salary_history() + _groceries_history(), "2026-01-20")

    assert _entries(result, "income"), "the fixture must project a salary"
    first = [entry for entry in _budget_entries(result) if entry.date == date(2026, 1, 20)]
    assert first, _budget_entries(result)
    # 12 days left in January: 300 * (12 + 1) / 30.
    assert first[0].amount == Decimal("-130")


def test_first_bucket_is_not_prorated_without_a_projected_salary():
    result = build_forecast(_groceries_history(), "2026-01-20")
    assert _entries(result, "income") == []
    first = [entry for entry in _budget_entries(result) if entry.date == date(2026, 1, 20)]
    assert first and first[0].amount == Decimal("-300")

    # The gate is the *projected* salary, not the history: ended employment stops
    # the projection, so the request month is reserved in full again.
    ended = build_forecast(
        _salary_history() + _groceries_history(),
        "2026-01-20",
        evidence_flags={"employment_ended"},
    )
    assert _entries(ended, "income") == []
    first = [entry for entry in _budget_entries(ended) if entry.date == date(2026, 1, 20)]
    assert first and first[0].amount == Decimal("-300")


def test_prorated_first_bucket_never_exceeds_the_full_monthly_budget():
    # 2026-01-01 is the 1st of a 31-day month, so (days_left + 1) / 30 = 32 / 30.
    # The remaining share of a month can never exceed the whole month's budget.
    result = build_forecast(_salary_history() + _groceries_history(), "2026-01-01")

    assert _entries(result, "income"), "the fixture must project a salary"
    first = [entry for entry in _budget_entries(result) if entry.date == date(2026, 1, 1)]
    assert first, _budget_entries(result)
    assert first[0].amount == Decimal("-300")


def test_stop_removes_the_budget_and_a_protected_stop_is_ignored_with_a_warning():
    stopped = build_forecast(
        _groceries_history(), "2026-01-10", suppressed_categories={"groceries"}
    )
    assert [entry for entry in stopped.entries if entry.category == "groceries"] == []

    protected = build_forecast(
        _groceries_history(),
        "2026-01-10",
        suppressed_categories={"groceries"},
        protected_categories={"groceries"},
    )
    assert _budget_entries(protected), "a protected category is never suppressed"
    flags = {warning["flag"] for warning in protected.warnings}
    assert "PROTECTED_CATEGORY_NOT_SUPPRESSED" in flags


def test_reduce_to_sets_the_monthly_budget_and_keeps_the_category():
    result = build_forecast(
        _groceries_history(),
        "2026-01-10",
        category_overrides={"groceries": Decimal("50")},
    )

    budget = _budget_entries(result)
    assert budget, "an override keeps the category in the forecast"
    assert {entry.category for entry in budget} == {"groceries"}
    assert {entry.amount for entry in budget} == {Decimal("-50")}
    assert Decimal("-300") not in {entry.amount for entry in result.entries}


def test_a_spending_change_override_wins_over_a_conflicting_expense_fact():
    fact = {
        "category": "groceries",
        "kind": "increase",
        "amount": None,
        "currency": "",
        "effective_date": "2026-01-01",
        "percent_change": Decimal("100"),
        "source_message_id": "message_test",
    }
    result = build_forecast(
        _groceries_history(),
        "2026-01-10",
        category_overrides={"groceries": Decimal("50")},
        expense_facts=[fact],
    )

    # Control: the fact alone doubles the budget, so the assertions below prove
    # the override replaced it rather than stacking on top of it.
    control = build_forecast(_groceries_history(), "2026-01-10", expense_facts=[fact])
    control_expenses = [entry for entry in control.entries if entry.kind == "expense"]
    assert control_expenses
    assert {entry.amount for entry in control_expenses} == {Decimal("-600")}

    budget = _budget_entries(result)
    assert budget, "the override keeps the category"
    assert {entry.amount for entry in budget} == {Decimal("-50")}
    assert Decimal("-600") not in {entry.amount for entry in result.entries}
    flags = {warning["flag"] for warning in result.warnings}
    assert "EXPENSE_FACT_OVERRIDDEN_BY_SPENDING_CHANGE" in flags


def test_monthly_budget_entries_are_deterministic_and_sorted():
    events = _groceries_history() + [
        _event("streaming_nov", "2025-11-09", "streaming", "20", event_type="subscription"),
        _event("streaming_dec", "2025-12-09", "streaming", "20", event_type="subscription"),
    ]
    first = build_forecast(events, "2026-01-10")
    second = build_forecast(events, "2026-01-10")

    signature = lambda result: [
        (entry.date, entry.category, entry.kind, entry.source, entry.reason, entry.amount)
        for entry in result.entries
    ]
    assert signature(first) == signature(second)
    keys = [(entry.date, entry.category, entry.kind, entry.source, entry.reason) for entry in first.entries]
    assert keys == sorted(keys)
    assert {(entry.date, entry.category) for entry in _budget_entries(first)} == {
        (date(2026, 1, 10), "groceries"),
        (date(2026, 1, 10), "streaming"),
        (date(2026, 2, 10), "groceries"),
        (date(2026, 2, 10), "streaming"),
        (date(2026, 3, 10), "groceries"),
        (date(2026, 3, 10), "streaming"),
        (date(2026, 4, 10), "groceries"),
        (date(2026, 4, 10), "streaming"),
    }


# 11. solve_safe_amount returns fail_closed=True with an empty ledger on STATUS_UNKNOWN.
def test_oracle_fails_closed_with_empty_ledger_on_unknown_status():
    events = [_event("e1", "2026-01-02", "rent", "1000", status="unknown")]
    profile = {
        "home_currency": "USD",
        "current_available_balance": "5000",
        "minimum_balance_to_keep": "2000",
    }
    result = solve_safe_amount(
        "u1", "2026-01-01", "15000", profile, events, [], set()
    )

    assert result["safe_amount"] == Decimal("0")
    assert result["earliest_date"] is None
    assert result["ledger"] == []
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "STATUS_UNKNOWN"


# 12. solve_request returns not_affordable/not_recommended on fail_closed=True.
def test_solver_returns_not_recommended_when_the_ledger_fails_closed(monkeypatch):
    def fake_safe_amount(*_args, **_kwargs):
        return {
            "safe_amount": Decimal("0"),
            "earliest_date": None,
            "ledger": [],
            "fail_closed": True,
            "fail_closed_reason": "FX_UNKNOWN",
        }

    monkeypatch.setattr(solver_mod, "solve_safe_amount", fake_safe_amount)
    options = pd.DataFrame(
        columns=[
            "payment_option_id",
            "request_id",
            "payment_method",
            "payment_amount",
            "number_of_payments",
            "first_payment_date",
            "payment_frequency_days",
            "financing_fee",
            "total_payable_amount",
        ]
    )
    profile = {
        "home_currency": "USD",
        "current_available_balance": "10000",
        "minimum_balance_to_keep": "2000",
        "payment_methods_user_will_consider": "full_payment|partial_payment|installments",
    }
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", profile, [], [], options, None, None, True
    )

    assert result["amount_safe_to_pay"] == "0"
    assert result["affordability_status"] == "not_affordable"
    assert result["recommended_payment_method"] == "not_recommended"
    assert result["payment_plan"] == "none"
    assert result["earliest_date_for_full_payment"] is None
    assert "fail closed" in result["decision_explanation"].lower()
