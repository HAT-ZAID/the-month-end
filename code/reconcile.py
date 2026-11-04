"""P1 message/event reconcile (TDD): AGENTS 6.3 conflict order.

Conflict resolution order (explicit per spec):
1. explicit cancellation / settlement / amendment (from a message);
2. then newer records from the same source;
3. then a settled event;
4. then the financially safer interpretation.

A message with a non-empty ``related_event_id`` directly describes one event row
and may amend its amount, date or status. A blank ``related_event_id`` means no
one-to-one event row exists, so the message is context only. A message may only
amend the event named by its own ``related_event_id``: matching is exact, never
"any message that carries some related id".

Unsafe amendments are never applied silently: currency mismatches, non-cash
(investment valuation) rows, unknown targets and ambiguous duplicate ids are
reported back as warnings.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from code.amounts import extract_amounts

CONFLICT_ORDER = (
    "explicit_cancellation",
    "explicit_settlement",
    "explicit_amendment",
    "newer_same_source",
    "settled_event",
    "safer_interpretation",
)

_CANCEL_CUE = re.compile(r"\bcancell?ed\b|dibatalkan|pembatalan", re.IGNORECASE)
_NEGATION_TOKEN = re.compile(
    r"\b(?:not|never|no|tidak|belum|bukan)\b|(?:n't|n\u2019t)\b", re.IGNORECASE
)
_NEGATION_WINDOW = 24
# A clause boundary ends a negation's reach: "not cancelled, but cancelled today"
# negates only its own mention. "yet" is deliberately absent: in "not yet
# cancelled" it belongs to the negation, not to a new clause.
_CLAUSE_BOUNDARY = re.compile(
    r"[,;:.!?()\n]|\b(?:but|however|although|though|while|whereas|tetapi|namun)\b",
    re.IGNORECASE,
)
_SETTLE_CUE = re.compile(
    r"has reached your account|proceeds have settled|has settled in the cash account"
    r"|payment was received|has been credited|already credited"
    r"|sudah masuk ke rekening|telah masuk|telah diterima|sudah diterima"
    r"|claim is now closed|has been settled",
    re.IGNORECASE,
)
_RETRY_CUE = re.compile(
    r"another debit will be attempted|another debit may be attempted"
    r"|debit (?:lain )?(?:akan|dapat) dicoba",
    re.IGNORECASE,
)
_PENDING_CUE = re.compile(
    r"has not reached your account|not been credited|still pending|still outstanding"
    r"|still being investigated|no reversal has been posted|still processing"
    r"|belum masuk|masih tertunda|masih dalam proses",
    re.IGNORECASE,
)
_AMOUNT_CUE = re.compile(
    r"\bamount\b|\bjumlah\b|sebesar|amended to|corrected to|adjusted to|\bsettled at\b",
    re.IGNORECASE,
)
_DATE_CUE = re.compile(
    r"expected on|rescheduled to|postponed to|new date is|replaces the .{0,30}date"
    r"|received on|billed on|paid on|dijadwalkan ulang|tanggal baru",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_SENTENCE_END = re.compile(r"\.(?=\s|$)|;|\n")

_CANCELLED = {"cancelled", "canceled"}
_NON_CASH_EVENT_TYPES = {"investment_valuation"}


def _as_dt(value) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def _intent_key(intent: dict) -> tuple[datetime, str]:
    return (_as_dt(intent.get("sent_at")), str(intent.get("message_id") or ""))


def _segment_after(text: str, cue: re.Pattern[str], limit: int = 80) -> str:
    match = cue.search(text)
    if not match:
        return ""
    tail = text[match.end() : match.end() + limit]
    stop = _SENTENCE_END.search(tail)
    if stop:
        tail = tail[: stop.start()]
    return tail


def _amount_from_text(text: str) -> tuple[float | None, str]:
    segment = _segment_after(text, _AMOUNT_CUE)
    if not segment:
        return None, ""
    amounts = extract_amounts(segment)
    if not amounts:
        return None, ""
    return float(amounts[0]["amount"]), str(amounts[0]["currency"] or "")


def _date_from_text(text: str) -> str | None:
    segment = _segment_after(text, _DATE_CUE)
    match = _ISO_DATE.search(segment)
    return match.group(1) if match else None


def _amount_str(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _clause_head(text: str, start: int, floor: int) -> str:
    """Text a negation may reach back over for the mention starting at ``start``.

    Lookback stops at ``floor`` (the previous mention) and at the nearest clause
    boundary, so "not cancelled, but cancelled today" negates only the first one.
    """
    head = text[max(floor, start - _NEGATION_WINDOW) : start]
    boundary = None
    for found in _CLAUSE_BOUNDARY.finditer(head):
        boundary = found
    if boundary is not None:
        head = head[boundary.end() :]
    return head


def negation_before(text: str, position: int, floor: int = 0) -> bool:
    """True when the wording immediately before ``position`` negates it.

    Shared with the date-cue reader in ``code/facts.py`` so "will not be paid on"
    and "not yet cancelled" follow one rule.
    """
    return bool(_NEGATION_TOKEN.search(_clause_head(text, position, floor)))


def _has_cancellation(text: str) -> bool:
    """True when at least one cancellation mention in ``text`` is not negated.

    Negation is scoped to the individual mention: "was not cancelled at first,
    but it was cancelled today" is a cancellation, while "not cancelled and
    still pending" is not.
    """
    previous_end = 0
    for match in _CANCEL_CUE.finditer(text):
        if not negation_before(text, match.start(), previous_end):
            return True
        previous_end = match.end()
    return False


def amendment_from_message(message: dict) -> dict | None:
    """Classify one message into an amendment intent for its related event.

    Returns ``None`` when the message has no one-to-one event row (blank
    ``related_event_id``). The intent never decides whether it wins: the conflict
    order is applied by :func:`resolve_event_amendment` / :func:`reconcile_events`.
    """
    related = str(message.get("related_event_id") or "").strip()
    if not related:
        return None
    text = str(message.get("message_text") or "")
    amount, currency = _amount_from_text(text)
    date = _date_from_text(text)

    if _has_cancellation(text):
        action, new_status = "cancelled", "cancelled"
    elif _SETTLE_CUE.search(text):
        action, new_status = "settled", "settled"
    elif _RETRY_CUE.search(text):
        action, new_status = "retry", "pending"
    elif _PENDING_CUE.search(text):
        action, new_status = "pending", None
    elif amount is not None:
        action, new_status = "amount", None
    elif date is not None:
        action, new_status = "date", None
    else:
        action, new_status = "none", None

    return {
        "related_event_id": related,
        "message_id": str(message.get("message_id") or "").strip(),
        "sent_at": str(message.get("sent_at") or "").strip(),
        "action": action,
        "new_status": new_status,
        "new_amount": amount,
        "new_currency": currency,
        "new_date": date,
    }


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _is_credit_event(event: dict) -> bool:
    if _norm(event.get("direction")) == "credit":
        return True
    return _norm(event.get("event_type")) in {"income", "credit"}


def _is_non_cash(event: dict) -> bool:
    return (
        _norm(event.get("direction")) == "non_cash"
        or _norm(event.get("status")) in {"unrealized", "non_cash"}
        or _norm(event.get("event_type")) in _NON_CASH_EVENT_TYPES
    )


def _warning(event_id: str, message_id: str, flag: str, detail: str = "") -> dict:
    return {"event_id": event_id, "message_id": message_id, "flag": flag, "detail": detail}


def _apply_intents(event: dict, intents: list[dict]) -> tuple[dict, str, list[dict]]:
    """Apply the winning amendment(s) of already-targeted intents to one event."""
    target = str(event.get("event_id") or "").strip()
    applicable = [
        intent
        for intent in intents
        if target and str(intent.get("related_event_id") or "").strip() == target
    ]
    if not applicable:
        return dict(event), "none", []

    ordered = sorted(applicable, key=_intent_key)
    amended = dict(event)
    warnings: list[dict] = []
    notes: list[str] = []
    status = _norm(event.get("status"))
    non_cash = _is_non_cash(event)

    # 1. Explicit cancellation outranks every later amendment (AGENTS 6.3).
    cancellations = [intent for intent in ordered if intent.get("action") == "cancelled"]
    if cancellations:
        latest = cancellations[-1]
        amended["status"] = "cancelled"
        notes.append("cancelled by message")
        later = [
            intent
            for intent in ordered
            if _intent_key(intent) > _intent_key(latest)
            and intent.get("action") in {"amount", "settled", "date"}
        ]
        if later:
            warnings.append(
                _warning(
                    target,
                    latest.get("message_id", ""),
                    "LATER_MESSAGE_OVERRIDDEN",
                    "explicit cancellation outranks a later amendment",
                )
            )
        return amended, "; ".join(notes), warnings

    # 2. Explicit settlement, then a scheduled retry of a failed debit.
    settlements = [intent for intent in ordered if intent.get("action") == "settled"]
    if settlements:
        latest = settlements[-1]
        if non_cash:
            warnings.append(
                _warning(
                    target,
                    latest.get("message_id", ""),
                    "NON_CASH_AMENDMENT_IGNORED",
                    "investment valuation rows are not cash",
                )
            )
        else:
            amended["status"] = "settled"
            notes.append("settled by message")
    else:
        retries = [intent for intent in ordered if intent.get("action") == "retry"]
        if retries and status == "failed" and _norm(event.get("direction")) == "debit":
            amended["status"] = "pending"
            notes.append("retry scheduled by message")
        else:
            pendings = [intent for intent in ordered if intent.get("action") == "pending"]
            if pendings and status in {"", "unknown"}:
                amended["status"] = "pending"
                notes.append("pending confirmed by message")

    # 3. Amount amendment: newest message wins; simultaneous conflict goes to
    #    the financially safer figure (larger debit / smaller credit).
    amount_intents = [intent for intent in ordered if intent.get("new_amount") is not None]
    if amount_intents:
        newest_ts = max(_intent_key(intent)[0] for intent in amount_intents)
        tied = [intent for intent in amount_intents if _intent_key(intent)[0] == newest_ts]
        values = {round(float(intent["new_amount"]), 2) for intent in tied}
        chosen = tied[0]
        event_currency = str(event.get("currency") or "").strip().upper()
        intent_currency = str(chosen.get("new_currency") or "").strip().upper()
        if non_cash:
            warnings.append(
                _warning(
                    target,
                    chosen.get("message_id", ""),
                    "NON_CASH_AMENDMENT_IGNORED",
                    "investment valuation rows are not cash",
                )
            )
        elif event_currency and intent_currency and event_currency != intent_currency:
            warnings.append(
                _warning(
                    target,
                    chosen.get("message_id", ""),
                    "AMOUNT_CURRENCY_MISMATCH",
                    f"message states {intent_currency}, event is {event_currency}",
                )
            )
        else:
            if len(values) > 1:
                tied_values = [float(intent["new_amount"]) for intent in tied]
                value = min(tied_values) if _is_credit_event(event) else max(tied_values)
                amended["amount"] = _amount_str(value)
                notes.append("amended by safer interpretation (tie)")
            else:
                amended["amount"] = _amount_str(float(chosen["new_amount"]))
                notes.append("amended by newer message")

    # 4. Date amendment: same newest-wins rule.
    date_intents = [intent for intent in ordered if intent.get("new_date")]
    if date_intents:
        latest_date = date_intents[-1]
        amended["settlement_date"] = latest_date["new_date"]
        amended["event_date"] = latest_date["new_date"]
        notes.append("date amended by message")

    return amended, "; ".join(notes) if notes else "none", warnings


def resolve_event_amendment(event: dict, messages: list[dict]) -> tuple[dict, str]:
    """Apply the strongest amendment a set of related messages confers on event.

    Conflict Order (AGENTS 6.3):
    1. Explicit cancellation, settlement, or amendment.
    2. Then newer records from same source.
    3. Then settled event.
    4. Then financially safer interpretation.
    """
    intents = [amendment_from_message(message) for message in (messages or [])]
    amended, note, _warnings = _apply_intents(
        event, [intent for intent in intents if intent is not None]
    )
    return amended, note


def reconcile_events(events: list[dict], intents: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Apply message amendment intents to the events they name; never to others.

    Returns ``(amended_events, warnings)``. Warnings cover unknown targets,
    ambiguous duplicate ids, currency mismatches and non-cash amendments so a
    caller can report them instead of silently trusting a bad amendment.
    """
    intents = [intent for intent in (intents or []) if intent]
    events = list(events or [])
    warnings: list[dict] = []

    counts = Counter(str(event.get("event_id") or "").strip() for event in events)
    known = {event_id for event_id, count in counts.items() if event_id and count == 1}

    amended_events: list[dict] = []
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            amended_events.append(dict(event))
            continue
        if counts[event_id] > 1:
            warnings.append(
                _warning(event_id, "", "EVENT_ID_AMBIGUOUS", "duplicate event ids; left unchanged")
            )
            amended_events.append(dict(event))
            continue
        amended, _note, event_warnings = _apply_intents(event, intents)
        amended_events.append(amended)
        warnings.extend(event_warnings)

    for intent in intents:
        target = str(intent.get("related_event_id") or "").strip()
        if target and target not in known:
            warnings.append(
                _warning(
                    target,
                    intent.get("message_id", ""),
                    "EVENT_NOT_FOUND",
                    "message names an event that is not in this user's event list",
                )
            )
    return amended_events, warnings
