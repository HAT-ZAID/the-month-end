"""P2 Ranking logic — the single ranking authority for payment plans.

Symmetry: a lower value is always better (changes, cost, date, legs, ID).

Order (problem_statement.md "Choosing Between Safe Plans", Task 4 brief §4):

1. deadline feasibility — a hard filter, applied before sorting: a plan that
   completes after ``desired_completion_date`` is not a candidate at all and is
   never ranked last;
2. number of spending changes (fewer is better);
3. total cost (cheapest is better);
4. start date (earlier is better);
5. number of payment legs (fewer is better);
6. option id (stable, deterministic tie-break).

Accepting a float completion date keeps the ORM-free callers simple, but any
date that cannot be parsed is treated as unverifiable: with a deadline present
it is dropped rather than ranked (fail closed).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from code.amounts import to_decimal

_BLANK = ("", "nan", "none", "null", "nat", "n/a", "na")
_FAR_FUTURE = date.max


def parse_deadline(value) -> date | None:
    """Parse a deadline/completion value; blank, NaN or unparsable => None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        if isinstance(value, float) and value != value:  # NaN
            return None
    except TypeError:
        return None
    text = str(value).strip()
    if not text or text.lower() in _BLANK:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _decimal(value, default: Decimal = Decimal("Infinity")) -> Decimal:
    return to_decimal(value, default)


def _sort_key(option: dict):
    return (
        int(option.get("changes", 0) or 0),
        _decimal(option.get("total_cost", option.get("cost", "Infinity"))),
        str(option.get("start_date") or "9999-12-31"),
        int(option.get("legs", option.get("num_payments", 999)) or 999),
        str(option.get("id") or option.get("option_id") or "zzz"),
    )


def feasible_by_deadline(option: dict, deadline: date | None) -> bool:
    """True when the option can complete by ``deadline`` (None = no constraint)."""
    if deadline is None:
        return True
    completion = parse_deadline(option.get("completion_date"))
    if completion is None:
        return False
    return completion <= deadline


def rank_options(options: list[dict], desired_completion_date=None) -> list[dict]:
    """Drop deadline-infeasible plans, then sort the survivors per the spec order."""
    deadline = parse_deadline(desired_completion_date)
    pool = [option for option in (options or ()) if feasible_by_deadline(option, deadline)]
    return sorted(pool, key=_sort_key)
