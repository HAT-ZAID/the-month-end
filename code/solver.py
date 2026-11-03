"""P3 Full Solver: plan candidates + the single ranking authority.

Launder logic:
1. Process Messages/Images into typed facts and apply them to the oracle copy.
2. Ask the oracle for the base (no optional spending change) cashflow.
3. Build every plan shape (full, partial, installments, wait) from that base
   state; when no base plan meets the deadline, repeat for a bounded set of
   permitted spending-change sets, each recomputed from its own cashflow.
4. Rank the combined pool with :func:`code.ranking.rank_options` and return the
   winner.  ``amount_safe_to_pay`` and ``earliest_date_for_full_payment`` always
   stay the base oracle values; only status, method, plan and the emitted change
   list come from the winning candidate.
"""
from __future__ import annotations

import pandas as pd
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from code.amounts import strict_decimal
from code.oracle import solve_safe_amount
from code.options import get_valid_options
from code.changes import apply_changes, build_change_sets, describe_action
from code.ranking import parse_deadline, rank_options
from code.recurrence import detect_recurrence
from code.parser import parse_messages
from code.reconcile import reconcile_events

# Salary kinds that may create a confirmed future income row when no matching
# row exists (the historical raise behavior). A resumed/regular salary and any
# reduction or temporary adjustment only amend an existing row: injecting them
# would duplicate income that the payroll cadence already provides.
_SALARY_INJECTABLE_KINDS = frozenset({"raise", "first", "final"})


def _empty_evidence() -> dict:
    return {
        "flags": [],
        "salary_changes": [],
        "income_facts": [],
        "expense_facts": [],
        "event_updates": [],
        "applied_salary_changes": [],
        "event_amendment_notes": [],
        "warnings": [],
        "notes": [],
    }


def _salary_row_date(event: dict) -> str:
    return str(event.get("settlement_date") or event.get("event_date") or "").strip()


def _is_salary_row(event: dict, user_id) -> bool:
    if str(event.get("user_id") or "") not in ("", str(user_id)):
        return False
    if str(event.get("category") or "").strip().lower() != "salary":
        return False
    return str(event.get("event_type") or "").strip().lower() == "income"


def _amend_salary_row(events, user_id, target_date, amount, currency) -> bool:
    matches = [
        event
        for event in events
        if _is_salary_row(event, user_id) and _salary_row_date(event) == target_date
    ]
    if not matches:
        return False
    # Prefer a countable future row over an already-settled historical one,
    # then order deterministically so a repeated run selects the same row.
    matches.sort(
        key=lambda event: (
            0 if str(event.get("status") or "").strip().lower() in {"scheduled", "pending"} else 1,
            str(event.get("event_id") or ""),
        )
    )
    event = matches[0]
    event["amount"] = str(amount)
    if str(currency or "").strip():
        event["currency"] = str(currency)
    # Countable scheduled income; never resurrect a failed/cancelled row.
    if str(event.get("status") or "").strip().lower() in ("", "nan", "none"):
        event["status"] = "scheduled"
    return True


def _inject_salary_row(events, user_id, kind, target_date, amount, currency) -> None:
    events.append({
        "event_id": f"salary_{kind}_{target_date}",
        "user_id": user_id,
        "event_type": "income",
        "description": "Salary update via payroll message (injected)",
        "category": "salary",
        "direction": "credit",
        "amount": str(amount),
        "currency": str(currency or ""),
        "event_date": target_date,
        "settlement_date": target_date,
        "status": "scheduled",
        "linked_event_id": "",
        "flexibility": "fixed",
        "minimum_allowed_amount": "",
    })


def _next_scheduled_salary_row(events, user_id, not_before: str):
    candidates = [
        event
        for event in events
        if _is_salary_row(event, user_id)
        and str(event.get("status") or "").strip().lower() in {"scheduled", "pending"}
        and _salary_row_date(event) >= not_before
    ]
    if not candidates:
        return None
    return sorted(candidates, key=_salary_row_date)[0]


