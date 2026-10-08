"""Oracle-level cash-state tests (TDD, no mocks): pending/failed/cancelled/unrealized/NaN/FX.

Spec basis (AGENTS 6.1/6.3, problem_statement): pending credits ignored,
pending debits reserved, failed/cancelled/unrealized ignored, NaN/blank
fail-closed (safe 0), FX-prior fail-closed (safe 0).
"""
from decimal import Decimal

from code.oracle import solve_safe_amount


def _profile(balance="5000", minimum="2000"):
    return {"home_currency": "USD", "current_available_balance": balance, "minimum_balance_to_keep": minimum}


def _ev(date, amount, direction, status, currency="USD", category="rent"):
    return {"event_date": date, "settlement_date": date, "amount": amount, "direction": direction,
            "status": status, "category": category, "user_id": "u1", "currency": currency}


def test_pending_credit_ignored():
    evs = [_ev("2026-01-02", "10000", "credit", "pending", category="refund")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("3000")


def test_pending_debit_reserved():
    evs = [_ev("2026-01-02", "1000", "debit", "pending")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("2000")


def test_failed_ignored():
    evs = [_ev("2026-01-02", "1000", "debit", "failed")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("3000")


def test_cancelled_ignored():
    evs = [_ev("2026-01-02", "1000", "debit", "cancelled")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("3000")


def test_unrealized_ignored():
    evs = [_ev("2026-01-02", "10000", "non_cash", "unrealized", category="invest")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("3000")


def test_nan_amount_fail_closed():
    evs = [_ev("2026-01-02", "nan", "debit", "settled")]
    res = solve_safe_amount("u1", "2026-01-01", "2000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None


def test_blank_amount_fail_closed():
    evs = [_ev("2026-01-02", "", "debit", "settled")]
    res = solve_safe_amount("u1", "2026-01-01", "2000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None


def test_fx_prior_fail_closed():
    evs = [_ev("2026-01-10", "100", "debit", "settled", currency="EUR")]
    fx = [{"rate_date": "2026-01-09", "from_currency": "EUR", "to_currency": "USD", "rate": "1.1"}]
    res = solve_safe_amount("u1", "2026-01-01", "5000",
                            {"home_currency": "USD", "current_available_balance": "10000", "minimum_balance_to_keep": "2000"},
                            evs, fx, set())
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None


def test_unknown_status_fails_closed():
    # Unknown status is ambiguous evidence and must fail closed.
    evs = [_ev("2026-01-02", "1000", "debit", "weird")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None


def test_blank_status_fails_closed():
    evs = [_ev("2026-01-02", "1000", "debit", "")]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] == Decimal("0")
    assert res["earliest_date"] is None


def test_duplicate_event_id_dedup_but_linked_successor_is_preserved():
    # The exact duplicate ID counts once; the distinct linked successor is a
    # valid cash row and must remain in the ledger.
    base = {"event_date": "2026-01-02", "settlement_date": "2026-01-02", "amount": "1000",
            "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
            "currency": "USD", "event_type": "expense"}
    e1 = dict(base, event_id="dup1", linked_event_id="")
    e2 = dict(base, event_id="dup1", linked_event_id="")  # exact duplicate id
    e3 = dict(base, event_id="retry1", linked_event_id="dup1")  # linked retry same date/amount
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), [e1, e2, e3], [], set())
    # Two $1000 expenses: 5000-2000=3000, leaving 1000 above the 2000 floor.
    assert res["safe_amount"] == Decimal("1000")


def test_transitive_same_direction_linked_lifecycle_is_preserved():
    # A -> B -> C has distinct identities. The link alone does not prove
    # duplication, so all three cash rows remain.
    base = {"event_date": "2026-01-02", "settlement_date": "2026-01-02",
            "amount": "1000", "direction": "debit", "status": "settled",
            "category": "rent", "user_id": "u1", "currency": "USD",
            "event_type": "expense"}
    events = [
        dict(base, event_id="a", linked_event_id=""),
        dict(base, event_id="b", linked_event_id="a"),
        dict(base, event_id="c", linked_event_id="b"),
    ]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())
    assert res["safe_amount"] == Decimal("0")


def test_linked_refund_pair_both_kept():
    # Opposite-direction linked pair is a refund lifecycle (expense vs refund):
    # the link alone does not determine cash, so BOTH rows are kept (net delta).
    # Same-date $1000 debit + $400 credit => net -600 => ledger 4400, safe 2400.
    # Pins the by-design same-direction-only dedup (no impl change expected).
    base = {"event_date": "2026-01-02", "settlement_date": "2026-01-02",
            "status": "settled", "category": "rent", "user_id": "u1",
            "currency": "USD", "event_type": "expense"}
    e1 = dict(base, event_id="exp1", linked_event_id="", amount="1000", direction="debit")
    e2 = dict(base, event_id="ref1", linked_event_id="exp1", amount="400", direction="credit")
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), [e1, e2], [], set())
    assert res["ledger"][0] == Decimal("5000")
    assert res["ledger"][1] == Decimal("4400")
    assert res["safe_amount"] == Decimal("2400")


def test_expense_history_credit_does_not_lower_forecast():
    # A credit-direction row in expense history is a refund, not a negative expense:
    # forecast must average -abs(d) (full 1000), not (-1000-1000+1000)/3 (optimistic 333).
    # Intervals [30,30] median 30: last 2025-12-31 +30d => 2026-01-30 (day 28) => 4000.
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0", "expense_categories_to_protect": "rent"}
    evs = [
        {"event_date": "2025-11-01", "settlement_date": "2025-11-01", "amount": "1000",
         "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h1", "linked_event_id": ""},
        {"event_date": "2025-12-01", "settlement_date": "2025-12-01", "amount": "1000",
         "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h2", "linked_event_id": ""},
        {"event_date": "2025-12-31", "settlement_date": "2025-12-31", "amount": "1000",
         "direction": "credit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h3", "linked_event_id": ""},
    ]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, [], flags)
    assert res["ledger"][28] == Decimal("4000")


def test_protected_split_strips_whitespace_and_nan():
    profile = {"home_currency": "USD", "current_available_balance": "5000",
               "minimum_balance_to_keep": "0",
               "expense_categories_to_protect": " rent | nan | | "}
    evs = [
        {"event_date": "2025-11-01", "settlement_date": "2025-11-01", "amount": "1000",
         "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h1", "linked_event_id": ""},
        {"event_date": "2025-12-01", "settlement_date": "2025-12-01", "amount": "1000",
         "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h2", "linked_event_id": ""},
        {"event_date": "2026-01-01", "settlement_date": "2026-01-01", "amount": "1000",
         "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1",
         "currency": "USD", "event_type": "expense", "event_id": "h3", "linked_event_id": ""},
    ]
    flags = {("u1", "rent", "expense")}
    res = solve_safe_amount("u1", "2026-01-02", "5000", profile, evs, [], flags)
    # Exact forecast: intervals [30,31] median-floor 30, last 2026-01-01 +30d
    # => 2026-01-31 (day 29) at -1000 => 4000; day 0 still 5000.
    assert res["ledger"][0] == Decimal("5000")
    assert res["ledger"][29] == Decimal("4000")


def test_missing_keys_do_not_crash():
    evs = [{"event_date": "2026-01-02", "settlement_date": "2026-01-02", "user_id": "u1"}]
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["safe_amount"] >= Decimal("0")
    assert len(res["ledger"]) == 91


def test_pending_debit_posts_on_settlement_date():
    # Pending debit reserves on its settlement date, not held from req_date (pinned behavior).
    evs = [_ev("2026-01-05", "1000", "debit", "pending")]
    for i, e in enumerate(evs):
        e.update({"event_id": f"p{i}", "linked_event_id": "", "event_type": "expense"})
    res = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), evs, [], set())
    assert res["ledger"][0] == Decimal("5000")
    assert res["ledger"][4] == Decimal("4000")
    assert res["safe_amount"] == Decimal("2000")
