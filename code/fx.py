"""P1 FX resolution (TDD).

Policy (user decision): exact settlement-date rate wins; otherwise the nearest
prior dated snapshot for the same pair is used and flagged FX_UNKNOWN so the
ledger can downgrade affordability on ambiguity. A pair with no on-or-before
rate returns None (never an invented cross-multiplied hop), also flagged.
"""
from __future__ import annotations

from decimal import Decimal


def resolve_rate(fx_rows: list[dict], from_ccy: str, to_ccy: str, rate_date: str) -> dict:
    if from_ccy == to_ccy:
        return {"rate": Decimal("1.0"), "flag": None, "method": "identity"}
    cands = [
        r
        for r in fx_rows
        if r["from_currency"] == from_ccy and r["to_currency"] == to_ccy
    ]
    if not cands:
        return {"rate": None, "flag": "FX_UNKNOWN", "method": "missing"}

    cands.sort(key=lambda r: r["rate_date"])
    priors = [r for r in cands if r["rate_date"] <= rate_date]
    if priors:
        r = priors[-1]
        method = "exact" if r["rate_date"] == rate_date else "prior"
        return {
            "rate": Decimal(str(r["rate"])),
            "flag": None if method == "exact" else "FX_UNKNOWN",
            "method": method,
        }

    return {"rate": None, "flag": "FX_UNKNOWN", "method": "missing"}