def _salary_change_warning(change: dict, detail: str) -> dict:
    return {
        "message_id": str(change.get("source_message_id") or ""),
        "flag": "SALARY_CHANGE_NOT_APPLIED",
        "kind": str(change.get("kind") or ""),
        "date": str(change.get("effective_date") or change.get("settlement_date") or ""),
        "detail": detail,
    }


def _apply_salary_changes(events, salary_changes, user_id, profile, req_date) -> tuple[list[dict], list[dict]]:
    """Apply typed salary facts to the oracle copy; return (applied, warnings).

    A dated change amends the salary row on that date, or injects a confirmed
    income row for kinds that legitimately create income. An undated "next
    payroll" change only amends the next scheduled salary row: it never invents
    a payment date. A change that matches no row and cannot be injected is
    reported in ``warnings`` so it is never silently dropped. Changes dated
    beyond the 90-day horizon are skipped by design (they cannot affect this
    request) and are not reported as data loss.
    """
    applied: list[dict] = []
    warnings: list[dict] = []
    request_day = str(req_date or "").strip()
    try:
        window_end = date.fromisoformat(request_day) + timedelta(days=90)
    except Exception:
        window_end = None

    for change in salary_changes or []:
        kind = str(change.get("kind") or "")
        amount = change.get("amount")
        if amount is None or kind == "ended":
            continue
        currency = change.get("currency") or profile.get("home_currency", "USD")
        target = change.get("effective_date") or change.get("settlement_date")

        if target:
            try:
                target_date = date.fromisoformat(str(target).strip())
            except Exception:
                warnings.append(
                    _salary_change_warning(change, "unparsable salary change date")
                )
                continue
            if window_end is not None and target_date > window_end:
                continue
            target_s = target_date.isoformat()
            if _amend_salary_row(events, user_id, target_s, amount, currency):
                applied.append({"kind": kind, "date": target_s, "amount": str(amount),
                                "source_message_id": change.get("source_message_id", ""),
                                "mode": "amended"})
            elif kind in _SALARY_INJECTABLE_KINDS:
                _inject_salary_row(events, user_id, kind, target_s, amount, currency)
                applied.append({"kind": kind, "date": target_s, "amount": str(amount),
                                "source_message_id": change.get("source_message_id", ""),
                                "mode": "injected"})
            else:
                warnings.append(
                    _salary_change_warning(
                        change, f"no salary row on {target_s} to amend; not injected by kind"
                    )
                )
            continue

        # Undated reduction/temporary/confirmation: amend the next payroll row
        # only. No row means no application (never invent a salary payment).
        row = _next_scheduled_salary_row(events, user_id, request_day) if request_day else None
        if row is None:
            warnings.append(
                _salary_change_warning(change, "no upcoming salary row to amend; no date stated")
            )
            continue
        row_date = _salary_row_date(row)
        row["amount"] = str(amount)
        if str(currency or "").strip():
            row["currency"] = str(currency)
        applied.append({"kind": kind, "date": row_date, "amount": str(amount),
                        "source_message_id": change.get("source_message_id", ""),
                        "mode": "amended"})
    return applied, warnings


def _evidence_notes(facts: dict) -> list[str]:
    """Limitations that still apply after the forecast layer (Task 3).

    Income-end flags and expense facts are now consumed by :mod:`code.forecast`,
    so their old "not applied" notes are gone; an ambiguous fact is reported as
    a forecast warning instead of a silent note.
    """
    notes: list[str] = []
    if any(fact.get("status") == "confirmed" for fact in facts.get("income_facts", [])):
        notes.append("confirmed_income_not_injected")
    if any(change.get("amount") is None for change in facts.get("salary_changes", [])):
        notes.append("unquantified_salary_change_not_applied")
    return notes


def _forecast_evidence(evidence: dict) -> tuple[set[str], list[dict]]:
    """Typed evidence handed to the oracle forecast; never raw message text."""
    flags = {str(flag) for flag in (evidence.get("flags") or []) if str(flag).strip()}
    facts = [fact for fact in (evidence.get("expense_facts") or []) if isinstance(fact, dict)]
    return flags, facts


