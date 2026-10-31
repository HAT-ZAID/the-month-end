"""Deterministic verifier: the kernel owns the decision, the agent only proposes.

``enforce_decision`` is the single place where a provider proposal meets the
kernel row.  It has three guarantees:

1. **Ownership** — the returned ``final_row`` is rebuilt field by field from
   ``kernel_result``; no candidate value can reach a machine field.  The kernel
   dict is never mutated (values are copied, not aliased).
2. **Auditability** — ``verification`` records whether the proposal was valid,
   whether it agreed, every field that differed, and why the kernel row stood.
3. **Claim-checked prose** — the candidate rationale ships only when every
   amount, date, status/method name and spending-change action in it is
   reproduced by the kernel row (:func:`code.claims.verify_claims`).  The check is
   independent of machine-field agreement: a model may be wrong about the plan
   and still contribute a correct, grounded explanation, and it may never
   contribute an unsupported number.

This module performs no LLM reasoning.
"""
from __future__ import annotations

from decimal import Decimal

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
        row[field] = kernel_result[field]
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


def enforce_decision(
    candidate: DecisionCandidate | dict | None,
    kernel_result: dict,
    request_context: dict | None = None,
    *,
    extra_numbers=(),
    extra_dates=(),
) -> dict:
    """Return ``{"final_row", "verification"}`` with the kernel row always winning.

    ``kernel_result`` is the return value of :func:`code.solver.solve_request`
    (or the pipeline's fail-closed row). ``extra_numbers`` / ``extra_dates`` are
    further facts the agent legitimately saw (the request amount, the requested
    date): a rationale may cite them, so the claim check must know them.
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

    final_row = dict(row)
    if rationale_source == "candidate":
        final_row["decision_explanation"] = rationale

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
    return {"final_row": final_row, "verification": payload}


__all__ = ["enforce_decision"]
