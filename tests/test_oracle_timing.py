"""P1 Oracle balance sequence (TDD): payment timing vs event timing.

Symmetry check: payment on day i happens BEFORE day i events.
If bal=5000, min=2000, and day 1 has expense=4000:
Payment of 2000 on day 1:
1. start_bal = 5000
2. pay 2000 -> bal=3000 (>=2000 OK)
3. event -4000 -> bal=-1000 (<2000 FAIL)
Safe amount should be 1000 (5000-1000=4000; 4000-4000=0 < 2000... wait, no, 5000-1000=4000; 4000-4000=0 FAIL).
Actually safe amount should be 0 because any payment + the 4k expense will dip below 2k.
"""
from decimal import Decimal
from code.oracle import solve_safe_amount


def test_payment_before_events_timing():
    # bal=5000, min=2000, req=2000
    # day 1: expense 4000.
    profile = {"home_currency": "USD", "current_available_balance": "5000", "minimum_balance_to_keep": "2000"}
    events = [
        {"event_date": "2026-01-02", "settlement_date": "2026-01-02", "amount": "4000", "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1", "currency": "USD"}
    ]
    fx = []
    flags = set()
    
    # request_date = "2026-01-01" (day 0)
    # Day 0: bal 5000. Safe to pay X? 5000-X >= 2000 -> X <= 3000.
    # Day 1: bal 5000 - X - 4000 = 1000 - X.
    # For 1000 - X >= 2000 -> X <= -1000.
    # So safe_amount = 0.
    res = solve_safe_amount("u1", "2026-01-01", "2000", profile, events, fx, flags)
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None  # no safe full payment in 90-day window


def _hist(user="u1", cat="rent", etype="expense", date="2025-11-01", amount="1000",
          status="settled", direction="debit", eid="h"):
    return {"event_date": date, "settlement_date": date, "amount": amount, "direction": direction,
            "status": status, "category": cat, "user_id": user, "currency": "USD",
            "event_type": etype, "event_id": eid, "linked_event_id": ""}


def test_median_interval_uses_median_not_mean():
    # Intervals [30,30,90]: median 30, mean 50. Last history 2026-03-31, req 2026-04-01.
    # Median forecasts 2026-04-30 (day 29); mean would forecast 2026-05-20 (day 49).
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0",
               "expense_categories_to_protect": "rent"}
    evs = [
        _hist(date="2025-11-01", eid="h1"),
        _hist(date="2025-12-01", eid="h2"),
        _hist(date="2025-12-31", eid="h3"),
        _hist(date="2026-03-31", eid="h4"),
    ]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-04-01", "5000", profile, evs, [], flags)
    # day 29 = 2026-04-30 should already reflect the median forecast expense
    assert res["ledger"][29] == Decimal("4000")


def test_single_history_does_not_forecast():
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0",
               "expense_categories_to_protect": "rent"}
    evs = [_hist(date="2026-03-01", eid="only1")]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-04-01", "5000", profile, evs, [], flags)
    assert all(b == Decimal("5000") for b in res["ledger"])


def test_forecast_average_excludes_failed_and_pending_credit():
    # 3x settled 1000 + failed 10000 + pending-credit 10000 => avg must stay 1000.
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0",
               "expense_categories_to_protect": "rent"}
    evs = [
        _hist(date="2025-11-01", eid="h1", amount="1000"),
        _hist(date="2025-12-01", eid="h2", amount="1000"),
        _hist(date="2025-12-31", eid="h3", amount="1000"),
        _hist(date="2025-12-15", eid="bad1", amount="10000", status="failed"),
        _hist(date="2025-12-20", eid="bad2", amount="10000", status="pending", direction="credit"),
    ]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, [], flags)
    # first forecast ~2026-01-30 (day 28): 5000-1000=4000 if avg 1000; polluted avg would be far lower
    assert res["ledger"][28] == Decimal("4000")