def _forecast_invalid_detail(result: dict) -> str | None:
    """Name the first non-finite value in a kernel result, or return ``None``.

    A projected amount or a daily balance that is not a finite
    :class:`~decimal.Decimal` is an unknown projection, not a zero.  Ranking a
    plan on it would emit a figure the ledger cannot support, so the solver
    treats it as a failed forecast.
    """
    for entry in result.get("forecast") or []:
        amount = entry.get("amount") if isinstance(entry, dict) else getattr(entry, "amount", None)
        if strict_decimal(amount) is None:
            return f"a projected forecast amount is not finite ({amount!r})"
    for balance in result.get("ledger") or []:
        if strict_decimal(balance) is None:
            return f"a daily balance is not finite ({balance!r})"
    return None


def _forecast_fail_closed(rid, evidence: dict, detail: str) -> dict:
    """Fail-closed envelope for a forecast that cannot be valued.

    Mirrors the oracle-ledger envelope: nothing safe, nothing to rank, and a
    named reason (``FORECAST_INVALID``) so the failure is visible instead of
    being reported as an ordinary "not affordable" verdict.
    """
    evidence = dict(evidence or {})
    evidence["warnings"] = list(evidence.get("warnings") or []) + [{
        "flag": "FORECAST_INVALID",
        "detail": detail,
    }]
    return {
        "request_id": rid,
        "amount_safe_to_pay": "0",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": None,
        "spending_changes_needed": "none",
        "decision_explanation": (
            "No payment is safe: the forecast could not be valued "
            f"(FORECAST_INVALID: {detail}). A plan ranked on a projection that "
            "is not a finite number would report a figure the ledger cannot "
            "support, so no payment is recommended."
        ),
        "evidence": evidence,
    }


def _parse_plan_legs(payment_plan: str) -> list[tuple[date, Decimal]] | None:
    legs: list[tuple[date, Decimal]] = []
    try:
        parts = str(payment_plan or "").split("|")
    except Exception:
        return None
    for part in parts:
        part = part.strip()
        if not part or ":" not in part:
            return None
        d_s, a_s = part.split(":", 1)
        try:
            d = date.fromisoformat(d_s.strip())
            a = Decimal(str(a_s).strip())
            if not a.is_finite() or a < 0:
                return None
            legs.append((d, a))
        except Exception:
            return None
    if not legs:
        return None
    return sorted(legs)


def _installment_safe(ledger, req_date_str: str, payment_plan: str, profile: dict) -> bool:
    """Strict leg simulation: rebuild post-leg balances (ledger - cumulative legs).

    Returns True when every daily post-leg balance stays >= minimum_balance_to_keep.
    Fail-closed: empty/missing/short/unparsable ledger, unparsable req_date/floor,
    NaN balances, or legs beyond the ledger horizon all return False.
    """
    try:
        req_d = date.fromisoformat(str(req_date_str).strip())
    except Exception:
        return False
    try:
        min_bal = Decimal(str(profile.get("minimum_balance_to_keep", "0")))
        if min_bal.is_nan():
            return False
    except Exception:
        return False
    if ledger is None:
        return False
    try:
        bal_list = [Decimal(str(b)) for b in ledger]
    except Exception:
        return False
    # Oracle ledger contract is 91 daily balances; anything else is fail-closed.
    if len(bal_list) != 91:
        return False
    for b in bal_list:
        try:
            if not b.is_finite():
                return False
        except Exception:
            return False
    legs = _parse_plan_legs(payment_plan)
    if not legs:
        return False
    # Conservative: legs beyond the ledger horizon cannot be verified -> unsafe.
    try:
        horizon = req_d + timedelta(days=len(bal_list) - 1)
        for d, _ in legs:
            if d < req_d:
                return False
            if d > horizon:
                return False
    except Exception:
        return False
    n = len(bal_list)
    for i in range(n):
        day = req_d + timedelta(days=i)
        cum = sum((a for d, a in legs if d <= day), Decimal("0"))
        if not cum:
            continue
        try:
            if bal_list[i] - cum < min_bal:
                return False
        except Exception:
            return False
    return True


def meets_deadline(comp_date, desired_completion_date) -> bool:
    """Deadline check with NaN/'' normalization (missing/NaN = no constraint)."""
    desired = parse_deadline(desired_completion_date)
    if desired is None:
        return True
    comp = parse_deadline(comp_date)
    if comp is None:
        return True
    return comp <= desired


