from decimal import Decimal
from code.oracle import solve_safe_amount

def _profile(**over):
    base = {"home_currency": "USD", "current_available_balance": "10000",
            "minimum_balance_to_keep": "2000"}
    base.update(over)
    return base

def _event(**over):
    row = {"event_id": "e1", "user_id": "u1", "event_type": "expense",
           "category": "rent", "direction": "debit", "amount": "9000",
           "currency": "USD", "event_date": "2026-01-05",
           "settlement_date": "2026-01-05", "status": "settled"}
    row.update(over)
    return row

def test_unknown_direction_fails_closed():
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(),
                               [_event(direction="mystery")], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "DIRECTION_UNKNOWN"
    assert result["safe_amount"] == Decimal("0")

def test_negative_debit_amount_cannot_become_a_credit():
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(),
                               [_event(amount="-9000")], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "AMOUNT_INVALID"

def test_invalid_reserve_fails_closed():
    for bad in ("garbage", "NaN", "Infinity", ""):
        result = solve_safe_amount("u1", "2026-01-01", "7000",
                                   _profile(minimum_balance_to_keep=bad),
                                   [_event()], [], set())
        assert result["fail_closed"] is True
        assert result["fail_closed_reason"] == "RESERVE_INVALID"

def test_active_event_without_date_fails_closed():
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(),
                               [_event(settlement_date="", event_date="")], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "DATE_UNKNOWN"

def test_valid_rows_still_compute():
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(), [_event()], [], set())
    assert result["fail_closed"] is False

def test_overflowing_amount_fails_closed_without_raising():
    # A finite Decimal with an extreme exponent still overflows when the FX
    # conversion scales it (the default context traps Overflow). The ledger must
    # fail closed with a named reason instead of raising into the caller.
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(),
                               [_event(amount="1e1000000")], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "AMOUNT_INVALID"
