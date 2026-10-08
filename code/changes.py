"""P2 Change Search (TDD): exhaustive search of <=3 combinations to find safe plans.

A 'change' is a stop or reduction of a flexible event.
Launder logic:
- Only reducible/stoppable events are candidates.
- Combinations of 1, 2, or 3 changes are tried in order.
- A change is 'safe' if the resulting balance simulation (P1 oracle) is green.
"""
from __future__ import annotations

from itertools import combinations
from decimal import Decimal
from code.oracle import solve_safe_amount


def get_candidates(events: list[dict]) -> list[tuple[str, str, Decimal]]:
    """Returns a list of (event_id, action, value) candidates."""
    candidates = []
    for e in events:
        eid = e["event_id"]
        flex = str(e.get("flexibility", "fixed")).strip().lower()
        try:
            amt = Decimal(str(e["amount"]))
        except Exception:
            continue
        stoppable = flex in ("stoppable", "reducible_or_stoppable")
        reducible = flex in ("reducible", "reducible_or_stoppable")
        if stoppable:
            candidates.append((eid, "stop", Decimal("0")))
        if reducible:
            try:
                min_amt = Decimal(str(e.get("minimum_allowed_amount", "0")))
            except Exception:
                continue
            if min_amt < amt:
                candidates.append((eid, "reduce_to", min_amt))
    return candidates


def search_changes(
    user_id: str,
    request_date: str,
    requested_amount: Decimal,
    profile: dict,
    events: list[dict],
    fx_rows: list[dict],
    recurrence_flags=None,
) -> list[str]:
    """Exhaustive search for the best set of spending changes.

    Priority:
    1. Fewest number of changes.
    2. Max total amount saved.
    """
    # 1. Get all possible single-action candidates, filtered to permitted
    # recurring flexible expense/subscription events owned by this user.
    # A change acts on the category's forward recurrence, not one historical
    # row, so a past-dated event remains a valid target.
    def _categories(key: str) -> set[str]:
        raw = str(profile.get(key, "") or "")
        return {part.strip().lower() for part in raw.split("|")
                if part.strip() and part.strip().lower() != "nan"}

    prot = _categories("expense_categories_to_protect")
    reduce_ok = _categories("expense_categories_user_is_willing_to_reduce")
    stop_ok = _categories("expense_categories_user_is_willing_to_stop")
    flags = set()
    for flag in recurrence_flags or ():
        try:
            uid, category, event_type = flag
            flags.add((str(uid), str(category).strip().lower(), str(event_type).strip().lower()))
        except (TypeError, ValueError):
            continue
    candidates = []
    for eid, action, val in get_candidates(events):
        ev = next((x for x in events if x.get("event_id") == eid), None)
        if ev is None:
            continue
        event_user = str(ev.get("user_id", "") or "")
        event_type = str(ev.get("event_type", "") or "").strip().lower()
        cat_raw = str(ev.get("category", "") or "")
        cat = cat_raw.strip().lower()
        flex = str(ev.get("flexibility", "") or "").strip().lower()
        direction = str(ev.get("direction", "") or "").strip().lower()
        status = str(ev.get("status", "") or "").strip().lower()
        if event_user != str(user_id) or event_type not in ("expense", "subscription"):
            continue
        if (event_user, cat, event_type) not in flags:
            continue
        if direction != "debit" or status not in {"settled", "scheduled", "pending"}:
            continue
        if status == "pending" and direction != "debit":
            continue
        if flex not in ("stoppable", "reducible", "reducible_or_stoppable"):
            continue
        if cat in prot:
            continue
        if action == "stop" and cat not in stop_ok:
            continue
        if action == "reduce_to" and cat not in reduce_ok:
            continue
        candidates.append((eid, action, val))

    # 2. Try combinations of size 1, then 2, then 3
    for size in range(1, 4):
        safe_combinations = []
        for combo in combinations(candidates, size):
            # Spec :198 — stop+reduce on the same event are mutually exclusive.
            if len({c[0] for c in combo}) != len(combo):
                continue
            # Two mechanisms act on disjoint date sets:
            # 1. Copy edit — an in-window row of a targeted event is real
            #    upcoming cash that a stop genuinely removes.
            modified_events = []
            for e in events:
                e_copy = dict(e)
                for eid, action, val in combo:
                    if e_copy["event_id"] == eid:
                        if action == "stop":
                            e_copy["amount"] = "0"
                        elif action == "reduce_to":
                            e_copy["amount"] = str(val)
                modified_events.append(e_copy)
            # 2. Suppression — pre-request history sits outside the window, so
            #    the copy edit is a no-op there; the oracle drops that
            #    category's forward forecast instead.
            cat_by_eid = {
                str(e.get("event_id", "")): str(e.get("category", "") or "").strip().lower()
                for e in events
            }
            suppressed = {cat_by_eid.get(eid, "") for eid, _action, _val in combo}
            suppressed.discard("")
            # Check safety using the same recurrence evidence used for filtering.
            flags_to_use = recurrence_flags if recurrence_flags is not None else set()
            res = solve_safe_amount(
                user_id, request_date, str(requested_amount),
                profile, modified_events, fx_rows, flags_to_use,
                suppressed_categories=suppressed,
            )
            if res["safe_amount"] >= requested_amount:
                # Calculate total savings by eid lookup (order-independent).
                amt_by_eid: dict[str, Decimal] = {}
                for e in events:
                    try:
                        amt_by_eid[e["event_id"]] = Decimal(str(e["amount"]))
                    except Exception:
                        continue
                savings = sum(
                    amt_by_eid.get(eid, Decimal("0")) - (Decimal("0") if action == "stop" else val)
                    for eid, action, val in combo
                )
                safe_combinations.append((combo, savings))

        if safe_combinations:
            # Among safe combos of this size, pick the one with max savings
            best_combo = max(safe_combinations, key=lambda x: x[1])[0]
            return [f"{action}:{eid}" if action == "stop" else f"reduce_to:{eid}:{val}"
                    for eid, action, val in best_combo]

    return []
