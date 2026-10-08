"""P3 Full Solver: Integrates Oracle, Ranking, and Payment Options to make the final decision.

Launder Logic:
1. Process Messages/Images to update profiles and events.
2. Identify all viable payment options (Full, Partial, Installments, Wait).
3. For each option, use the Oracle to check safety.
4. Rank the safe options using Ranking logic (code/ranking.py).
5. Pick the best.
"""
from __future__ import annotations

import pandas as pd
from datetime import date, timedelta
from decimal import Decimal
from code.oracle import solve_safe_amount
from code.options import get_valid_options
from code.changes import search_changes
from code.recurrence import detect_recurrence
from code.parser import parse_messages


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


def _parse_deadline_date(val) -> date | None:
    try:
        if val is None:
            return None
        if isinstance(val, float) and val != val:  # NaN
            return None
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null", "nat", "n/a", "na"):
            return None
        return date.fromisoformat(s)
    except Exception:
        return None


def meets_deadline(comp_date, desired_completion_date) -> bool:
    """Deadline check with NaN/'' normalization (missing/NaN = no constraint)."""
    desired = _parse_deadline_date(desired_completion_date)
    if desired is None:
        return True
    comp = _parse_deadline_date(comp_date)
    if comp is None:
        return True
    return comp <= desired