def _is_iso_date(value) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _build_candidates(state: dict, base: dict, ctx: dict) -> list[dict]:
    """Build every plan shape for one cashflow state (base or changed).

    ``state`` carries the safe amount, earliest full-payment date and ledger
    recomputed for this state; ``base`` carries the no-optional-change oracle
    values.  The output contract pins ``amount_safe_to_pay`` and
    ``earliest_date_for_full_payment`` to the *base* values, so a partial plan
    is always exactly those two legs (leg 1 on the request date, leg 2 on the
    base earliest date); only status, method, plan and changes come from the
    winning candidate.  Deadline feasibility is *not* decided here: ranking is
    the single authority for it.
    """
    request_date = ctx["request_date"]
    requested = ctx["requested_amount"]
    will = ctx["will"]
    profile = ctx["profile"]
    suffix = state.get("suffix", "")
    changes = list(state.get("changes") or [])
    change_count = len(changes)
    changes_text = "|".join(changes) if changes else "none"
    safe = state["safe"]
    earliest = state.get("earliest")
    ledger = state.get("ledger") or []
    base_safe = base["safe"]
    base_earliest = base.get("earliest")
    candidates: list[dict] = []

    # Option A: full payment on the request date.
    if safe >= requested and "full_payment" in will:
        candidates.append({
            "option_id": f"opt_full_now{suffix}", "id": f"opt_full_now{suffix}",
            "payment_method": "full_payment",
            "amount_safe_to_pay": requested,
            "affordability_status": "affordable_with_plan" if changes else "affordable_now",
            "payment_plan": f"{request_date}:{requested}",
            "earliest_date": earliest,
            "spending_changes": changes_text,
            "cost": requested, "total_cost": requested,
            "completion_date": request_date,
            "changes": change_count, "legs": 1, "start_date": request_date,
            "num_payments": 1,
        })

    # Option B: partial payment — the request allows it, the user accepts it,
    # and the remainder lands on the (base) earliest full-payment date.
    if (Decimal("0") < base_safe < requested and ctx["allows_partial"]
            and "partial_payment" in will and _is_iso_date(base_earliest)):
        remainder = requested - base_safe
        candidates.append({
            "option_id": f"opt_partial{suffix}", "id": f"opt_partial{suffix}",
            "payment_method": "partial_payment",
            "amount_safe_to_pay": base_safe,
            "affordability_status": "affordable_with_plan",
            "payment_plan": f"{request_date}:{base_safe}|{base_earliest}:{remainder}",
            "earliest_date": base_earliest,
            "spending_changes": changes_text,
            "cost": requested, "total_cost": requested,
            "completion_date": base_earliest,
            "changes": change_count, "legs": 2, "start_date": request_date,
            "num_payments": 2,
        })

    # Option C: installments — exactly a supplied option, certified per leg
    # against *this* state's ledger.
    for opt in ctx["installment_options"]:
        if opt.get("payment_method") != "installments" or "installments" not in will:
            continue
        if ctx["strict_installments"] and not _installment_safe(
            ledger, request_date, opt.get("payment_plan", ""), profile
        ):
            continue
        completion = opt.get("completion_date", opt["first_payment_date"])
        candidates.append({
            "option_id": f"{opt['option_id']}{suffix}", "id": f"{opt['option_id']}{suffix}",
            "payment_method": "installments",
            "amount_safe_to_pay": base_safe if base_safe < requested else requested,
            "affordability_status": "affordable_with_plan",
            "payment_plan": opt["payment_plan"],
            "earliest_date": earliest,
            "spending_changes": changes_text,
            "cost": opt.get("total_payable", opt["amount"]),
            "total_cost": opt.get("total_payable", opt["amount"]),
            "completion_date": completion,
            "changes": change_count, "legs": opt["number_of_payments"],
            "start_date": opt["first_payment_date"],
            "num_payments": opt["number_of_payments"],
        })

    # Option D: wait — one full payment on this state's earliest safe date.
    if (_is_iso_date(earliest)
            and date.fromisoformat(str(earliest)) > date.fromisoformat(str(request_date))
            and "full_payment" in will):
        candidates.append({
            "option_id": f"opt_wait{suffix}", "id": f"opt_wait{suffix}",
            "payment_method": "wait",
            "amount_safe_to_pay": safe,
            "affordability_status": "affordable_later",
            "payment_plan": f"{earliest}:{requested}",
            "earliest_date": earliest,
            "spending_changes": changes_text,
            "cost": requested, "total_cost": requested,
            "completion_date": earliest,
            "changes": change_count, "legs": 1, "start_date": earliest,
            "num_payments": 1,
        })

    return candidates


