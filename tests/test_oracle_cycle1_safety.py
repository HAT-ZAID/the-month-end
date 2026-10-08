from decimal import Decimal

from code.oracle import solve_safe_amount


def _profile():
    return {
        "home_currency": "USD",
        "current_available_balance": "5000",
        "minimum_balance_to_keep": "2000",
    }


def _event(**overrides):
    event = {
        "event_id": "event-1",
        "linked_event_id": "",
        "user_id": "u1",
        "event_date": "2026-01-02",
        "settlement_date": "2026-01-02",
        "amount": "1000",
        "direction": "debit",
        "status": "settled",
        "currency": "USD",
        "category": "rent",
    }
    event.update(overrides)
    return event


def test_same_direction_orphan_linked_rows_are_preserved():
    events = [
        _event(event_id="retry-a", linked_event_id="missing-parent"),
        _event(event_id="retry-b", linked_event_id="missing-parent"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("1000")


def test_exact_duplicate_event_ids_count_once():
    events = [
        _event(event_id="same-id"),
        _event(event_id="same-id"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("2000")


def test_parent_and_successor_linked_rows_are_both_preserved():
    events = [
        _event(event_id="parent"),
        _event(event_id="successor", linked_event_id="parent"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("1000")


def test_explicit_cancellation_link_cancels_parent_cash_flow():
    events = [
        _event(event_id="parent"),
        _event(event_id="cancel", linked_event_id="parent", status="cancelled"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("3000")


def test_exact_identity_cancellation_beats_settled_baseline():
    events = [
        _event(event_id="same-id", status="settled"),
        _event(event_id="same-id", status="cancelled"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("3000")


def test_exact_identity_settlement_beats_scheduled_baseline():
    events = [
        _event(event_id="same-id", status="scheduled", amount="1000"),
        _event(event_id="same-id", status="settled", amount="400"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("2600")


def test_same_direction_lifecycle_rows_with_distinct_ids_are_preserved():
    events = [
        _event(event_id="lifecycle-1"),
        _event(event_id="lifecycle-2", linked_event_id="lifecycle-1", amount="500"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("1500")


def test_opposite_direction_linked_rows_are_preserved():
    events = [
        _event(event_id="debit", amount="1000"),
        _event(event_id="refund", linked_event_id="debit", amount="400", direction="credit"),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["ledger"][1] == Decimal("4400")


def test_unknown_and_blank_statuses_do_not_count_as_cash():
    events = [
        _event(event_id="unknown", status="unknown"),
        _event(event_id="blank", status=""),
    ]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("0")
    assert result["earliest_date"] is None
    assert result["ledger"] == [Decimal("5000")] * 91


def test_fx_fail_closed_keeps_91_point_ledger_shape():
    events = [_event(currency="EUR")]

    result = solve_safe_amount("u1", "2026-01-01", "15000", _profile(), events, [], set())

    assert result["safe_amount"] == Decimal("0")
    assert result["earliest_date"] is None
    assert result["ledger"] == [Decimal("5000")] * 91
