"""P2 Change Search (TDD): exhaustive search of <=3 combinations to find safe plans.

A 'change' is a stop or reduction of a flexible event.
Launder logic:
- Only reducible/stoppable events are candidates.
- Combinations of 1, 2, or 3 changes are tried in order.
- A change is 'safe' if the resulting balance simulation (P1 oracle) is green.
"""
from decimal import Decimal
from code.oracle import solve_safe_amount
from code.changes import search_changes


def test_no_changes_needed():
    # user has 10,000, req 5,000, min 2,000. safe_amount = 8,000.
    profile = {"home_currency": "USD", "current_available_balance": "10000", "minimum_balance_to_keep": "2000"}
    events = []
    fx = []
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, fx)
    assert res == []


def test_single_stop_makes_safe():
    # bal 8,000, min 2,000, req 5,000.
    # Day 1: expense 4,000. 8k - 5k - 4k = -1k < 2k.
    # Stop the 4,000: 8,000 - 5,000 = 3,000 >= 2,000. OK.
    profile = {"home_currency": "USD", "current_available_balance": "8000", "minimum_balance_to_keep": "2000",
               "expense_categories_user_is_willing_to_stop": "rent"}
    events = [
        {"event_id": "event_0", "event_type": "expense", "event_date": "2026-01-01", "settlement_date": "2026-01-01", "amount": "4000", "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1", "currency": "USD", "flexibility": "stoppable"}
    ]
    fx = []
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, fx,
                         {("u1", "rent", "expense")})
    assert res == ["stop:event_0"]


def test_reduction_makes_safe():
    # bal 7,000, min 2,000, req 5,000.
    # Day 1: expense 4,000. 7k - 5k - 4k = -2k < 2k.
    # Reduce 4,000 to 0: 7k - 5k = 2k >= 2k. OK.
    profile = {"home_currency": "USD", "current_available_balance": "7000", "minimum_balance_to_keep": "2000",
               "expense_categories_user_is_willing_to_reduce": "rent"}
    events = [
        {"event_id": "event_1", "event_type": "expense", "event_date": "2026-01-01", "settlement_date": "2026-01-01", "amount": "4000", "direction": "debit", "status": "settled", "category": "rent", "user_id": "u1", "currency": "USD", "flexibility": "reducible", "minimum_allowed_amount": "0"}
    ]
    fx = []
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, fx,
                         {("u1", "rent", "expense")})
    assert res == ["reduce_to:event_1:0"]


def _base_profile(**over):
    p = {"home_currency": "USD", "current_available_balance": "8000", "minimum_balance_to_keep": "2000"}
    p.update(over)
    return p


def _exp(eid, cat="dining", amt="4000", sdate="2026-01-01", flex="stoppable", **over):
    e = {"event_id": eid, "user_id": "u1", "event_type": "expense", "category": cat,
         "direction": "debit", "amount": amt, "currency": "USD",
         "event_date": sdate, "settlement_date": sdate, "status": "settled",
         "flexibility": flex}
    e.update(over)
    return e


def test_protected_category_event_ignored():
    # Stopping this event WOULD make it safe, but dining is protected -> must be ignored.
    profile = _base_profile(expense_categories_to_protect="dining")
    events = [_exp("event_p", cat="dining")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                          {("u1", "dining", "expense")}) == []


def test_non_permitted_category_event_ignored():
    # User only permits stopping dining; entertainment event must be ignored.
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("event_np", cat="entertainment")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                          {("u1", "entertainment", "expense")}) == []


def test_out_of_window_event_ignored():
    # Fixed in-window 4000 forces unsafe; the only flexible event is 151d out (2026-06-01).
    profile = _base_profile()
    events = [
        _exp("event_fixed", cat="rent", flex="fixed"),
        _exp("event_far", cat="dining", sdate="2026-06-01"),
    ]
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                         {("u1", "dining", "expense")})
    assert res == []
    assert all("event_far" not in r for r in res)