#: A deliberately absurd probe amount.  The oracle caps ``safe_amount`` at the
#: requested amount, so asking for more than anyone could afford returns the
#: largest single payment that is safe on the date: the number a person means by
#: "how much can I spend right now without dropping below my floor".
_POSITION_PROBE = "1000000000000"

#: The profile facts a position cannot be valued without, paired with the words
#: the refusal uses.  Kept in step with ``code.service.MISSING_FACTS``: the
#: kernel and the service name the same two contract fields, so an unanswered
#: fact reads identically whether it reaches a decision or a position (CR-14).
POSITION_REQUIRED_FACTS = (
    ("current_available_balance", "balance"),
    ("minimum_balance_to_keep", "minimum balance to keep"),
)


def _unknown_position(user_id, as_of_str, profile, unknown) -> dict:
    """The position envelope when a fact the kernel cannot invent is blank.

    An unanswered balance is not zero.  Every derived monetary field is left
    empty, the reasons are named in ``unknown`` and ``fail_closed_reason``, and
    no ledger is built -- so a view (or a model reading the tool result) can say
    what is missing instead of rendering a figure computed against a balance
    nobody gave (CR-14).  The shape is the same dict :func:`position` returns,
    so callers need no second contract.
    """
    reasons = [f"{label} is not set" for label in unknown]
    return {
        "user_id": user_id,
        "as_of": as_of_str,
        "currency": str((profile or {}).get("home_currency", "") or ""),
        "available_balance": "",
        "minimum_balance": "",
        "headroom": "",
        "committed": "",
        "committed_until": None,
        "max_safe_payment": "",
        "window_days": 91,
        "fail_closed": True,
        "fail_closed_reason": "MISSING_FACTS: " + "; ".join(reasons),
        "outflows": [],
        "unknown": reasons,
        "assumptions": [
            "no position is computed while a fact the kernel cannot invent is "
            f"missing ({'; '.join(reasons)})",
        ],
    }


