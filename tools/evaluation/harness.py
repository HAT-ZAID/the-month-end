"""P2 Validation Harness (TDD): per-field comparison of solver output vs gold anchors.

The harness audits the solver's decisions against the gold sample.
Mismatches are reported with: [request_id] field: expected <X>, got <Y>.
"""
from __future__ import annotations

import pandas as pd
from decimal import Decimal
from pathlib import Path


def normalize_decimal(v: str | float | Decimal) -> str:
    if v is None or str(v).strip() == "":
        return "None"
    try:
        # normalize to remove trailing zeros for consistency
        d = Decimal(str(v))
        return f"{d.normalize():f}"
    except Exception:
        return str(v)


def audit_field(actual: str, expected: str, field_name: str, rid: str) -> str | None:
    a_norm = normalize_decimal(actual)
    e_norm = normalize_decimal(expected)
    if a_norm != e_norm:
        return f"[{rid}] {field_name}: expected {e_norm}, got {a_norm}"
    return None


def run_audit(solver_df: pd.DataFrame, gold_df: pd.DataFrame) -> list[str]:
    errors = []
    # Join on request_id
    merged = gold_df.merge(solver_df, on="request_id", suffixes=("_gold", "_sol"))
    
    # Fields to audit (excluding explanation and id)
    fields = [
        "amount_safe_to_pay", 
        "affordability_status", 
        "recommended_payment_method", 
        "payment_plan", 
        "earliest_date_for_full_payment", 
        "spending_changes_needed"
    ]
    
    for _, row in merged.iterrows():
        rid = row["request_id"]
        for f in fields:
            err = audit_field(row[f"{f}_sol"], row[f"{f}_gold"], f, rid)
            if err:
                errors.append(err)
    
    return errors