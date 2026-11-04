"""P1 FX resolution (TDD).

Policy (user decision): exact settlement-date rate wins; otherwise the nearest
prior dated snapshot for the same pair is used and flagged FX_UNKNOWN so the
ledger can downgrade affordability on ambiguity. A pair with no on-or-before
rate returns None (never an invented cross-multiplied hop), also flagged.

A rate is money arithmetic, so it is parsed with the strict finite helper: a
signed, zero, non-finite or unparsable rate is not a rate.  It fails closed as
FX_UNKNOWN on the same path as a missing snapshot, and an unparsable cell never
raises out of :func:`resolve_rate` into a position or decision caller.
"""
from __future__ import annotations

from decimal import Decimal

from code.amounts import strict_decimal


def _valid_rate(row: dict) -> Decimal | None:
    """The row's rate when it is a finite positive number, else ``None``."""
    rate = strict_decimal(row.get("rate"))
    if rate is None or rate <= 0:
        return None
    return rate


def resolve_rate(fx_rows: list[dict], from_ccy: str, to_ccy: str, rate_date: str) -> dict:
    if from_ccy == to_ccy:
        return {"rate": Decimal("1.0"), "flag": None, "method": "identity"}
    cands = [
        r
        for r in fx_rows
        if r.get("from_currency") == from_ccy and r.get("to_currency") == to_ccy
    ]
    if not cands:
        return {"rate": None, "flag": "FX_UNKNOWN", "method": "missing"}

    cands.sort(key=lambda r: r.get("rate_date") or "")
    priors = [r for r in cands if (r.get("rate_date") or "") <= rate_date]
    if priors:
        r = priors[-1]
        rate = _valid_rate(r)
        if rate is None:
            return {"rate": None, "flag": "FX_UNKNOWN", "method": "invalid"}
        method = "exact" if r.get("rate_date") == rate_date else "prior"
        return {
            "rate": rate,
            "flag": None if method == "exact" else "FX_UNKNOWN",
            "method": method,
        }

    return {"rate": None, "flag": "FX_UNKNOWN", "method": "missing"}
