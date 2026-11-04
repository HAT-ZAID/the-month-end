"""Typed decision contract for the agentic layer (Task 5A).

The deterministic kernel (:mod:`code.solver`) owns every machine field that
reaches ``output.csv``.  This module only describes what an optional provider may
*propose*, and it validates that proposal before anything else reads it.

Design notes:

* Standard library only.  The submission must run without new runtime
  dependencies, so frozen dataclasses plus explicit validation are used instead
  of Pydantic.
* A candidate is advisory.  Being schema-valid means "well formed", never
  "approved"; :func:`code.verifier.enforce_decision` always writes the kernel
  values.
* Serialization is deterministic: sorted keys, fixed Decimal/date formatting,
  and NaN/Infinity are rejected instead of silently emitted.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

AFFORDABILITY_STATUSES = (
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
)
PAYMENT_METHODS = (
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
)

#: The seven machine fields of the output contract (request id + six fields).
MACHINE_FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)
FINAL_FIELDS = ("request_id",) + MACHINE_FIELDS

NO_PLAN = "none"
NO_CHANGES = "none"
MAX_SPENDING_CHANGES = 3
CENT = Decimal("0.01")
MONEY_TOLERANCE = CENT

CHANGE_VERBS = ("stop", "reduce_to")

CANDIDATE_FIELDS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "evidence_sufficient",
    "confidence",
    "rationale",
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MISSING = object()


class CandidateValidationError(ValueError):
    """Raised when a provider proposal cannot be turned into a candidate."""

    def __init__(self, errors):
        if isinstance(errors, str):
            messages = [errors]
        else:
            messages = [str(message) for message in errors]
        self.errors = messages
        super().__init__("; ".join(messages) or "invalid candidate")


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------


def decimal_or_none(value) -> Decimal | None:
    """Parse a finite Decimal from a str/int/Decimal/float, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def money_text(value) -> str:
    """Serialize a monetary value at cent precision, deterministically."""
    amount = value if isinstance(value, Decimal) else decimal_or_none(value)
    if amount is None:
        return str(value)
    try:
        quantized = amount.quantize(CENT)
    except (InvalidOperation, ValueError):
        return format(amount, "f")
    return format(quantized, "f")


