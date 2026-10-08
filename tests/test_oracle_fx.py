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
    assert len(res["ledger"]) == 91  # fail-closed keeps 91-point contract


def _fx_hist(date, amount, currency, eid, direction="debit", status="settled"):
    return {"event_date": date, "settlement_date": date, "amount": amount, "direction": direction,
            "status": status, "category": "rent", "user_id": "u1", "currency": currency,
            "event_type": "expense", "event_id": eid, "linked_event_id": ""}


def test_foreign_history_converted_to_home_currency():
    # Two EUR 100 expenses with exact 2.0 EUR->USD rate must forecast -200 USD,
    # not raw -100. Interval 30d: 2025-11-01,2025-12-01 -> first forecast 2026-01-30 (day 28).
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0", "expense_categories_to_protect": "rent"}
    evs = [_fx_hist("2025-11-01", "100", "EUR", "fh1"),
           _fx_hist("2025-12-01", "100", "EUR", "fh2")]
    fx = [{"rate_date": "2025-11-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"},
          {"rate_date": "2025-12-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"}]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, fx, flags)
    assert res["ledger"][0] == Decimal("5000")
    assert res["ledger"][28] == Decimal("4800")


def test_missing_rate_history_skipped():
    # EUR 100 @2.0 (=200 home) + XYZ 1000 with no rate: missing observation is skipped,
    # so average stays -200 and forecast is 4800 (not polluted 4450, not raw 4900).
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0", "expense_categories_to_protect": "rent"}
    evs = [_fx_hist("2025-11-01", "100", "EUR", "fh1"),
           _fx_hist("2025-12-01", "1000", "XYZ", "fh2")]
    fx = [{"rate_date": "2025-11-01", "from_currency": "EUR", "to_currency": "USD", "rate": "2.0"}]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, fx, flags)
    assert res["ledger"][28] == Decimal("4800")