"""Semantic sample verifier (Task 1).

Delegates to the production pipeline (``code.main.run_pipeline``) so the same
OCR/evidence resolution, fail-closed rows, strict installment safety and
serialization used for the deliverable are exercised, then compares the six
machine fields semantically via ``evaluation.semantics``. Replaces the old
string comparator that misread blanks/NaN and skipped image evidence.

Run from the repo root::

    python -m tools.verify_samples

Exit code 0 only when every machine field on every public sample row matches
semantically.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `python tools/verify_samples.py` as well as `python -m tools.verify_samples`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from code.main import run_pipeline
from evaluation import semantics


def run_verification(repo_root=None) -> int:
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    sample_path = root / "dataset" / "sample_requests.csv"

    output_df, usage = run_pipeline(
        repo_root=root,
        requests_path=sample_path,
        write_output=False,
        write_usage=False,
    )
    reference = pd.read_csv(sample_path, dtype=str, keep_default_na=False)

    comparison = semantics.compare_rows(output_df, reference)
    total = comparison["total"]
    print("== semantic sample verification ==")
    print(f"rows compared: {comparison['compared']}/{total}")
    print(f"full-row semantic matches: {comparison['full_row_matches']}/{total}")
    for field in semantics.MACHINE_FIELDS:
        correct = comparison["per_field_correct"].get(field, 0)
        print(f"  {field}: {correct}/{total}")
    spending = comparison["spending_changes"]
    print(
        "spending changes: "
        f"TP {spending['true_positives']} FP {spending['false_positives']} "
        f"FN {spending['false_negatives']}"
    )
    print(
        "usage: "
        f"provider={usage.get('provider', 'none')} "
        f"model={usage.get('model', 'none')} "
        f"calls={usage.get('calls', 0)} "
        f"total_tokens={usage.get('total_tokens', 0)}"
    )

    mismatches = comparison["mismatches"]
    if comparison["missing_request_ids"]:
        print(f"missing request IDs: {comparison['missing_request_ids']}")
    if comparison["unexpected_request_ids"]:
        print(f"unexpected request IDs: {comparison['unexpected_request_ids']}")
    if comparison["duplicate_request_ids"]:
        print(f"duplicate request IDs: {comparison['duplicate_request_ids']}")
    for mismatch in mismatches:
        print(
            f"[{mismatch['request_id']}] {mismatch['field']}: "
            f"expected {mismatch['expected']!r}, got {mismatch['actual']!r}"
        )
    print(f"semantic mismatches: {len(mismatches)}")
    return 0 if not mismatches and not comparison["missing_request_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(run_verification())
