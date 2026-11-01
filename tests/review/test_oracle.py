"""P1 Independent Oracle (TDD): 90-day daily-exact ledger reconstruction.

Launder logic:
- safe_amount: max X <= requested_amount such that balance - X >= min_balance
  for all t in [0, 90].
- earliest_date: first t in [0, 90] such that balance >= requested_amount.
"""
from decimal import Decimal
from datetime import date, timedelta

from code.oracle import solve_safe_amount


def test_safe_amount_limited_by_future_dip():
    # user has 10,000 now, min_balance 2,000.
    # but on day 10, they have a fixed expense of 7,000.
    # safe_amount should be 1,000 (so balance becomes 9,000, then 2,000 on day 10).
    profile = {"home_currency": "USD", "current_available_balance": "10000", "minimum_balance_to_keep": "2000"}
    events = [
        {"event_date": "2026-01-10", "settlement_date": "2026-01-10", "amount": "7000", "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1", "currency": "USD"}
    ]
    fx = [] # USD only
    flags = set()
    
    res = solve_safe_amount("u1", "2026-01-01", "5000", profile, events, fx, flags)
    assert res["safe_amount"] == Decimal("1000")
    # Wait: balance starts at 10k. if we pay 5000 now, balance=5k. On day 10, 5k-7k = -2k < 2k.
    # Max X such that 10k - X - 7k >= 2k  => X <= 1k.
    # Wait: balance starts at 10k. if we pay 5000 now, balance=5k. On day 10, 5k-7k = -2k < 2k.
    # Max X such that 10k - X - 7k >= 2k  => X <= 1k.
    # earliest_date for full 5000: needs balance >= 7000 (5k + 2k min). 
    # But balance is 10k on day 1. So earliest_date = 2026-01-01.
    # wait — earliest_date for FULL payment: does it include the min_balance check? Yes.
    # balance on day 1 is 10k. 10k - 5k = 5k >= 2k. OK.
    # So safe_amount = 1000 (to survive day 10 dip), earliest_date = "2026-01-01" (for the requested 5000).
    # No, that's wrong. earliest_date is when a FULL payment is safe.
    # If we pay 5000 on day 1, we dip to -2k on day 10. Not safe.
    # We must wait until... but here the balance only drops.
    # So earliest_date = None.
    assert res["earliest_date"] is None


def test_earliest_date_found_after_income():
    # user has 1,000 now, min_balance 500. Req 2,000.
    # Day 5: income 5,000.
    profile = {"home_currency": "USD", "current_available_balance": "1000", "minimum_balance_to_keep": "500"}
    events = [
        {"event_date": "2026-01-05", "settlement_date": "2026-01-05", "amount": "5000", "direction": "credit", "status": "settled", "category": "salary", "user_id": "u1", "currency": "USD"}
    ]
    fx = []
    flags = set()
    
    res = solve_safe_amount("u1", "2026-01-01", "2000", profile, events, fx, flags)
    # safe_amount: 1000 - X >= 500 => X <= 500.
    assert res["safe_amount"] == Decimal("500")
    # earliest_date: day 5 balance = 1000+5000 = 6000. 6000 - 2000 = 4000 >= 500. OK.
    assert res["earliest_date"] == "2026-01-05"


def test_zero_safe_amount_when_already_below_min():
    profile = {"home_currency": "USD", "current_available_balance": "1000", "minimum_balance_to_keep": "1500"}
    events = []
    fx = []
    flags = set()
    res = solve_safe_amount("u1", "2026-01-01", "1000", profile, events, fx, flags)
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None