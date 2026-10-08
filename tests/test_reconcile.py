"""P1 message/event reconcile (TDD): AGENTS 6.3 conflict order, blank related = no amendment."""
from datetime import datetime

from code.reconcile import resolve_event_amendment


def _msg(event_id, sent_at, amount=None, status=None):
    base = {"message_id": "m", "related_event_id": event_id, "sent_at": sent_at}
    if amount is not None:
        base["message_text"] = f"Confirmed amount USD {amount}"
    if status is not None:
        base["message_text"] = (base.get("message_text") or "") + f" status {status}"
    return base


def test_message_amends_related_event_amount():
    event = {"event_id": "event_1", "amount": "5000", "status": "pending",
             "linked_event_id": ""}
    msgs = [_msg("event_1", "2025-06-01T00:00:00Z", amount="4275000")]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "4275000"
    assert "amended" in note


def test_nonblank_message_without_amount_leaves_unchanged():
    event = {"event_id": "event_2", "amount": "3000", "status": "settled"}
    msgs = [_msg("event_2", "2025-06-01T00:00:00Z")]
    # non-blank id but no numeric amendment: no change.
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "3000"
    assert note == "none"


def test_blank_related_event_leaves_unchanged():
    event = {"event_id": "event_2", "amount": "3000", "status": "settled"}
    msgs = [_msg("", "2025-06-01T00:00:00Z", amount="9999")]
    # blank related_event_id means no one-to-one row: context only, must not amend.
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "3000"
    assert note == "none"


def test_whitespace_blank_related_event_leaves_unchanged():
    event = {"event_id": "event_2", "amount": "3000", "status": "settled"}
    msgs = [_msg("   ", "2025-06-01T00:00:00Z", amount="9999")]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "3000"
    assert note == "none"


def test_newer_message_wins():
    event = {"event_id": "event_3", "amount": "1000", "status": "pending"}
    msgs = [
        _msg("event_3", "2025-05-01T00:00:00Z", amount="1000"),
        _msg("event_3", "2025-06-01T00:00:00Z", amount="2500"),
    ]
    amended, note = resolve_event_amendment(event, msgs)
    assert amended["amount"] == "2500"
    assert "newer" in note.lower()


def test_no_messages_returns_original():
    event = {"event_id": "event_4", "amount": "700", "status": "settled"}
    amended, note = resolve_event_amendment(event, [])
    assert amended["amount"] == "700"
    assert note == "none"