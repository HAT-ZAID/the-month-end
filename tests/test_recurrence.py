"""P1 recurrence detection (TDD): >=3 observations + CV gate, conservative."""
from code.recurrence import detect_recurrence


def test_three_stable_observations_flags_recurring():
    events = [
        {"user_id": "u1", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u1", "category": "rent", "event_type": "expense",
         "amount": "1050", "status": "settled"},
        {"user_id": "u1", "category": "rent", "event_type": "expense",
         "amount": "980", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u1", "rent", "expense") in flags


def test_three_high_variance_not_recurring():
    events = [
        {"user_id": "u2", "category": "utilities", "event_type": "expense",
         "amount": "100", "status": "settled"},
        {"user_id": "u2", "category": "utilities", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u2", "category": "utilities", "event_type": "expense",
         "amount": "5000", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u2", "utilities", "expense") not in flags  # CV exceeds conservative threshold


def test_two_observations_not_recurring():
    events = [
        {"user_id": "u3", "category": "food", "event_type": "expense",
         "amount": "500", "status": "settled"},
        {"user_id": "u3", "category": "food", "event_type": "expense",
         "amount": "520", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u3", "food", "expense") not in flags  # fewer than 3 observations


def test_different_categories_do_not_merge():
    events = [
        {"user_id": "u4", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u4", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u4", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u4", "category": "utilities", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u4", "category": "utilities", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u4", "category": "utilities", "event_type": "expense",
         "amount": "1000", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u4", "rent", "expense") in flags
    assert ("u4", "utilities", "expense") in flags
    assert ("u4", "food", "expense") not in flags


def test_income_and_expense_are_separate_groups():
    events = [
        {"user_id": "u5", "category": "salary", "event_type": "income",
         "amount": "5000", "status": "settled"},
        {"user_id": "u5", "category": "salary", "event_type": "income",
         "amount": "5000", "status": "settled"},
        {"user_id": "u5", "category": "salary", "event_type": "income",
         "amount": "5000", "status": "settled"},
        {"user_id": "u5", "category": "salary", "event_type": "expense",
         "amount": "100", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u5", "salary", "income") in flags
    assert ("u5", "salary", "expense") not in flags  # only 1 observation


def test_non_string_amounts_ignored_without_crash():
    from code.recurrence import _amount

    assert _amount({"amount": [100]}) is None
    assert _amount({"amount": {"v": 1}}) is None
    assert _amount({"amount": None}) is None
    # inf leak: non-finite amounts must be ignored like NaN (see code/recurrence.py _amount)
    assert _amount({"amount": float("inf")}) is None
    assert _amount({"amount": float("-inf")}) is None
    assert _amount({"amount": "Infinity"}) is None
    assert _amount({"amount": "-Infinity"}) is None
    assert _amount({"amount": "inf"}) is None
    events = [
        {"user_id": "u6", "category": "rent", "event_type": "expense",
         "amount": [100], "status": "settled"},
        {"user_id": "u6", "category": "rent", "event_type": "expense",
         "amount": {"v": 1}, "status": "settled"},
        {"user_id": "u6", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u6", "rent", "expense") not in flags  # only 1 valid observation


def test_bad_str_amount_does_not_crash():
    from code.recurrence import _amount, detect_recurrence

    class BadStr:
        def __str__(self):
            return 123  # non-string -> str() raises TypeError

        def __repr__(self):
            return "BadStr()"

    assert _amount({"amount": BadStr()}) is None
    events = [
        {"user_id": "u7", "category": "rent", "event_type": "expense",
         "amount": BadStr(), "status": "settled"},
        {"user_id": "u7", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled"},
        {"user_id": "u7", "category": "rent", "event_type": "expense",
         "amount": "1010", "status": "settled"},
    ]
    flags = detect_recurrence(events)
    assert ("u7", "rent", "expense") not in flags  # only 2 valid observations


def test_recurrence_ignores_non_countable_statuses():
    events = [
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "settled", "direction": "debit"},
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "1010", "status": "settled", "direction": "debit"},
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "990", "status": "settled", "direction": "debit"},
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "100000", "status": "failed", "direction": "debit"},
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "100000", "status": "mystery", "direction": "debit"},
        {"user_id": "u8", "category": "rent", "event_type": "expense",
         "amount": "100000", "status": "pending", "direction": "credit"},
    ]
    assert ("u8", "rent", "expense") in detect_recurrence(events)


def test_stable_events_without_status_preserve_legacy_recurrence():
    events = [
        {"user_id": "u9", "category": "rent", "event_type": "expense", "amount": "1000"},
        {"user_id": "u9", "category": "rent", "event_type": "expense", "amount": "1010"},
        {"user_id": "u9", "category": "rent", "event_type": "expense", "amount": "990"},
    ]
    assert ("u9", "rent", "expense") in detect_recurrence(events)


def test_pending_events_without_direction_are_not_recurrence_evidence():
    events = [
        {"user_id": "u10", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "pending"},
        {"user_id": "u10", "category": "rent", "event_type": "expense",
         "amount": "1010", "status": "pending"},
        {"user_id": "u10", "category": "rent", "event_type": "expense",
         "amount": "990", "status": "pending"},
    ]
    assert ("u10", "rent", "expense") not in detect_recurrence(events)


def test_pending_events_with_unknown_direction_are_not_recurrence_evidence():
    events = [
        {"user_id": "u11", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "pending", "direction": "mystery"},
        {"user_id": "u11", "category": "rent", "event_type": "expense",
         "amount": "1010", "status": "pending", "direction": "mystery"},
        {"user_id": "u11", "category": "rent", "event_type": "expense",
         "amount": "990", "status": "pending", "direction": "mystery"},
    ]
    assert ("u11", "rent", "expense") not in detect_recurrence(events)


def test_scheduled_and_pending_debit_cash_events_are_recurrence_evidence():
    scheduled = [
        {"user_id": "u12", "category": "rent", "event_type": "expense",
         "amount": "1000", "status": "scheduled", "direction": "debit"},
        {"user_id": "u12", "category": "rent", "event_type": "expense",
         "amount": "1010", "status": "scheduled", "direction": "debit"},
        {"user_id": "u12", "category": "rent", "event_type": "expense",
         "amount": "990", "status": "scheduled", "direction": "debit"},
    ]
    pending_debit = [dict(e, user_id="u13", status="pending") for e in scheduled]
    assert ("u12", "rent", "expense") in detect_recurrence(scheduled)
    assert ("u13", "rent", "expense") in detect_recurrence(pending_debit)


def test_non_cash_events_are_not_recurrence_evidence():
    events = [
        {"user_id": "u14", "category": "shares", "event_type": "investment",
         "amount": "1000", "status": "settled", "direction": "non_cash"},
        {"user_id": "u14", "category": "shares", "event_type": "investment",
         "amount": "1010", "status": "settled", "direction": "non_cash"},
        {"user_id": "u14", "category": "shares", "event_type": "investment",
         "amount": "990", "status": "settled", "direction": "non_cash"},
    ]
    assert ("u14", "shares", "investment") not in detect_recurrence(events)
