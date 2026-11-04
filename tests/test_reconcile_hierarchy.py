"""P1 Reconciliation TDD: Hierarchical conflict resolution.

Test cases cover AGENTS 6.3 conflict order:
1. Cancellation/Settlement/Amendment > Newer > Settled > Safer.
"""
from code.reconcile import resolve_event_amendment

def test_cancellation_wins_over_newer():
    event = {"event_id": "e1", "amount": "1000", "status": "pending"}
    msgs = [
        {"message_id": "m1", "related_event_id": "e1", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount is 1000"},
        {"message_id": "m2", "related_event_id": "e1", "sent_at": "2025-01-02T00:00:00Z", "message_text": "CANCELLED"},
        {"message_id": "m3", "related_event_id": "e1", "sent_at": "2025-01-03T00:00:00Z", "message_text": "Amount is 1200"},
    ]
    amended, note = resolve_event_amendment(event, msgs)
    # Cancellation (m2) should win over newer (m3)
    assert "CANCELLED" in amended["status"] or "cancelled" in amended.get("status", "").lower()

def test_newer_wins_over_settled():
    event = {"event_id": "e1", "amount": "1000", "status": "settled"}
    msgs = [
        {"message_id": "m1", "related_event_id": "e1", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Settle at 1000 USD"},
        {"message_id": "m2", "related_event_id": "e1", "sent_at": "2025-01-02T00:00:00Z", "message_text": "Amended to 1100 USD"},
    ]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "1100"

def test_safer_interpretation_fallback():
    # Same-timestamp conflicting amendments: newer cannot decide, so the
    # financially safer interpretation wins (larger expense reserves more).
    event = {"event_id": "e9", "amount": "1000", "status": "pending"}
    msgs = [
        {"message_id": "m1", "related_event_id": "e9", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 1000"},
        {"message_id": "m2", "related_event_id": "e9", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 2000"},
    ]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "2000"
    assert "safer" in note.lower()


def test_safer_interpretation_fallback_order_independent():
    # Safer wins regardless of input order.
    event = {"event_id": "e9", "amount": "1000", "status": "pending"}
    msgs = [
        {"message_id": "m2", "related_event_id": "e9", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 2000"},
        {"message_id": "m1", "related_event_id": "e9", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 1000"},
    ]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "2000"
    assert "safer" in note.lower()


def test_income_tie_safer_is_min():
    # Same-timestamp income amendments: safer = smaller income (conservative).
    event = {"event_id": "e10", "amount": "1000", "status": "pending",
             "direction": "credit", "event_type": "income"}
    msgs = [
        {"message_id": "m1", "related_event_id": "e10", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 1000"},
        {"message_id": "m2", "related_event_id": "e10", "sent_at": "2025-01-01T00:00:00Z", "message_text": "Amount USD 2000"},
    ]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "1000"
    assert "safer" in note.lower()