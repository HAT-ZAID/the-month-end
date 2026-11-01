"""Deterministic verifier: the kernel owns the decision, the agent only proposes.

``enforce_decision`` is the single place where a provider proposal meets the
kernel row.  It has three guarantees:

1. **Ownership** — the returned ``final_row`` is rebuilt field by field from
   ``kernel_result``; no candidate value can reach a machine field.  The kernel
   dict is never mutated (values are copied, not aliased).
2. **Auditability** — ``verification`` records whether the proposal was valid,
   whether it agreed, every field that differed, and why the kernel row stood.
3. **Claim-checked prose** - the candidate rationale ships only when every
   amount, date, status/method name and spending-change action in it is
   reproduced by the kernel row (:func:`code.claims.verify_claims`).  The check is
   independent of machine-field agreement: a model may be wrong about the plan
   and still contribute a correct, grounded explanation, and it may never
   contribute an unsupported number.

A caller may additionally ask for the *disagreement* note
(``disagreement_note=True``, used by :mod:`code.agent` only): a valid candidate
that disagreed gets to say where, in text that is marked ``Disagreements:`` and
always carries the computed field list.  The batch path never asks for it, so
``output.csv`` cannot depend on a model's disagreement.

This module performs no LLM reasoning.
"""
from __future__ import annotations

import re
from decimal import ROUND_DOWN, Decimal

from code.amounts import to_decimal

from code.claims import verify_claims
from code.schema import (
    CENT,
    FINAL_FIELDS,
    CandidateValidationError,
    DecisionCandidate,
    VerificationResult,
    decimal_or_none,
    iso_date_or_none,
    money_text,
    parse_candidate,
    parse_payment_plan,
    parse_spending_changes,
)