def position(user_id, as_of, profile, events, fx_rows, messages_df=None):
    """The kernel's view of a person's money on ``as_of``.

    This is the only place a position figure is computed.  A view layer renders
    what this returns and sums nothing: ``committed`` and ``max_safe_payment``
    both come out of :func:`code.oracle.solve_safe_amount`, which is the same
    function the decision path calls, so the home screen cannot disagree with a
    purchase decision made on the same account.

    ``committed`` is the scheduled outflow between ``as_of`` and the next
    confirmed income (or the whole 91-day window when no income is scheduled),
    taken from the oracle's own ``processed_events``.  ``max_safe_payment`` is
    the oracle's capped safe amount for a probe request, i.e. the largest
    one-off payment that keeps the balance at or above the floor after every
    projected commitment.

    Evidence messages are not applied here: a position is a statement about
    recorded facts.  ``messages_df`` is accepted so a caller that has already
    reconciled evidence can pass the amended rows in ``events``.

    A blank ``current_available_balance`` or ``minimum_balance_to_keep`` is
    *unknown*, not zero: the answer is the same dict shape with empty monetary
    fields, ``fail_closed=True`` and an ``unknown`` list naming each fact
    (CR-14).  A figure computed against an invented zero would be a number the
    ledger cannot support.
    """
    import copy as _copy

    as_of_str = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
    if not _is_iso_date(as_of_str):
        raise ValueError(f"position() needs an ISO date, got {as_of!r}")
    req_date = date.fromisoformat(as_of_str)

    profile = profile or {}
    missing = [
        label for field, label in POSITION_REQUIRED_FACTS
        if not str(profile.get(field) or "").strip()
    ]
    if missing:
        return _unknown_position(user_id, as_of_str, profile, missing)

    events_for_oracle = _copy.deepcopy(events or [])
    try:
        recurrence_flags = detect_recurrence(events_for_oracle)
    except Exception:
        recurrence_flags = set()

    result = solve_safe_amount(
        user_id, as_of_str, _POSITION_PROBE, profile, events_for_oracle, fx_rows or [],
        recurrence_flags,
    )

    def _decimal(value, default="0"):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return Decimal(default)

    available = _decimal(profile.get("current_available_balance", "0"))
    floor = _decimal(profile.get("minimum_balance_to_keep", "0"))
    headroom = available - floor

    fail_closed = bool(result.get("fail_closed"))
    processed = list(result.get("processed_events") or [])

    outflows = []
    for item in processed:
        delta = item.get("delta")
        try:
            amount = Decimal(str(delta))
        except (InvalidOperation, TypeError):
            continue
        outflows.append({
            "date": str(item.get("date") or ""),
            "amount": str(amount),
            "category": str(item.get("category") or ""),
        })

    incomes = [
        str(item.get("date")) for item in processed
        if _decimal(item.get("delta", "0")) > 0
    ]
    next_income = sorted(incomes)[0] if incomes else None

    committed = Decimal("0")
    for item in processed:
        try:
            amount = Decimal(str(item.get("delta")))
        except (InvalidOperation, TypeError):
            continue
        if amount >= 0:
            continue
        when = str(item.get("date") or "")
        if when and next_income is not None and when > next_income:
            continue
        committed += -amount

    if fail_closed:
        max_safe = Decimal("0")
    else:
        max_safe = _decimal(result.get("safe_amount", "0"))

    assumptions = [
        "conservative: scheduled and pending outflows are reserved, pending income is not counted",
        "before optional spending changes: no category is stopped or reduced",
        f"over the {91}-day projection window from {as_of_str}",
        "the floor is the minimum balance you asked to keep",
    ]

    return {
        "user_id": user_id,
        "as_of": as_of_str,
        "currency": str(profile.get("home_currency", "") or ""),
        "available_balance": str(available),
        "minimum_balance": str(floor),
        "headroom": str(headroom),
        "committed": str(committed),
        "committed_until": next_income,
        "max_safe_payment": str(max_safe),
        "window_days": 91,
        "fail_closed": fail_closed,
        "fail_closed_reason": result.get("fail_closed_reason"),
        "unknown": [],
        "outflows": outflows,
        "assumptions": assumptions,
    }


