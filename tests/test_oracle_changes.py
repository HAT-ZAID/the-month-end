from code.oracle import solve_safe_amount


PROFILE = {
    "home_currency": "USD",
    "current_available_balance": "10000",
    "minimum_balance_to_keep": "1000",
    "expense_categories_to_protect": "rent",
    "expense_categories_user_is_willing_to_stop": "streaming",
    "expense_categories_user_is_willing_to_reduce": "",
    "max_installment_months": "",
}

# Recurrence evidence for a stoppable subscription. Production obtains this from
# code.recurrence.detect_recurrence; tests pin it so they do not depend on that
# detector's internals.
FLAGS = {("u1", "streaming", "subscription")}


def _streaming(month: str) -> dict:
    return {
        "event_id": f"stream_{month}",
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
    }


def _history():
    # Four monthly observations so the median interval is 30 days and at least
    # two forecast occurrences land inside [request_date, request_date+90].
    return [_streaming(m) for m in ("2025-10", "2025-11", "2025-12", "2026-01")]


def test_baseline_forecasts_flexible_recurring_category():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    # Without the fix the flexible subscription is never forecast, so the ledger
    # is flat at the starting balance.
    assert min(base["ledger"]) < 10000


def test_suppressing_flexible_category_raises_safe_amount():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    stopped = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"streaming"},
    )
    assert min(stopped["ledger"]) > min(base["ledger"])
    assert stopped["safe_amount"] >= base["safe_amount"]
    # Suppressing the category removes every forecast occurrence of it.
    assert min(stopped["ledger"]) == 10000


def test_suppression_preserves_ledger_shape_and_day_zero():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    stopped = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"streaming"},
    )
    assert len(stopped["ledger"]) == 91
    assert len(base["ledger"]) == 91
    assert base["ledger"][0] == stopped["ledger"][0]


def test_no_suppression_matches_default_behavior():
    events = _history()
    explicit_none = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS, suppressed_categories=None
    )
    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    assert explicit_none["safe_amount"] == default["safe_amount"]
    assert explicit_none["ledger"] == default["ledger"]


def test_unknown_suppressed_category_is_ignored():
    events = _history()
    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    ignored = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"no_such_category", ""},
    )
    assert ignored["ledger"] == default["ledger"]