def _as_text(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return money_text(value)
    return str(value)


def _money_equal(candidate_value, final_value) -> bool:
    candidate_amount = decimal_or_none(candidate_value)
    final_amount = decimal_or_none(final_value)
    if candidate_amount is None or final_amount is None:
        return _as_text(candidate_value) == _as_text(final_value)
    return abs(candidate_amount - final_amount) <= CENT


def _plan_equal(candidate_value, final_value) -> bool:
    try:
        return parse_payment_plan(candidate_value) == parse_payment_plan(final_value)
    except CandidateValidationError:
        return _as_text(candidate_value) == _as_text(final_value)


def _date_equal(candidate_value, final_value) -> bool:
    try:
        return iso_date_or_none(candidate_value) == iso_date_or_none(final_value)
    except CandidateValidationError:
        return _as_text(candidate_value) == _as_text(final_value)


def _changes_equal(candidate_value, final_value) -> bool:
    try:
        return parse_spending_changes(candidate_value) == parse_spending_changes(final_value)
    except CandidateValidationError:
        return _as_text(candidate_value) == _as_text(final_value)


#: Decimal money fields, and the precision the CSV writes them at.
_CENT = Decimal("0.01")
_CENT_FIELDS = ("amount_safe_to_pay",)


def _kernel_row(kernel_result: dict) -> dict:
    """Copy the kernel row into the output contract; never alias or mutate it."""
    if not isinstance(kernel_result, dict):
        raise TypeError(
            "kernel_result must be the dict returned by code.solver.solve_request"
        )
    row: dict = {}
    for field in FINAL_FIELDS:
        if field not in kernel_result:
            raise KeyError(f"kernel_result is missing the {field!r} field")
        value = kernel_result[field]
        # The row keeps the solver's own value and type: every comparison and
        # every shipped string is built from it.  The claim check narrows the
        # *figure* it compares against, in code.claims, so the sentence a model
        # derived from the printed cents is accepted and the full-precision
        # figure is not.  Rounding here would change the row the agent returns.
        row[field] = value
    row["decision_explanation"] = str(kernel_result.get("decision_explanation") or "")
    return row


def _compare(candidate: DecisionCandidate, row: dict) -> tuple[list[dict], bool]:
    """Return ``(differences, request_id_mismatch)``."""
    differences: list[dict] = []

    def note(field, candidate_value, final_value):
        differences.append(
            {
                "field": field,
                "candidate": _as_text(candidate_value),
                "final": _as_text(final_value),
            }
        )

    id_mismatch = str(candidate.request_id) != str(row["request_id"])
    if id_mismatch:
        note("request_id", candidate.request_id, row["request_id"])
    if not _money_equal(candidate.amount_safe_to_pay, row["amount_safe_to_pay"]):
        note("amount_safe_to_pay", candidate.amount_safe_to_pay, row["amount_safe_to_pay"])
    if candidate.affordability_status != str(row["affordability_status"]):
        note(
            "affordability_status",
            candidate.affordability_status,
            row["affordability_status"],
        )
    if candidate.recommended_payment_method != str(row["recommended_payment_method"]):
        note(
            "recommended_payment_method",
            candidate.recommended_payment_method,
            row["recommended_payment_method"],
        )
    if not _plan_equal(candidate.payment_plan, row["payment_plan"]):
        note("payment_plan", candidate.payment_plan, row["payment_plan"])
    if not _date_equal(
        candidate.earliest_date_for_full_payment, row["earliest_date_for_full_payment"]
    ):
        note(
            "earliest_date_for_full_payment",
            candidate.earliest_date_for_full_payment,
            row["earliest_date_for_full_payment"],
        )
    if not _changes_equal(
        candidate.spending_changes_needed, row["spending_changes_needed"]
    ):
        note(
            "spending_changes_needed",
            candidate.spending_changes_needed,
            row["spending_changes_needed"],
        )
    return differences, id_mismatch


DISAGREEMENT_PREFIX = "Disagreements:"

#: The candidate fields a disagreement note may name.  A note that names a field
#: which did not actually differ is not about this run, so its text is dropped.
_CANDIDATE_FIELDS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


#: Emphasis and invisible characters a model may wrap around the marker.
_MARKER_NOISE = str.maketrans({character: None for character in "*_`~"})
#: Every invisible character a model may wrap around or inside the marker:
#: zero-width joiners, bidi controls, the soft hyphen and the BOM.
_MARKER_INVISIBLE = re.compile(
    r"[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)
#: Leading list or quote structure: "> Disagreements:", "1. Disagreements:".
_MARKER_STRUCTURE = re.compile(r"^(?:(?:\d+[.)]|[>#+\-\u2022\u2023\u25aa\u25cf\u25e6\u00b7\u2013\u2014])\s*)+")


def _opens_with_marker(text) -> bool:
    """True when the prose opens with the disagreement marker, however styled."""
    cleaned = _MARKER_INVISIBLE.sub("", str(text or ""))
    cleaned = cleaned.replace("\uff1a", ":").replace("\u2236", ":")
    cleaned = cleaned.translate(_MARKER_NOISE).strip()
    cleaned = _MARKER_STRUCTURE.sub("", cleaned)
    # Any leading punctuation a model decorates the marker with: a bullet we
    # did not think of must not become a way to ship unendorsed prose.
    cleaned = re.sub(r"^[^\w]+", "", cleaned).lstrip()
    return re.match(r"disagreements?\s*:", cleaned, re.IGNORECASE) is not None


def _disagreement_note(rationale: str, differences) -> str:
    """The note a disagreeing candidate writes on the agent path.

    The model narrates the disagreement in its own words, but the record has to
    say *where* it happened: every note carries the computed field list, and a
    note that names a field outside that list is replaced by the list alone.  A
    note that does not open with the marker gets it, so the text is always
    recognisable as an unendorsed disagreement rather than a decision.
    """
    fields = [str(item.get("field", "")) for item in differences if item.get("field")]
    text = str(rationale or "").strip()
    named = {
        field
        for field in _CANDIDATE_FIELDS
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])", text)
    }
    if not text or not named <= set(fields):
        text = ""
    if not text:
        text = f"{DISAGREEMENT_PREFIX} the proposal disagrees with the computed ledger"
    elif not text.lower().startswith(DISAGREEMENT_PREFIX.lower()):
        text = f"{DISAGREEMENT_PREFIX} {text}"
    if fields:
        text = f"{text} (fields: {', '.join(fields)})"
    return text


def _candidate_figures(parsed) -> tuple[tuple, tuple]:
    """The numbers and dates the candidate itself proposes.

    A disagreement note may quote the model's own figures — that is what makes it
    a debugging record — but nothing beyond the kernel row and the candidate.
    """
    numbers: list[str] = []
    dates: list[str] = []
    amount = str(getattr(parsed, "amount_safe_to_pay", "") or "").strip()
    if amount:
        numbers.append(amount)
    earliest = str(getattr(parsed, "earliest_date_for_full_payment", "") or "").strip()
    if earliest:
        dates.append(earliest)
    for part in str(getattr(parsed, "payment_plan", "") or "").split("|"):
        day, _, leg = part.partition(":")
        if day.strip():
            dates.append(day.strip())
        if leg.strip():
            numbers.append(leg.strip())
    return tuple(numbers), tuple(dates)