def test_stop_reduce_same_event_never_combined():
    # Two dual-flex 3000 events; only the pair solves it. Same-eid stop+reduce is forbidden (:198).
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining",
                            expense_categories_user_is_willing_to_reduce="dining")
    events = [_exp("event_a", amt="3000", flex="reducible_or_stoppable", minimum_allowed_amount="1000"),
              _exp("event_b", amt="3000", flex="reducible_or_stoppable", minimum_allowed_amount="1000")]
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                         {("u1", "dining", "expense")})
    assert len(res) == 2
    eids = [r.split(":")[-2] if r.startswith("reduce_to") else r.split(":")[1] for r in res]
    assert len(set(eids)) == len(eids)  # no event appears twice


def test_never_returns_more_than_three():
    # Five 2000 stoppables; 3 stops save 6000 -> balance 4000 -> safe 2000 < 5000. Needs 4, capped at 3.
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp(f"event_m{i}", cat="dining", amt="2000") for i in range(5)]
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                         {("u1", "dining", "expense")})
    assert len(res) <= 3


def test_two_change_only_solvable_returns_two():
    # Two 3000 stoppables; one stop -> safe 3000 < 5000; both -> safe 5000.
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("event_2a", amt="3000"), _exp("event_2b", amt="3000")]
    res = search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                         {("u1", "dining", "expense")})
    assert len(res) == 2
    assert set(res) == {"stop:event_2a", "stop:event_2b"}


def test_larger_savings_wins_among_single_changes():
    # Heterogeneous amounts: either single stop makes it safe, but the
    # larger-savings stop must win regardless of combo/event order.
    # bal 8000, min 2000, req 5000 -> remaining <= 1000 is safe.
    # total 1500 is unsafe; stopping either (leaving 500 or 1000) is safe.
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("event_small", cat="dining", amt="500"),
              _exp("event_big", cat="dining", amt="1000")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                          {("u1", "dining", "expense")}) == ["stop:event_big"]
    events_rev = [_exp("event_big", cat="dining", amt="1000"),
                  _exp("event_small", cat="dining", amt="500")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events_rev, [],
                          {("u1", "dining", "expense")}) == ["stop:event_big"]


def test_search_changes_requires_recurrence_evidence():
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("event_big", cat="dining", amt="1000"),
              _exp("event_small", cat="dining", amt="500")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, []) == []
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [], set()) == []
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [], {("u1", "dining", "expense")}) == ["stop:event_big"]


def test_one_time_event_without_recurrence_evidence_is_rejected():
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("one_time", amt="4000")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                          {("u1", "other", "expense")}) == []


def test_cross_user_event_is_rejected_even_with_matching_recurrence_evidence():
    profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
    events = [_exp("other_user", amt="4000", user_id="u2")]
    assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                          {("u2", "dining", "expense")}) == []


def test_search_changes_stops_a_subscription_that_only_exists_in_history():
    """Gold samples prove subscriptions are valid change targets even when every
    historical row predates request_date."""
    from decimal import Decimal
    from code.changes import search_changes

    profile = {
        "home_currency": "USD",
        "current_available_balance": "4600",
        "minimum_balance_to_keep": "4000",
        "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_stop": "streaming",
        "expense_categories_user_is_willing_to_reduce": "",
        "max_installment_months": "",
    }
    events = []
    for i, month in enumerate(("2026-01", "2026-02", "2026-03")):
        events.append({
            "event_id": f"s{i}",
            "user_id": "u1",
            "event_type": "subscription",
            "category": "streaming",
            "direction": "debit",
            "amount": "100",
            "currency": "USD",
            "event_date": f"{month}-05",
            "settlement_date": f"{month}-05",
            "status": "settled",
            "linked_event_id": "",
            "flexibility": "stoppable",
            "minimum_allowed_amount": "0",
        })
    result = search_changes(
        "u1", "2026-04-01", Decimal("600"), profile, events, [],
        {("u1", "streaming", "subscription")},
    )
    assert result, "expected a stop action for a stoppable recurring subscription"
    assert result[0].startswith("stop:")