def solve_request(rid, user_id, req_date, req_amt, profile, events, fx_rows, options_df, messages_df=None, desired_completion_date=None, allows_partial_payment=None, installment_safety="strict"):
    import copy as _copy
    req_amt_dec = Decimal(str(req_amt))
    # Never mutate caller data: work on a modified copy for all oracle calls.
    try:
        events_for_oracle = _copy.deepcopy(events) if events is not None else []
    except Exception:
        events_for_oracle = [dict(e) for e in (events or [])]

    # 1. Process Evidence (Messages) into typed facts, then apply them to the
    #    oracle copy (oracle signature frozen). Untrusted content never decides
    #    a field: only typed facts reach the ledger.
    evidence = _empty_evidence()
    if messages_df is not None:
        try:
            facts = parse_messages(user_id, messages_df)
        except Exception:
            facts = None
        if facts is not None:
            # Event amendments: exact event_id match only, AGENTS 6.3 conflict order.
            events_for_oracle, amendment_warnings = reconcile_events(
                events_for_oracle, facts.get("event_updates", [])
            )
            # Flag stability: snapshot BEFORE salary injection so a huge raise
            # cannot drop the salary flag via CV (injection kept for amounts).
            try:
                _pre_injection = _copy.deepcopy(events_for_oracle)
            except Exception:
                _pre_injection = [dict(e) for e in events_for_oracle]
            applied_changes, salary_warnings = _apply_salary_changes(
                events_for_oracle, facts.get("salary_changes", []), user_id, profile, req_date
            )
            evidence = {
                "flags": sorted(facts.get("flags", set())),
                "salary_changes": facts.get("salary_changes", []),
                "income_facts": facts.get("income_facts", []),
                "expense_facts": facts.get("expense_facts", []),
                "event_updates": facts.get("event_updates", []),
                "applied_salary_changes": applied_changes,
                "event_amendment_notes": amendment_warnings,
                "warnings": list(facts.get("warnings", []))
                + list(amendment_warnings)
                + list(salary_warnings),
                "notes": _evidence_notes(facts),
            }
        else:
            try:
                _pre_injection = _copy.deepcopy(events_for_oracle)
            except Exception:
                _pre_injection = [dict(e) for e in events_for_oracle]

        recurrence_flags = detect_recurrence(_pre_injection)
    else:
        # No messages: flags from the (uninjected) oracle copy.
        recurrence_flags = detect_recurrence(events_for_oracle)

    # Parse eligibility per spec Â§Choosing Between Safe Plans
    will_raw = profile.get("payment_methods_user_will_consider", "") or ""
    will = set(s.strip() for s in str(will_raw).split("|") if s.strip())
    if desired_completion_date is None:
        # caller may embed in options_df path; default to None (no deadline filter)
        pass
    allows_partial = str(allows_partial_payment).strip().lower() == "true" if allows_partial_payment is not None else False

    # 2. Get Oracle results for the base state (modified copy carries salary injection).
    #    Typed evidence reaches the forecast layer here; the oracle signature is frozen.
    evidence_flags, expense_facts = _forecast_evidence(evidence)
    try:
        res_now = solve_safe_amount(
            user_id, req_date, str(req_amt_dec), profile, events_for_oracle, fx_rows, recurrence_flags,
            evidence_flags=evidence_flags, expense_facts=expense_facts,
        )
        invalid_detail = _forecast_invalid_detail(res_now)
    except (ArithmeticError, ValueError, TypeError) as exc:
        # A forecast that raises (an unparsable amount, a type the ledger cannot
        # add, NaN arithmetic) leaves the projection unknown.  Fail closed with a
        # named reason instead of letting a half-built ledger be ranked.
        return _forecast_fail_closed(rid, evidence, f"{type(exc).__name__}: {exc}")
    if invalid_detail is not None:
        return _forecast_fail_closed(rid, evidence, invalid_detail)
    if res_now.get("fail_closed"):
        # An ambiguous ledger cannot certify any amount: fail closed explicitly
        # instead of ranking options derived from a synthetic ledger.
        reason = str(res_now.get("fail_closed_reason") or "UNKNOWN")
        evidence["warnings"] = list(evidence.get("warnings", [])) + [{
            "flag": "LEDGER_FAIL_CLOSED",
            "detail": f"the ledger cannot be valued or classified with confidence ({reason})",
        }]
        return {
            "request_id": rid,
            "amount_safe_to_pay": "0",
            "affordability_status": "not_affordable",
            "recommended_payment_method": "not_recommended",
            "payment_plan": "none",
            "earliest_date_for_full_payment": None,
            "spending_changes_needed": "none",
            "decision_explanation": (
                f"No payment is safe: the ledger had to fail closed ({reason}) because at "
                "least one cash row cannot be valued or classified with confidence. "
                "Resolve the ambiguous row before committing to this request."
            ),
            "evidence": evidence,
        }
    safe_amt = res_now["safe_amount"]
    earliest = res_now["earliest_date"]

    # 3. Build every candidate plan: the no-change state first, then a bounded
    #    set of permitted change sets, each recomputed against its own cashflow.
    base_state = {
        "safe": safe_amt,
        "earliest": earliest,
        "ledger": res_now.get("ledger") or [],
        "changes": [],
        "suffix": "",
    }
    will_set = set(will)
    strict_inst = installment_safety != "lenient"
    ctx = {
        "request_date": req_date,
        "requested_amount": req_amt_dec,
        "profile": profile,
        "will": will_set,
        "allows_partial": bool(allows_partial),
        "installment_options": get_valid_options(rid, profile, options_df),
        "strict_installments": strict_inst,
    }
    candidates = _build_candidates(base_state, base_state, ctx)
    ranked = rank_options(candidates, desired_completion_date)

    if not ranked:
        # Spending changes are first-class candidates: they are evaluated
        # whenever no no-change plan meets the deadline, not merely when the
        # option pool happens to be empty.  Ranking rule 2 (fewer changes) makes
        # a feasible no-change plan unbeatable, so skipping the search then
        # cannot change the winner.  Sizes are tried 1, then 2, then 3, and a
        # size is only reached when the smaller sizes produced no candidate.
        try:
            change_sets = build_change_sets(
                user_id, profile, events_for_oracle, recurrence_flags
            )
        except Exception:
            change_sets = []
        counter = 0
        for size in range(1, 4):
            for change_set in (combo for combo in change_sets if len(combo) == size):
                counter += 1
                try:
                    modified, suppressed, overrides = apply_changes(events_for_oracle, change_set)
                    changed = solve_safe_amount(
                        user_id, req_date, str(req_amt_dec), profile, modified, fx_rows,
                        recurrence_flags,
                        suppressed_categories=suppressed,
                        category_overrides=overrides,
                        evidence_flags=evidence_flags,
                        expense_facts=expense_facts,
                    )
                except Exception:
                    continue
                # A fail-closed ledger certifies nothing: never build options from it.
                if changed.get("fail_closed"):
                    continue
                # Neither does a ledger or forecast the kernel cannot value: a
                # non-finite plan state must never reach ranking.
                if _forecast_invalid_detail(changed) is not None:
                    continue
                # An identical real ledger means the change had no cash effect, so
                # every plan shape it could offer already exists with fewer changes.
                base_ledger = base_state["ledger"]
                if len(base_ledger) == 91 and changed.get("ledger") == base_ledger:
                    continue
                state = {
                    "safe": changed["safe_amount"],
                    "earliest": changed["earliest_date"],
                    "ledger": changed.get("ledger") or [],
                    "changes": [describe_action(action) for action in change_set],
                    "suffix": f"_chg{counter}",
                }
                candidates.extend(_build_candidates(state, base_state, ctx))
            ranked = rank_options(candidates, desired_completion_date)
            if ranked:
                break

    if not ranked:
        deadline = parse_deadline(desired_completion_date)
        reason = "No safe payment plan keeps the balance above the minimum"
        if deadline is not None:
            reason += f" and completes by {deadline.isoformat()}"
        return {
            "request_id": rid,
            "amount_safe_to_pay": str(safe_amt),
            "affordability_status": "not_affordable",
            "recommended_payment_method": "not_recommended",
            "payment_plan": "none",
            "earliest_date_for_full_payment": None,
            "spending_changes_needed": "none",
            "decision_explanation": (
                f"{reason}. The request is not affordable without inventing income or "
                "breaching a protected commitment, so no payment is recommended."
            ),
            "evidence": evidence,
        }

    best = ranked[0]
    # `wait` means "a full payment becomes safe on this date". When the winning
    # wait plan only works after a permitted spending change, the base projection
    # has no safe date at all, and reporting an empty date next to "wait" is a
    # contradiction. The base amount stays untouched (it is the pre-change value
    # by contract); only this date follows the recommended plan.
    reported_earliest = earliest
    if best["payment_method"] == "wait" and _is_iso_date(best.get("earliest_date")):
        reported_earliest = best["earliest_date"]
    explanation = f"Recommended {best['payment_method']} ({best['affordability_status']})."
    if best.get("spending_changes") not in (None, "", "none"):
        explanation = (
            f"Recommended {best['payment_method']} ({best['affordability_status']}) after "
            f"permitted spending changes ({best['spending_changes']})."
        )

    return {
        "request_id": rid,
        # Output contract: these two fields are always the base (no optional
        # changes) oracle values, whatever plan wins.
        "amount_safe_to_pay": str(safe_amt),
        "affordability_status": best["affordability_status"],
        "recommended_payment_method": best["payment_method"],
        "payment_plan": best["payment_plan"],
        "earliest_date_for_full_payment": reported_earliest,
        "spending_changes_needed": best["spending_changes"],
        "decision_explanation": explanation,
        "evidence": evidence,
    }