def enforce_decision(
    candidate: DecisionCandidate | dict | None,
    kernel_result: dict,
    request_context: dict | None = None,
    *,
    extra_numbers=(),
    extra_dates=(),
    disagreement_note: bool = False,
) -> dict:
    """Return ``{"final_row", "verification"}`` with the kernel row always winning.

    ``kernel_result`` is the return value of :func:`code.solver.solve_request`
    (or the pipeline's fail-closed row). ``extra_numbers`` / ``extra_dates`` are
    further facts the agent legitimately saw (the request amount, the requested
    date): a rationale may cite them, so the claim check must know them.
    ``disagreement_note`` is the agent path's opt-in: when the candidate was
    valid but disagreed with the ledger, the row carries the model's note about
    the disagreement, marked as such, instead of the kernel text.  The batch path
    leaves it off, so ``output.csv`` never depends on a model's disagreement.
    """
    row = _kernel_row(kernel_result)

    parsed: DecisionCandidate | None
    errors: list[str]
    if candidate is None:
        parsed, errors = None, []
    else:
        parsed, errors = parse_candidate(candidate)

    candidate_valid = parsed is not None
    differences: list[dict] = []
    if parsed is not None:
        differences, id_mismatch = _compare(parsed, row)
        if id_mismatch:
            candidate_valid = False
            errors = [
                f"candidate request_id {parsed.request_id!r} does not match "
                f"kernel request_id {row['request_id']!r}"
            ]

    expected_id = None
    if isinstance(request_context, dict):
        expected_id = request_context.get("request_id")
        if expected_id is None and isinstance(request_context.get("request"), dict):
            expected_id = request_context["request"].get("request_id")
    context_mismatch = bool(expected_id) and str(expected_id) != str(row["request_id"])

    if candidate is None:
        fallback_reason = "no candidate supplied; the deterministic kernel row stands"
    elif parsed is None:
        detail = "; ".join(errors[:3]) or "candidate rejected"
        fallback_reason = f"candidate rejected ({detail}); the deterministic kernel row stands"
    elif differences:
        fields = ", ".join(item["field"] for item in differences)
        fallback_reason = (
            f"candidate disagreed with the deterministic kernel on {fields}; "
            "the kernel row stands"
        )
    else:
        fallback_reason = None

    if context_mismatch:
        note = (
            f"request context id {expected_id!r} does not match kernel request_id "
            f"{row['request_id']!r}; the kernel row stands"
        )
        fallback_reason = f"{fallback_reason}; {note}" if fallback_reason else note

    agreement = bool(candidate_valid and not differences)
    rationale = str(getattr(parsed, "rationale", "") or "").strip()
    # The model may narrate the row only when it also got the plan right.  A
    # candidate that disagreed on a machine field would otherwise attach prose
    # about a plan we are not shipping; a failed schema check or a context
    # mismatch rules it out for the same reason.  Claim checking then runs on
    # top of agreement, so agreement alone is never enough either.
    claim_report = None
    if rationale and not context_mismatch and parsed is not None:
        claim_report = verify_claims(
            rationale,
            row,
            extra_numbers=extra_numbers or (),
            extra_dates=extra_dates or (),
        )
    rationale_ok = bool(agreement and claim_report and claim_report.get("ok"))
    rationale_source = "candidate" if rationale_ok else "kernel"
    disagreement = ""
    if (
        disagreement_note
        and candidate_valid
        and not agreement
        and not context_mismatch
    ):
        # The model was wrong about the plan but may still say what it thought:
        # the note is the debugging record of the disagreement, not a decision.
        # It may quote its own figures -- that is the point -- but nothing that
        # is neither in the kernel row nor in its own candidate, so an invented
        # amount, date or status still falls back to the marked note.
        grounded = ""
        if rationale and parsed is not None:
            own_numbers, own_dates = _candidate_figures(parsed)
            note_report = verify_claims(
                rationale,
                row,
                extra_numbers=tuple(extra_numbers or ()) + own_numbers,
                extra_dates=tuple(extra_dates or ()) + own_dates,
            )
            if note_report.get("ok"):
                grounded = rationale
        disagreement = _disagreement_note(grounded, differences)
        rationale_source = "disagreement"

    final_row = dict(row)
    if rationale_source == "candidate" and _opens_with_marker(rationale):
        # The marker means "the model disagreed here". A candidate that agrees
        # cannot borrow it: the marker is the only in-band signal that
        # separates an unendorsed note from a decision, and a shipped row that
        # opened with it would make every other note untrustworthy.
        rationale_source = "kernel"
    if rationale_source == "candidate":
        final_row["decision_explanation"] = rationale
    elif rationale_source == "disagreement":
        final_row["decision_explanation"] = disagreement

    verification = VerificationResult(
        agent_agreement=agreement,
        differences=tuple(differences),
        candidate_valid=bool(candidate_valid),
        fallback_reason=fallback_reason,
        rationale_source=rationale_source,
    )
    payload = verification.as_dict()
    if claim_report is not None:
        payload["claim_check"] = claim_report
    if disagreement:
        payload["disagreement_note"] = disagreement
    return {"final_row": final_row, "verification": payload}


__all__ = ["enforce_decision"]