def solve_request(rid, user_id, req_date, req_amt, profile, events, fx_rows, options_df, messages_df=None, desired_completion_date=None, allows_partial_payment=None, installment_safety="strict"):
    import copy as _copy
    req_amt_dec = Decimal(str(req_amt))
    # Never mutate caller data: work on a modified copy for all oracle calls.
    try:
        events_for_oracle = _copy.deepcopy(events) if events is not None else []
    except Exception:
        events_for_oracle = [dict(e) for e in (events or [])]

    # 1. Process Evidence (Messages) via EVENT INJECTION (oracle signature frozen).
    if messages_df is not None:
        try:
            updates = parse_messages(user_id, messages_df)
        except Exception:
            updates = {"salary_updates": [], "event_updates": []}
        # Apply event amendments to the copied list only.
        for update in updates.get("event_updates", []):
            for e in events_for_oracle:
                if e.get("event_id") == update.get("related_event_id"):
                    if "new_amount" in update:
                        e["amount"] = str(update["new_amount"])
                    if "new_date" in update:
                        e["settlement_date"] = update["new_date"]
                        e["event_date"] = update["new_date"]

        # Flag stability: detect recurrence on the PRE-injection copy so a huge
        # raise cannot drop the salary flag via CV (injection kept for amounts).
        try:
            _pre_injection = _copy.deepcopy(events_for_oracle)
        except Exception:
            _pre_injection = [dict(e) for e in events_for_oracle]
        recurrence_flags = detect_recurrence(_pre_injection)

        # Salary-raise via event injection: add/amend a scheduled salary income
        # event (category salary, event_type income) at the effective date.
        # Raises effective after the 90-day window do not affect current safe (skipped).
        try:
            _req_d = date.fromisoformat(str(req_date).strip())
            _win_end = _req_d + timedelta(days=90)
        except Exception:
            _req_d = None
            _win_end = None
        for update in updates.get("salary_updates", []):
            eff = update.get("effective_date")
            amt = update.get("amount")
            if not eff or amt is None:
                continue
            try:
                eff_s = str(eff).strip()
                eff_d = date.fromisoformat(eff_s)
            except Exception:
                continue
            if _win_end is not None and eff_d > _win_end:
                continue
            try:
                amt_dec = Decimal(str(amt))
            except Exception:
                continue
            ccy = update.get("currency") or profile.get("home_currency", "USD")
            # Amend when a salary income row already exists on the effective date.
            amended = False
            for e in events_for_oracle:
                if str(e.get("user_id", "") or "") not in ("", str(user_id)) and e.get("user_id") != user_id:
                    continue
                if e.get("category") == "salary" and str(e.get("event_type", "") or "").strip().lower() == "income":
                    if e.get("settlement_date") == eff_s or e.get("event_date") == eff_s:
                        e["amount"] = str(amt_dec)
                        e["currency"] = str(ccy)
                        # Ensure countable scheduled/settled income (leave failed/cancelled untouched).
                        if str(e.get("status", "") or "").strip().lower() in ("", "nan", "none"):
                            e["status"] = "scheduled"
                        amended = True
                        break
            if not amended:
                events_for_oracle.append({
                    "event_id": f"salary_raise_{eff_s}",
                    "user_id": user_id,
                    "event_type": "income",
                    "description": "Salary update via payroll message (injected)",
                    "category": "salary",
                    "direction": "credit",
                    "amount": str(amt_dec),
                    "currency": str(ccy),
                    "event_date": eff_s,
                    "settlement_date": eff_s,
                    "status": "scheduled",
                    "linked_event_id": "",
                    "flexibility": "fixed",
                    "minimum_allowed_amount": "",
                })
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
    res_now = solve_safe_amount(user_id, req_date, str(req_amt_dec), profile, events_for_oracle, fx_rows, recurrence_flags)
    safe_amt = res_now["safe_amount"]
    earliest = res_now["earliest_date"]

    def _meets(comp_date) -> bool:
        return meets_deadline(comp_date, desired_completion_date)

    def _valid_earliest(val) -> bool:
        try:
            date.fromisoformat(str(val))
            return True
        except Exception:
            return False

    # Build viable options
    viable_options = []

    # Option A: Full Payment (Now) â€” eligible only if user considers full_payment
    if safe_amt >= req_amt_dec and "full_payment" in will:
        viable_options.append({
            "option_id": "opt_full_now",
            "id": "opt_full_now",
            "payment_method": "full_payment",
            "amount_safe_to_pay": req_amt_dec,
            "affordability_status": "affordable_now",
            "payment_plan": f"{req_date}:{req_amt_dec}",
            "earliest_date": req_date,
            "spending_changes": "none",
            "cost": req_amt_dec,
            "total_cost": req_amt_dec,
            "completion_date": req_date,
            "changes": 0,
            "legs": 1,
            "start_date": req_date,
            "num_payments": 1
        })

    # Option B: Partial Payment â€” only when request allows, user accepts, 0<safe<req, second leg by deadline
    if Decimal("0") < safe_amt < req_amt_dec and earliest and "partial_payment" in will:
        partial_ok = True
        if allows_partial is False:
            partial_ok = False
        if not _meets(earliest):
            partial_ok = False
        if partial_ok:
            viable_options.append({
            "option_id": "opt_partial",
            "id": "opt_partial",
            "payment_method": "partial_payment",
            "amount_safe_to_pay": safe_amt,
            "affordability_status": "affordable_with_plan",
            "payment_plan": f"{req_date}:{safe_amt}|{earliest}:{req_amt_dec - safe_amt}",
            "earliest_date": earliest,
            "spending_changes": "none",
            "cost": req_amt_dec,
            "total_cost": req_amt_dec,
            "completion_date": earliest,
            "changes": 0,
            "legs": 2,
            "start_date": req_date,
            "num_payments": 2
        })

    # Option C: Installments â€” eligible only if user considers installments.
    # Strict (default): deadline + per-leg ledger safety (post-leg balances >= floor).
    # Lenient: deadline only (legacy behavior).
    strict_inst = (installment_safety != "lenient")
    ledger_now = res_now.get("ledger", [])
    valid_inst = get_valid_options(rid, profile, options_df)
    for opt in valid_inst:
        if opt["payment_method"] == "installments" and "installments" in will:
            if not _meets(opt.get("completion_date", opt["first_payment_date"])):
                continue
            if strict_inst and not _installment_safe(ledger_now, req_date, opt.get("payment_plan", ""), profile):
                continue
            viable_options.append({
                "option_id": opt["option_id"],
                "id": opt["option_id"],
                "payment_method": "installments",
                "amount_safe_to_pay": safe_amt if safe_amt < req_amt_dec else req_amt_dec,
                "affordability_status": "affordable_with_plan",
                "payment_plan": opt["payment_plan"],
                "earliest_date": earliest,
                "spending_changes": "none",
                "cost": opt.get("total_payable", opt["amount"]),
                "total_cost": opt.get("total_payable", opt["amount"]),
                "completion_date": opt.get("completion_date", opt["first_payment_date"]),
                "changes": 0,
                "legs": opt["number_of_payments"],
                "start_date": opt["first_payment_date"],
                "num_payments": opt["number_of_payments"]
            })

    # Option D: Wait â€” eligible when full becomes safe later and user accepts full_payment
    if earliest and _valid_earliest(earliest) and str(earliest) > str(req_date) and "full_payment" in will and _meets(earliest):
        viable_options.append({
            "option_id": "opt_wait",
            "id": "opt_wait",
            "payment_method": "wait",
            "amount_safe_to_pay": safe_amt,
            "affordability_status": "affordable_later",
            "payment_plan": f"{earliest}:{req_amt_dec}",
            "earliest_date": earliest,
            "spending_changes": "none",
            "cost": req_amt_dec,
            "total_cost": req_amt_dec,
            "completion_date": earliest,
            "changes": 0,
            "legs": 1,
            "start_date": earliest,
            "num_payments": 1
        })

    # Spec Â§Choosing: try permitted spending changes when no viable plan,
    # or when viable exist but ALL miss desired_completion_date.
    def _append_changed_options(changed_safe, changed_earliest, changed_ledger, changes_text):
        """Add safe variants to the same pool as no-change options."""
        change_count = len(changes_text.split("|"))
        if changed_safe >= req_amt_dec and "full_payment" in will:
            viable_options.append({
                "option_id": "opt_full_changed", "id": "opt_full_changed",
                "payment_method": "full_payment", "amount_safe_to_pay": req_amt_dec,
                "affordability_status": "affordable_with_plan",
                "payment_plan": f"{req_date}:{req_amt_dec}", "earliest_date": changed_earliest,
                "spending_changes": changes_text, "cost": req_amt_dec, "total_cost": req_amt_dec,
                "completion_date": req_date, "changes": change_count, "legs": 1,
                "start_date": req_date, "num_payments": 1,
            })
        if (Decimal("0") < safe_amt < req_amt_dec and allows_partial and
                "partial_payment" in will and changed_earliest and _meets(changed_earliest)):
            viable_options.append({
                "option_id": "opt_partial_changed", "id": "opt_partial_changed",
                "payment_method": "partial_payment", "amount_safe_to_pay": safe_amt,
                "affordability_status": "affordable_with_plan",
                "payment_plan": f"{req_date}:{safe_amt}|{changed_earliest}:{req_amt_dec - safe_amt}",
                "earliest_date": changed_earliest, "spending_changes": changes_text,
                "cost": req_amt_dec, "total_cost": req_amt_dec, "completion_date": changed_earliest,
                "changes": change_count, "legs": 2, "start_date": req_date, "num_payments": 2,
            })
        for opt in valid_inst:
            if opt["payment_method"] != "installments" or "installments" not in will:
                continue
            completion = opt.get("completion_date", opt["first_payment_date"])
            if not _meets(completion):
                continue
            if strict_inst and not _installment_safe(changed_ledger, req_date, opt.get("payment_plan", ""), profile):
                continue
            changed_id = f"{opt['option_id']}_changed"
            viable_options.append({
                "option_id": changed_id, "id": changed_id, "payment_method": "installments",
                "amount_safe_to_pay": safe_amt if safe_amt < req_amt_dec else req_amt_dec,
                "affordability_status": "affordable_with_plan", "payment_plan": opt["payment_plan"],
                "earliest_date": changed_earliest, "spending_changes": changes_text,
                "cost": opt.get("total_payable", opt["amount"]),
                "total_cost": opt.get("total_payable", opt["amount"]), "completion_date": completion,
                "changes": change_count, "legs": opt["number_of_payments"],
                "start_date": opt["first_payment_date"], "num_payments": opt["number_of_payments"],
            })
        if (changed_earliest and _valid_earliest(changed_earliest) and
                date.fromisoformat(str(changed_earliest)) > date.fromisoformat(str(req_date)) and
                "full_payment" in will and _meets(changed_earliest)):
            viable_options.append({
                "option_id": "opt_wait_changed", "id": "opt_wait_changed", "payment_method": "wait",
                "amount_safe_to_pay": safe_amt, "affordability_status": "affordable_later",
                "payment_plan": f"{changed_earliest}:{req_amt_dec}", "earliest_date": changed_earliest,
                "spending_changes": changes_text, "cost": req_amt_dec, "total_cost": req_amt_dec,
                "completion_date": changed_earliest, "changes": change_count, "legs": 1,
                "start_date": changed_earliest, "num_payments": 1,
            })

    spending_needed = "none"
    _all_miss = False
    if viable_options:
        try:
            _desired_present = _parse_deadline_date(desired_completion_date) is not None
        except Exception:
            _desired_present = False
        if _desired_present and all(not _meets(o.get("completion_date")) for o in viable_options):
            _all_miss = True
    if (not viable_options) or _all_miss:
        try:
            changes = search_changes(user_id, req_date, req_amt_dec, profile, events_for_oracle, fx_rows, recurrence_flags)
            if changes:
                spending_needed = "|".join(changes)
                # Re-evaluate under the SAME semantics search_changes validated:
                # in-window rows are real upcoming cash (copy edit), while
                # pre-request history never hits the ledger, so only category
                # suppression removes the category's forward forecast.
                mod_events = [dict(e) for e in events_for_oracle]
                suppressed_cats: set[str] = set()
                for ch in changes:
                    parts = ch.split(":")
                    target = None
                    if parts[0] == "stop" and len(parts) == 2:
                        target = parts[1]
                    elif parts[0] == "reduce_to" and len(parts) == 3:
                        target = parts[1]
                    if target is None:
                        continue
                    for e in mod_events:
                        if e.get("event_id") == target:
                            e["amount"] = "0" if parts[0] == "stop" else parts[2]
                            cat = str(e.get("category", "") or "").strip().lower()
                            if cat:
                                suppressed_cats.add(cat)
                res2 = solve_safe_amount(user_id, req_date, str(req_amt_dec), profile, mod_events, fx_rows,
                                         recurrence_flags, suppressed_categories=suppressed_cats)
                _append_changed_options(res2["safe_amount"], res2["earliest_date"],
                                        res2.get("ledger", []), spending_needed)
        except Exception:
            pass
        if not viable_options:
            return {
                "request_id": rid,
                "amount_safe_to_pay": str(safe_amt),
                "affordability_status": "not_affordable",
                "recommended_payment_method": "not_recommended",
                "payment_plan": "none",
                "earliest_date_for_full_payment": None,
                "spending_changes_needed": "none",
                "decision_explanation": "No viable payment options found."
            }

    # Spec rank order: deadline âœ“, no changes, minimize total, start earlier, fewer legs, lowest option_id
    def spec_key(o):
        return (
            0 if _meets(o.get("completion_date")) else 1,
            o.get("changes", 0),
            Decimal(str(o.get("total_cost", o.get("cost", "Infinity")))),
            o.get("start_date", "9999-12-31"),
            o.get("legs", o.get("num_payments", 999)),
            o.get("id", o.get("option_id", "zzz")),
        )
    ranked = sorted(viable_options, key=spec_key)
    best = ranked[0]

    return {
        "request_id": rid,
        "amount_safe_to_pay": str(safe_amt),
        "affordability_status": best["affordability_status"],
        "recommended_payment_method": best["payment_method"],
        "payment_plan": best["payment_plan"],
        "earliest_date_for_full_payment": earliest,
        "spending_changes_needed": best["spending_changes"],
        "decision_explanation": f"Recommended {best['payment_method']} based on ranking."
    }
