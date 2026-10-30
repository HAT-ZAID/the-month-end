"""P2 Spending-change search: stop/reduce candidates as first-class plan input.

A 'change' is a stop or reduction of a user-owned, non-protected, flexible
recurring expense or subscription in a category the profile explicitly permits.
This module owns four things:

* ``permitted_actions`` — the individual actions a user actually permits
  (recurrence evidence, ownership, flexibility, stop/reduce permission lists,
  protected categories), ranked by potential savings;
* ``build_change_sets`` — a bounded list of change sets (size 1, then 2, then 3,
  never two actions on one event, and never a ``reduce_to`` sharing its category
  with another action), capped at :data:`MAX_CHANGE_SETS`;
* ``apply_changes`` — the single application mechanism used by every caller:
  the targeted in-window rows carry the new amount, a ``stop`` suppresses the
  category's forward forecast, and a ``reduce_to`` overrides the category's
  forward run-rate to the new amount (the category is never deleted);
* ``search_changes`` — the legacy convenience wrapper returning the smallest
  change set that makes a full payment safe.

Launder logic: a change is only real if the resulting ledger (P1 oracle) is
green, and only categories the profile permits may be touched at all.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import date
from itertools import combinations

from code.oracle import solve_safe_amount

MAX_CHANGE_SETS = 24
MAX_CHANGE_ACTIONS = 3
_ACTIONS = ("stop", "reduce_to")


def _profile_categories(profile: dict, key: str) -> set[str]:
    raw = str((profile or {}).get(key, "") or "")
    return {
        part.strip().lower()
        for part in raw.split("|")
        if part.strip() and part.strip().lower() != "nan"
    }


def _recurring_flags(recurrence_flags) -> set[tuple[str, str, str]]:
    flags: set[tuple[str, str, str]] = set()
    for flag in recurrence_flags or ():
        try:
            uid, category, event_type = flag
        except (TypeError, ValueError):
            continue
        flags.add((str(uid), str(category).strip().lower(), str(event_type).strip().lower()))
    return flags


def get_candidates(events: list[dict]) -> list[tuple[str, str, Decimal]]:
    """Returns a list of (event_id, action, value) candidates."""
    candidates = []
    for e in events:
        eid = e["event_id"]
        flex = str(e.get("flexibility", "fixed")).strip().lower()
        try:
            amt = Decimal(str(e["amount"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        stoppable = flex in ("stoppable", "reducible_or_stoppable")
        reducible = flex in ("reducible", "reducible_or_stoppable")
        if stoppable:
            candidates.append((eid, "stop", Decimal("0")))
        if reducible:
            try:
                min_amt = Decimal(str(e.get("minimum_allowed_amount", "0")))
            except (InvalidOperation, TypeError):
                continue
            if min_amt < amt:
                candidates.append((eid, "reduce_to", min_amt))
    return candidates


def _action_date(event: dict) -> str:
    return str(event.get("settlement_date") or event.get("event_date") or "").strip()


def _date_ordinal(value: str) -> int:
    try:
        return date.fromisoformat(value).toordinal()
    except (TypeError, ValueError):
        return 0


def permitted_actions(
    user_id: str,
    profile: dict,
    events: list[dict],
    recurrence_flags=None,
) -> list[dict]:
    """Individual stop/reduce actions the profile permits, newest row first.

    A change acts on the category's forward recurrence, not on one historical
    row, so a past-dated event remains a valid target.  The newest row of a
    category documents its current run-rate (and carries its reduction floor),
    so it is the representative target: newer records from the same source win
    the tie (AGENTS.md §6.3).  Each action is a dict:
    ``{"event_id", "action", "value", "category", "savings", "date"}``.
    """
    protected = _profile_categories(profile, "expense_categories_to_protect")
    reduce_ok = _profile_categories(profile, "expense_categories_user_is_willing_to_reduce")
    stop_ok = _profile_categories(profile, "expense_categories_user_is_willing_to_stop")
    flags = _recurring_flags(recurrence_flags)
    by_id = {str(e.get("event_id", "")): e for e in events}

    actions: list[dict] = []
    for eid, action, value in get_candidates(events):
        event = by_id.get(eid)
        if event is None:
            continue
        event_user = str(event.get("user_id", "") or "")
        event_type = str(event.get("event_type", "") or "").strip().lower()
        category = str(event.get("category", "") or "").strip().lower()
        flexibility = str(event.get("flexibility", "") or "").strip().lower()
        direction = str(event.get("direction", "") or "").strip().lower()
        status = str(event.get("status", "") or "").strip().lower()
        if event_user != str(user_id) or event_type not in ("expense", "subscription"):
            continue
        if (event_user, category, event_type) not in flags:
            continue
        if direction != "debit" or status not in {"settled", "scheduled", "pending"}:
            continue
        if flexibility not in ("stoppable", "reducible", "reducible_or_stoppable"):
            continue
        if category in protected:
            continue
        if action == "stop" and category not in stop_ok:
            continue
        if action == "reduce_to" and category not in reduce_ok:
            continue
        try:
            current = Decimal(str(event.get("amount", "")))
        except (InvalidOperation, TypeError):
            continue
        if action == "stop":
            savings = current
        else:
            savings = current - value
        actions.append({
            "event_id": eid,
            "action": action,
            "value": value,
            "category": category,
            "savings": savings if savings > 0 else Decimal("0"),
            "date": _action_date(event),
        })

    # Candidate order, after the profile filters:
    #   1. category potential (largest saving the category can offer) — the brief
    #      ranks candidate actions by potential savings;
    #   2. inside a category, the newest row first: it documents the current
    #      run-rate and carries the reduction floor, and newer records from the
    #      same source win conflicts (AGENTS.md §6.3);
    #   3. verb, saving, then a stable id so the order is deterministic.
    potential: dict[str, Decimal] = {}
    for action in actions:
        potential[action["category"]] = max(
            potential.get(action["category"], Decimal("0")), action["savings"]
        )
    actions.sort(
        key=lambda a: (
            -potential.get(a["category"], Decimal("0")),
            a["category"],
            _ACTIONS.index(a["action"]),
            -_date_ordinal(a["date"]),
            -a["savings"],
            a["event_id"],
        )
    )
    return actions


def _categories_compatible(combo) -> bool:
    """A reduce_to may not share its category with any other action."""
    by_category: dict[str, list[str]] = {}
    for action in combo:
        by_category.setdefault(action.get("category", ""), []).append(action.get("action", ""))
    for verbs in by_category.values():
        if len(verbs) > 1 and "reduce_to" in verbs:
            return False
    return True


def build_change_sets(
    user_id: str,
    profile: dict,
    events: list[dict],
    recurrence_flags=None,
    *,
    limit: int = MAX_CHANGE_SETS,
) -> list[list[dict]]:
    """Bounded change sets, smallest first, capped at ``limit`` in total.

    Two actions on the same event (``stop`` + ``reduce_to``) are mutually
    exclusive per the spec.  A ``reduce_to`` also cannot share its category with
    another action, because both would fight over the same forward override;
    several ``stop`` actions on different rows of one category are fine.

    Set order follows :func:`permitted_actions` (category potential, then the
    newest row of the category), so the first candidate of each size is the
    best-supported, most current one.
    """
    actions = permitted_actions(user_id, profile, events, recurrence_flags)
    sets: list[list[dict]] = []
    for size in range(1, MAX_CHANGE_ACTIONS + 1):
        rung: list[list[dict]] = []
        for combo in combinations(actions, size):
            if len({a["event_id"] for a in combo}) != size:
                continue
            if not _categories_compatible(combo):
                continue
            rung.append(list(combo))
        sets.extend(rung)
    return sets[: max(0, int(limit))]


def describe_action(action: dict) -> str:
    """Render one action in the output grammar."""
    if action.get("action") == "stop":
        return f"stop:{action['event_id']}"
    return f"reduce_to:{action['event_id']}:{format(action['value'], 'f')}"


def apply_changes(events: list[dict], changes) -> tuple[list[dict], set[str], dict[str, Decimal]]:
    """Apply a change set to a copy of the events.

    Returns ``(modified_events, suppressed_categories, category_overrides)``.
    - the targeted in-window row carries the new amount (0 for ``stop``);
    - ``stop`` suppresses the category's forward forecast;
    - ``reduce_to`` overrides the category's forward run-rate, never removing it.
    """
    modified = [dict(event) for event in events]
    by_id = {str(event.get("event_id", "")): event for event in modified}
    suppressed: set[str] = set()
    overrides: dict[str, Decimal] = {}
    for action in changes or ():
        eid = str(action.get("event_id", ""))
        verb = str(action.get("action", ""))
        value = action.get("value", Decimal("0"))
        try:
            target = Decimal("0") if verb == "stop" else Decimal(str(value))
        except (InvalidOperation, TypeError):
            continue
        event = by_id.get(eid)
        category = str(action.get("category", "") or "").strip().lower()
        if not category and event is not None:
            category = str(event.get("category", "") or "").strip().lower()
        if event is not None:
            event["amount"] = format(target, "f")
        if not category:
            continue
        if verb == "stop":
            suppressed.add(category)
        else:
            overrides[category] = target
    return modified, suppressed, overrides


def search_changes(
    user_id: str,
    request_date: str,
    requested_amount: Decimal,
    profile: dict,
    events: list[dict],
    fx_rows: list[dict],
    recurrence_flags=None,
    *,
    evidence_flags=None,
    expense_facts=None,
) -> list[str]:
    """Smallest change set that makes the full request amount safe.

    Kept as a convenience for direct callers and tests; the solver builds and
    ranks whole candidate plans from :func:`build_change_sets` instead.
    """
    requested = Decimal(str(requested_amount))
    for size in range(1, MAX_CHANGE_ACTIONS + 1):
        best: tuple[Decimal, list[dict]] | None = None
        for combo in build_change_sets(user_id, profile, events, recurrence_flags):
            if len(combo) != size:
                continue
            modified, suppressed, overrides = apply_changes(events, combo)
            res = solve_safe_amount(
                user_id, request_date, str(requested),
                profile, modified, fx_rows, recurrence_flags or set(),
                suppressed_categories=suppressed,
                category_overrides=overrides,
                evidence_flags=evidence_flags,
                expense_facts=expense_facts,
            )
            if res.get("fail_closed"):
                continue
            if res["safe_amount"] < requested:
                continue
            savings = sum((a["savings"] for a in combo), Decimal("0"))
            if best is None or savings > best[0]:
                best = (savings, combo)
        if best is not None:
            return [describe_action(action) for action in best[1]]
    return []
