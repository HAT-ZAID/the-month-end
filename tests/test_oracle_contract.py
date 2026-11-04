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

def test_a_negative_fx_rate_cannot_turn_a_debit_into_a_credit():
    # 5000 EUR at the signed rate -1 would become +5000 and raise safe from 0
    # to 6000. It is not a rate: the row fails closed as FX_UNKNOWN.
    result = solve_safe_amount(
        "u1", "2026-01-01", "6000", _profile(),
        [_event(amount="5000", currency="EUR")],
        [{"from_currency": "EUR", "to_currency": "USD",
          "rate_date": "2026-01-05", "rate": "-1"}],
        set(),
    )
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "FX_UNKNOWN"
    assert result["safe_amount"] == Decimal("0")


def test_a_non_numeric_fx_rate_fails_closed_without_raising():
    result = solve_safe_amount(
        "u1", "2026-01-01", "6000", _profile(),
        [_event(amount="5000", currency="EUR")],
        [{"from_currency": "EUR", "to_currency": "USD",
          "rate_date": "2026-01-05", "rate": "abc"}],
        set(),
    )
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "FX_UNKNOWN"
    assert result["safe_amount"] == Decimal("0")


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

def test_overflowing_opening_balance_fails_closed_without_raising():
    # A finite opening balance past the context exponent range overflows on the
    # first `curr_bal += day_delta`; that is an unrepresentable balance, not a
    # crash for the TUI/position callers.
    result = solve_safe_amount("u1", "2026-01-01", "7000",
                               _profile(current_available_balance="1e1000000"),
                               [_event()], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "BALANCE_INVALID"

def test_overflowing_same_day_sum_fails_closed_without_raising():
    # Two individually representable deltas can overflow when summed on one day.
    result = solve_safe_amount("u1", "2026-01-01", "7000", _profile(),
                               [_event(event_id="e1", amount="9e999999"),
                                _event(event_id="e2", amount="9e999999")], [], set())
    assert result["fail_closed"] is True
    assert result["fail_closed_reason"] == "AMOUNT_INVALID"
