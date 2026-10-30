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


# --- Task 4: spending changes are first-class candidates ---------------------


def _subscription_history(category="cloud_storage", amount="30", minimum="10",
                          flexibility="reducible"):
    events = []
    for i, month in enumerate(("2026-01", "2026-02", "2026-03")):
        events.append({
            "event_id": f"{category}_{i}",
            "user_id": "u1",
            "event_type": "subscription",
            "category": category,
            "direction": "debit",
            "amount": amount,
            "currency": "USD",
            "event_date": f"{month}-05",
            "settlement_date": f"{month}-05",
            "status": "settled",
            "linked_event_id": "",
            "flexibility": flexibility,
            "minimum_allowed_amount": minimum,
        })
    return events


def test_reduce_to_overrides_the_forward_run_rate_and_keeps_the_category():
    """`reduce_to` sets the forward amount; it must not delete the category."""
    from code.changes import apply_changes
    from code.oracle import solve_safe_amount

    profile = {
        "home_currency": "USD",
        "current_available_balance": "1000",
        "minimum_balance_to_keep": "0",
        "expense_categories_to_protect": "",
        "expense_categories_user_is_willing_to_reduce": "cloud_storage",
        "expense_categories_user_is_willing_to_stop": "",
        "max_installment_months": "",
    }
    events = _subscription_history()
    flags = {("u1", "cloud_storage", "subscription")}
    base = solve_safe_amount("u1", "2026-04-01", "1000", profile, events, [], flags)
    # Task 8: the category is a 30/month budget posted on 2026-04-01, 05-01, 06-01.
    assert base["safe_amount"] == Decimal("910"), base

    action = {"event_id": "cloud_storage_2", "action": "reduce_to",
              "value": Decimal("10"), "category": "cloud_storage"}
    modified, suppressed, overrides = apply_changes(events, [action])
    res = solve_safe_amount(
        "u1", "2026-04-01", "1000", profile, modified, [], flags,
        suppressed_categories=suppressed, category_overrides=overrides,
    )
    forward = [e for e in res["forecast"] if e["category"] == "cloud_storage"]
    assert forward, "reduce_to must keep the category in the forward forecast"
    assert {Decimal(e["amount"]) for e in forward} == {Decimal("-10")}
    assert res["safe_amount"] == Decimal("970"), res
    # The in-window copy of the targeted row carries the new amount, not zero.
    assert next(e for e in modified if e["event_id"] == "cloud_storage_2")["amount"] == "10"

    # `stop` keeps the current semantics: the category leaves the forecast.
    stop = {"event_id": "cloud_storage_2", "action": "stop",
            "value": Decimal("0"), "category": "cloud_storage"}
    modified_stop, suppressed_stop, overrides_stop = apply_changes(events, [stop])
    stopped = solve_safe_amount(
        "u1", "2026-04-01", "1000", profile, modified_stop, [], flags,
        suppressed_categories=suppressed_stop, category_overrides=overrides_stop,
    )
    assert [e for e in stopped["forecast"] if e["category"] == "cloud_storage"] == []
    assert stopped["safe_amount"] == Decimal("1000")


def test_build_change_sets_is_bounded_and_prefers_smaller_sets():
    from code.changes import MAX_CHANGE_SETS, build_change_sets

    profile = {
        "home_currency": "USD",
        "current_available_balance": "8000",
        "minimum_balance_to_keep": "2000",
        "expense_categories_to_protect": "",
        "expense_categories_user_is_willing_to_stop": "dining",
        "expense_categories_user_is_willing_to_reduce": "",
    }
    events = [
        {
            "event_id": f"e{i}", "user_id": "u1", "event_type": "expense",
            "category": "dining", "direction": "debit", "amount": "100",
            "currency": "USD", "event_date": "2026-01-01",
            "settlement_date": "2026-01-01", "status": "settled",
            "flexibility": "stoppable", "minimum_allowed_amount": "",
        }
        for i in range(6)
    ]
    sets = build_change_sets("u1", profile, events, {("u1", "dining", "expense")})
    assert sets, "expected permitted change sets"
    assert len(sets) <= MAX_CHANGE_SETS
    rungs = [len(s) for s in sets]
    assert rungs == sorted(rungs), "smaller change sets must come first"
    assert set(rungs) <= {1, 2, 3}
    for combo in sets:
        assert len({a["event_id"] for a in combo}) == len(combo)
        assert all(a["action"] == "stop" for a in combo)
        assert all(a["category"] == "dining" for a in combo)
    # Nothing is permitted when the category is protected.
    protected = dict(profile, expense_categories_to_protect="dining")
    assert build_change_sets("u1", protected, events, {("u1", "dining", "expense")}) == []

