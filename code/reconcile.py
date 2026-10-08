"""P1 message/event reconcile (TDD): AGENTS 6.3 conflict order.

Conflict resolution order (explicit per spec):
1. explicit cancellation / settlement / amendment (from a message);
2. then newer records from the same source;
3. then a settled event;
4. then the financially safer interpretation.

A message with a non-empty related_event_id directly describes one event row
and may amend its amount or status. A blank related_event_id means no
one-to-one event row exists, so the message is context only.
"""
from __future__ import annotations

from datetime import datetime

from code.amounts import extract_amounts


def _as_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _num_from_text(text: str | None) -> float | None:
    amounts = extract_amounts(text or "")
    if not amounts:
        return None
    return float(amounts[0]["amount"])


def _amount_str(v: float) -> str:
    if v.is_integer():
        return str(int(v))
    return str(v)


def resolve_event_amendment(
    event: dict, messages: list[dict]
) -> tuple[dict, str]:
    """Apply the strongest amendment a set of related messages confers on event.

    Conflict Order (AGENTS 6.3):
    1. Explicit cancellation, settlement, or amendment.
    2. Then newer records from same source.
    3. Then settled event.
    4. Then financially safer interpretation.
    """
    related = [m for m in messages if (m.get("related_event_id") or "").strip()]
    if not related:
        return dict(event), "none"

    # 1. Check for explicit cancellation/settlement/amendment flags in text
    # (In a full build, this uses a dedicated lexicon; here we use keyword scan)
    cancellation_msgs = [m for m in related if "cancelled" in (m.get("message_text") or "").lower()]
    if cancellation_msgs:
        # Use latest cancellation
        latest_cancel = sorted(cancellation_msgs, key=lambda m: _as_dt(m["sent_at"]), reverse=True)[0]
        amended = dict(event)
        amended["status"] = "cancelled"
        return amended, "cancelled by message"

    # 2. Newer record wins among those providing amendments
    # Filter to messages that actually provide a number
    amendments = []
    for m in related:
        amt = _num_from_text(m.get("message_text") or "")
        if amt is not None:
            amendments.append((_as_dt(m["sent_at"]), amt))
    
    if amendments:
        latest_ts = max(ts for ts, _ in amendments)
        tied = [amt for ts, amt in amendments if ts == latest_ts]
        if len(tied) > 1:
            direction = str(event.get("direction", "") or "").strip().lower()
            event_type = str(event.get("event_type", "") or "").strip().lower()
            is_income = direction == "credit" or event_type in ("income", "credit")
            latest_amt = min(tied) if is_income else max(tied)
            amended = dict(event)
            amended["amount"] = _amount_str(latest_amt)
            return amended, "amended by safer interpretation (tie)"
        amendments.sort(key=lambda x: x[0], reverse=True)
        latest_amt = amendments[0][1]
        amended = dict(event)
        amended["amount"] = _amount_str(latest_amt)
        return amended, "amended by newer message"

    # 3. / 4. If no amendments found, return original event (the "settled" or "safer" baseline)
    return dict(event), "none"