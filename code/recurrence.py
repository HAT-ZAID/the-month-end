"""P1 recurrence detection (TDD): >=3 observations + conservative CV gate.

An event type for a (user, category, event_type) group is treated as recurring
only when there are at least three observations AND the coefficient of
variation of the amounts stays below a conservative threshold. High-variance
spending is NOT extrapolated as a committed recurring cost.
"""
from __future__ import annotations

import math
from collections import defaultdict
from math import sqrt

CV_THRESHOLD = 0.5  # conservative: stable spending only


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _cv(vals: list[float]) -> float:
    if len(vals) < 2:
        return 1.0
    m = _mean(vals)
    if m == 0:
        return 0.0 if all(v == 0 for v in vals) else 1.0
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return sqrt(var) / abs(m)


def detect_recurrence(events: list[dict]) -> set[tuple[str, str, str]]:
    """Return the set of (user_id, category, event_type) groups that are recurring."""
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for e in events:
        try:
            uid = e.get("user_id")
            cat = e.get("category")
            et = e.get("event_type")
            status = str(e.get("status", "") or "").strip().lower()
            direction = str(e.get("direction", "") or "").strip().lower()
        except AttributeError:
            continue
        if uid is None or cat is None or et is None:
            continue
        # Recurrence evidence must use cash-state rows only. Preserve the
        # legacy interface for settled rows that omit direction, but exclude
        # explicitly non-cash, failed/unknown, and pending-credit records.
        if direction == "non_cash":
            continue
        if status and status not in {"settled", "scheduled", "pending"}:
            continue
        if status == "pending" and direction != "debit":
            continue
        key = (uid, cat, et)
        amt = _amount(e)
        if amt is not None:
            groups[key].append(amt)

    return {
        key
        for key, vals in groups.items()
        if len(vals) >= 3 and _cv(vals) <= CV_THRESHOLD
    }


def _amount(e: dict) -> float | None:
    try:
        v = e.get("amount")
        if v is None:
            return None
        if isinstance(v, (int, float)):
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f)) else f
        v = str(v).strip()
        if not v or v.lower() == "nan":
            return None
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None
