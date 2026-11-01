"""P1 Oracle FX fail-safe (TDD): FX_UNKNOWN -> safe_amount=0 (fail-closed)."""
from decimal import Decimal
from code.oracle import solve_safe_amount


def test_fx_unknown_forces_zero_safe_amount():
    # user has 10,000 now, min_balance 2,000. Req 5,000.
    # event exists but its FX rate is missing (FX_UNKNOWN).
    profile = {"home_currency": "USD", "current_available_balance": "10000", "minimum_balance_to_keep": "2000"}
    events = [
        {"event_date": "2026-01-10", "settlement_date": "2026-01-10", "amount": "100", "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1", "currency": "XYZ"}
    ]
    # FX rows doesn't contain XYZ
    fx = [] 
    flags = set()
    
    res = solve_safe_amount("u1", "2026-01-01", "5000", profile, events, fx, flags)
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None # cannot prove safe payment if an event is unknown
    # Task 3 contract: an ambiguous ledger is never synthesized; it is empty.
    assert res["ledger"] == []
    assert res["fail_closed"] is True
    assert res["fail_closed_reason"] == "FX_UNKNOWN"


def _fx_hist(date, amount, currency, eid, direction="debit", status="settled"):
    return {"event_date": date, "settlement_date": date, "amount": amount, "direction": direction,
            "status": status, "category": "rent", "user_id": "u1", "currency": currency,
            "event_type": "expense", "event_id": eid, "linked_event_id": ""}


def test_foreign_history_converted_to_home_currency():
    # Two EUR 100 expenses with an exact 2.0 EUR->USD rate form a monthly budget
    # of 200 home-currency units; an unconverted budget would be 100, so the
    # ledger proves the conversion.  Monthly accrual (Task 8) posts the budget on
    # the request date, and again one bucket later (2026-02-02, day 31).
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0", "expense_categories_to_protect": "rent"}
    evs = [_fx_hist("2025-11-01", "100", "EUR", "fh1"),
           _fx_hist("2025-12-01", "100", "EUR", "fh2")]
    fx = [{"rate_date": "2025-11-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"},
          {"rate_date": "2025-12-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"}]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, fx, flags)
    assert res["ledger"][0] == Decimal("4800")
    assert res["ledger"][28] == Decimal("4800")   # no second bucket yet
    assert res["ledger"][31] == Decimal("4600")   # 2026-02-02, next monthly budget


def test_missing_rate_history_skipped():
    # EUR 100 @2.0 (=200 home) in two months plus an XYZ row with no rate: the
    # unconvertible observation is skipped, so the monthly budget stays 200 and
    # day 0 is 4800.  Counting the XYZ row at face value (1000) would push the
    # December total to 1200 and the median budget to 700 (day 0 = 4300).
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0", "expense_categories_to_protect": "rent"}
    evs = [_fx_hist("2025-11-01", "100", "EUR", "fh1"),
           _fx_hist("2025-12-01", "100", "EUR", "fh2"),
           _fx_hist("2025-12-15", "1000", "XYZ", "fh3")]
    fx = [{"rate_date": "2025-11-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"},
          {"rate_date": "2025-12-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"}]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, fx, flags)
    assert res["ledger"][0] == Decimal("4800")
    assert res["ledger"][28] == Decimal("4800")