def iso_date_or_none(value) -> str | None:
    """Return the strict ``YYYY-MM-DD`` form of ``value``, or ``None``.

    Blank strings mean "no date" and normalize to ``None``.  Anything else must
    be a real calendar date written exactly as ``YYYY-MM-DD``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not _ISO_DATE.match(text):
        raise CandidateValidationError([f"date must be YYYY-MM-DD, got {text!r}"])
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise CandidateValidationError([f"invalid calendar date {text!r}: {exc}"]) from exc


def parse_payment_plan(text) -> tuple[tuple[str, Decimal], ...]:
    """Parse ``none`` or ``YYYY-MM-DD:amount[|YYYY-MM-DD:amount]``.

    Returns an ordered tuple of ``(iso_date, Decimal)`` legs.  The empty tuple
    means "no plan".  Raises :class:`CandidateValidationError` when malformed.
    """
    if text is None or not isinstance(text, str):
        raise CandidateValidationError(["payment_plan must be a string or 'none'"])
    raw = text.strip()
    if not raw:
        raise CandidateValidationError(["payment_plan must be 'none' or dated legs"])
    if raw == NO_PLAN:
        return ()
    legs: list[tuple[str, Decimal]] = []
    for part in raw.split("|"):
        leg = part.strip()
        if not leg:
            raise CandidateValidationError(f"payment_plan has an empty leg in {raw!r}")
        pieces = leg.split(":")
        if len(pieces) != 2:
            raise CandidateValidationError(f"payment_plan leg must be date:amount, got {leg!r}")
        leg_date = iso_date_or_none(pieces[0])
        if leg_date is None:
            raise CandidateValidationError(f"payment_plan leg has no date: {leg!r}")
        amount = decimal_or_none(pieces[1])
        if amount is None or amount < 0:
            raise CandidateValidationError(f"payment_plan leg has no non-negative amount: {leg!r}")
        legs.append((leg_date, amount))
    return tuple(legs)


def render_payment_plan(legs) -> str:
    if not legs:
        return NO_PLAN
    return "|".join(f"{leg_date}:{money_text(amount)}" for leg_date, amount in legs)


def parse_spending_changes(text) -> tuple[tuple[str, str, Decimal | None], ...]:
    """Parse ``none`` or up to three ``stop:<id>`` / ``reduce_to:<id>:<amount>``.

    Returns an ordered tuple of ``(verb, event_id, amount_or_None)``.  Contradictory
    actions on one event and duplicate targets are rejected.
    """
    if text is None or not isinstance(text, str):
        raise CandidateValidationError(["spending_changes_needed must be a string or 'none'"])
    raw = text.strip()
    if not raw:
        raise CandidateValidationError(["spending_changes_needed must be 'none' or actions"])
    if raw == NO_CHANGES:
        return ()
    tokens: list[tuple[str, str, Decimal | None]] = []
    seen: dict[str, str] = {}
    for part in raw.split("|"):
        token = part.strip()
        if not token:
            raise CandidateValidationError(
                f"spending_changes_needed has an empty action in {raw!r}"
            )
        pieces = token.split(":")
        verb = pieces[0].strip()
        if verb not in CHANGE_VERBS:
            raise CandidateValidationError(
                f"unknown spending-change action {token!r}; expected stop or reduce_to"
            )
        if verb == "stop":
            if len(pieces) != 2:
                raise CandidateValidationError(f"stop action must be stop:<event_id>, got {token!r}")
            event_id = pieces[1].strip()
            amount = None
        else:
            if len(pieces) != 3:
                raise CandidateValidationError(
                    f"reduce_to action must be reduce_to:<event_id>:<amount>, got {token!r}"
                )
            event_id = pieces[1].strip()
            amount = decimal_or_none(pieces[2])
            if amount is None or amount < 0:
                raise CandidateValidationError(
                    f"reduce_to action has no non-negative amount: {token!r}"
                )
        if not event_id or ":" in event_id or "|" in event_id or event_id != event_id.strip():
            raise CandidateValidationError(f"action has no usable event id: {token!r}")
        if event_id in seen:
            raise CandidateValidationError(
                f"duplicate spending-change target {event_id!r} "
                f"({seen[event_id]} and {verb} may not both apply)"
            )
        seen[event_id] = verb
        tokens.append((verb, event_id, amount))
    if len(tokens) > MAX_SPENDING_CHANGES:
        raise CandidateValidationError(
            f"spending_changes_needed allows at most {MAX_SPENDING_CHANGES} actions, got {len(tokens)}"
        )
    return tuple(tokens)


def render_spending_changes(tokens) -> str:
    if not tokens:
        return NO_CHANGES
    rendered = []
    for verb, event_id, amount in tokens:
        rendered.append(f"stop:{event_id}" if verb == "stop" else f"reduce_to:{event_id}:{money_text(amount)}")
    return "|".join(rendered)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionCandidate:
    """A provider proposal.  Advisory only; never the decision of record."""

    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str | None
    spending_changes_needed: str
    evidence_sufficient: bool
    confidence: float
    rationale: str

    @property
    def plan_legs(self) -> tuple[tuple[str, Decimal], ...]:
        return parse_payment_plan(self.payment_plan)

    @property
    def change_tokens(self) -> tuple[tuple[str, str, Decimal | None], ...]:
        return parse_spending_changes(self.spending_changes_needed)

    def as_dict(self) -> dict:
        """Deterministic, JSON-ready representation (sorted by :func:`stable_json`)."""
        return {
            "request_id": self.request_id,
            "amount_safe_to_pay": money_text(self.amount_safe_to_pay),
            "affordability_status": self.affordability_status,
            "recommended_payment_method": self.recommended_payment_method,
            "payment_plan": render_payment_plan(self.plan_legs),
            "earliest_date_for_full_payment": self.earliest_date_for_full_payment,
            "spending_changes_needed": render_spending_changes(self.change_tokens),
            "evidence_sufficient": bool(self.evidence_sufficient),
            "confidence": round(float(self.confidence), 6),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class VerificationResult:
    """What the verifier concluded about one proposal.

    ``agent_agreement`` is true only when the candidate is valid *and* matches
    the kernel on every machine field.  ``fallback_reason`` is ``None`` exactly
    when the kernel row and the candidate agree.  ``rationale_source`` records
    whether the prose came from the candidate or the kernel.
    """

    agent_agreement: bool
    differences: tuple[dict, ...]
    candidate_valid: bool
    fallback_reason: str | None
    rationale_source: str = "kernel"

    def as_dict(self) -> dict:
        return {
            "agent_agreement": bool(self.agent_agreement),
            "differences": [dict(item) for item in self.differences],
            "candidate_valid": bool(self.candidate_valid),
            "fallback_reason": self.fallback_reason,
            "rationale_source": self.rationale_source,
        }


@dataclass(frozen=True)
class TraceEvent:
    """One lifecycle event inside a per-request trace."""

    kind: str
    payload: dict

    def as_dict(self) -> dict:
        return {"kind": str(self.kind), **_jsonable(dict(self.payload or {}))}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _parse_confidence(value, errors: list[str]) -> float:
    if isinstance(value, bool) or value is None:
        errors.append("confidence must be a number between 0 and 1")
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append("confidence must be a number between 0 and 1")
        return 0.0
    if not math.isfinite(number):
        errors.append("confidence must be finite")
        return 0.0
    if not 0.0 <= number <= 1.0:
        errors.append(f"confidence must be between 0 and 1, got {number}")
        return 0.0
    return number


def _check_evidence_shape(
    amount: Decimal,
    status: str,
    method: str,
    plan: str,
    earliest: str | None,
    changes: str,
    rationale: str,
) -> list[str]:
    problems: list[str] = []
    if amount != 0:
        problems.append(f"amount_safe_to_pay is {money_text(amount)}, expected 0")
    if status != "not_affordable":
        problems.append(f"affordability_status is {status!r}, expected 'not_affordable'")
    if method != "not_recommended":
        problems.append(f"recommended_payment_method is {method!r}, expected 'not_recommended'")
    if plan != NO_PLAN:
        problems.append("payment_plan must be 'none'")
    if earliest is not None:
        problems.append("earliest_date_for_full_payment must be empty")
    if changes != NO_CHANGES:
        problems.append("spending_changes_needed must be 'none'")
    if not rationale.strip():
        problems.append("rationale must name the missing evidence")
    if not problems:
        return []
    return [
        "evidence_sufficient=False requires the not-enough-evidence shape "
        "(safe 0, not_affordable, not_recommended, plan none, no date, no changes, "
        "non-empty rationale): " + "; ".join(problems)
    ]


def parse_candidate(payload) -> tuple[DecisionCandidate | None, list[str]]:
    """Validate a proposal without raising; return ``(candidate, errors)``."""
    if isinstance(payload, DecisionCandidate):
        return payload, []
    if not isinstance(payload, dict):
        return None, [f"candidate must be a mapping, got {type(payload).__name__}"]

    keys = {str(key) for key in payload}
    expected = set(CANDIDATE_FIELDS)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    errors: list[str] = []
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if extra:
        errors.append("unexpected fields: " + ", ".join(extra))
    if missing or extra:
        return None, errors

    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        errors.append("request_id must be a non-empty string")

    amount = decimal_or_none(payload.get("amount_safe_to_pay"))
    if amount is None:
        errors.append("amount_safe_to_pay must be a finite number")
    elif amount < 0:
        errors.append(f"amount_safe_to_pay must not be negative, got {money_text(amount)}")

    status = payload.get("affordability_status")
    if status not in AFFORDABILITY_STATUSES:
        errors.append(f"invalid affordability_status {status!r}")

    method = payload.get("recommended_payment_method")
    if method not in PAYMENT_METHODS:
        errors.append(f"invalid recommended_payment_method {method!r}")

    plan_text = payload.get("payment_plan")
    plan_ok = True
    try:
        parse_payment_plan(plan_text)
    except CandidateValidationError as exc:
        plan_ok = False
        errors.extend(exc.errors)

    earliest = payload.get("earliest_date_for_full_payment")
    earliest_norm: str | None = None
    try:
        earliest_norm = iso_date_or_none(earliest)
    except CandidateValidationError as exc:
        errors.extend(exc.errors)

    changes_text = payload.get("spending_changes_needed")
    changes_ok = True
    try:
        parse_spending_changes(changes_text)
    except CandidateValidationError as exc:
        changes_ok = False
        errors.extend(exc.errors)

    evidence = payload.get("evidence_sufficient")
    if not isinstance(evidence, bool):
        errors.append("evidence_sufficient must be true or false")
        evidence = None

    confidence = _parse_confidence(payload.get("confidence"), errors)

    rationale = payload.get("rationale")
    if not isinstance(rationale, str):
        errors.append("rationale must be a string")
        rationale = ""

    if errors:
        return None, errors

    if evidence is False:
        shape_errors = _check_evidence_shape(
            amount,
            status,
            method,
            str(plan_text).strip(),
            earliest_norm,
            str(changes_text).strip(),
            rationale,
        )
        if shape_errors:
            return None, shape_errors

    assert amount is not None and plan_ok and changes_ok  # narrowed by the error gate
    return (
        DecisionCandidate(
            request_id=request_id.strip(),
            amount_safe_to_pay=amount,
            affordability_status=status,
            recommended_payment_method=method,
            payment_plan=render_payment_plan(parse_payment_plan(plan_text)),
            earliest_date_for_full_payment=earliest_norm,
            spending_changes_needed=render_spending_changes(parse_spending_changes(changes_text)),
            evidence_sufficient=bool(evidence),
            confidence=confidence,
            rationale=rationale,
        ),
        [],
    )


def validate_candidate(payload) -> DecisionCandidate:
    """Return a validated candidate or raise :class:`CandidateValidationError`."""
    candidate, errors = parse_candidate(payload)
    if candidate is None:
        raise CandidateValidationError(errors)
    return candidate


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------


def _jsonable(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal cannot be serialized")
        return money_text(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float cannot be serialized")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    return str(value)


def stable_json(payload) -> str:
    """Canonical JSON: sorted keys, compact separators, no NaN/Infinity."""
    return json.dumps(
        _jsonable(payload),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
