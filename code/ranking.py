"""P2 Ranking logic (TDD): 6-rank order tie-breaking.

Symmetry: a lower value is always better (cost, date, count, ID).
"""
from __future__ import annotations

from decimal import Decimal


def rank_options(options: list[dict]) -> list[dict]:
    """Sort options according to the 6-rank order tie-breaking rules.

    Order: Completion Date -> Num Changes -> Total Cost -> Start Date -> Num Legs -> Option ID.
    (problem_statement.md Choosing Between Safe Plans)
    """
    # We sort by a tuple of keys. In Python, tuples are compared lexicographically.
    # For every key, lower is better.
    def sort_key(opt: dict):
        return (
            opt.get("completion_date", "9999-12-31"),
            opt.get("changes", 999),
            Decimal(str(opt.get("total_cost", "Infinity"))),
            opt.get("start_date", "9999-12-31"),
            opt.get("legs", 999),
            opt.get("id", "zzz"),
        )

    return sorted(options, key=sort_key)