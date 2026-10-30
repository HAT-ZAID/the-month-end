"""Experiment: does a typed decision model agree with the deterministic kernel?

The kernel decides every row; Jev (OpenCode Zen, System One) is asked the same
typed questions about the same facts and its answers are compared.  Nothing here
writes ``output.csv`` or changes any machine field — the gate is an audit, and a
disagreement is a calibration signal for humans, never an automatic edit.

Run:

```bash
python -m tools.compare_jev                 # 25-row sample, ~1 call per request
python -m tools.compare_jev --limit 5       # quick pass
python -m tools.compare_jev --dry-run       # build states, no network
```

Credentials are the OpenCode key (env ``OPENCODE_API_KEY`` or the desktop
client's ``auth.json``); the free ``jev-1.13-free`` model is the default target.
Output: ``docs/jev-decision-gate.md`` (+ ``.json`` with the raw rows).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sys_path_setup() -> None:  # keep import order tidy for direct execution
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


_sys_path_setup()

from code.jev import (  # noqa: E402
    DEFAULT_MODEL,
    JevGate,
    METHOD_OPTIONS,
    STATUS_OPTIONS,
    evaluate,
    implied_choice,
    load_api_key,
)  # noqa: E402
from code.main import run_pipeline  # noqa: E402
from code.tools import DatasetBundle, Toolbox  # noqa: E402

SAMPLE_REQUESTS = "dataset/sample_requests.csv"
MACHINE_FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def _read(name: str):
    import pandas as pd

    return pd.read_csv(Path(REPO_ROOT) / "dataset" / name, dtype=str, keep_default_na=False)


def load_resolved_bundle():
    """Toolbox view of the sample requests after the same evidence resolution
    the production pipeline applies, so the gate sees what the kernel saw."""
    from code.evidence import resolve_event_amounts
    from code.ocr import configure_tesseract
    from code.vision import build_backend, read_evidence

    events = _read("financial_events.csv").to_dict("records")
    images = _read("images.csv")
    ocr_path = configure_tesseract()
    vision_backend, _reason = build_backend()
    if vision_backend is None:
        events, warnings = resolve_event_amounts(
            images, events, Path(REPO_ROOT) / "dataset/media/images", ocr_available=ocr_path is not None,
        )
    else:
        events, warnings = resolve_event_amounts(
            images, events, Path(REPO_ROOT) / "dataset/media/images",
            reader=lambda image_path: read_evidence(image_path, vision_backend),
        )
    return DatasetBundle.from_frames(
        requests=_read("sample_requests.csv"),
        profiles=_read("financial_profiles.csv"),
        events=events,
        fx=_read("exchange_rates.csv").to_dict("records"),
        options=_read("request_payment_options.csv"),
        messages=_read("messages.csv"),
        images=images,
        evidence_warnings=warnings,
    )


def _pick(d: dict, keys) -> dict:
    return {key: d.get(key) for key in keys if key in d}


def build_state(toolbox: Toolbox, request_id: str, kernel_row: dict) -> dict | None:
    """Compact, typed state: the same facts the kernel saw, nothing invented."""
    context = toolbox.get_request_context(request_id)
    if not context.get("ok"):
        return None
    data = context.get("data") or {}
    request = _pick(data.get("request") or {}, (
        "request_id", "user_id", "request_type", "requested_amount", "currency",
        "request_date", "desired_completion_date", "allows_partial_payment",
        "request_text",
    ))
    profile = data.get("profile") or {}
    profile_state = _pick(profile, (
        "home_currency", "current_available_balance", "minimum_balance_to_keep",
        "financial_priorities", "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "max_installment_months",
    ))
    options = [
        _pick(option, (
            "payment_option_id", "payment_method", "payment_amount",
            "number_of_payments", "first_payment_date", "payment_frequency_days",
            "financing_fee", "total_payable_amount",
        ))
        for option in (data.get("options") or [])
    ]
    projection = toolbox.get_cashflow_projection(request_id)
    projection_state = {}
    if projection.get("ok"):
        projection_state = _pick(projection.get("data") or {}, (
            "safe_amount", "earliest_date", "fail_closed", "fail_closed_reason",
            "ledger_summary",
        ))
    decision_state = {key: kernel_row.get(key) for key in (
        "affordability_status", "recommended_payment_method", "amount_safe_to_pay",
        "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed",
    )}
    return {
        "request": request,
        "profile": profile_state,
        "payment_options": [option for option in options if option],
        "oracle_projection": projection_state,
        "kernel_decision": decision_state,
    }


def _clean(value):
    """DataFrame cells arrive as NaN for empty date columns; make them null."""
    if isinstance(value, float) and value != value:
        return None
    return value


def _row_of(frame, request_id: str) -> dict | None:
    if frame is None:
        return None
    matches = frame[frame["request_id"] == request_id]
    if matches.empty:
        return None
    return {column: _clean(matches.iloc[0][column]) for column in frame.columns}


def _markdown(rows, meta, path: Path) -> None:
    asked = [row for row in rows if row.get("ok")]
    status_match = sum(1 for row in asked if row.get("status_match"))
    method_match = sum(1 for row in asked if row.get("method_match"))
    answerable = [row for row in asked if row.get("status_pred") or row.get("method_pred")]
    lines = [
        "# Jev decision gate vs the deterministic kernel",
        "",
        f"- endpoint: `{meta['base_url']}`",
        f"- model: `{meta['model']}`",
        f"- requests asked: {len(rows)}, answered: {len(asked)}, errors: {len(rows) - len(asked)}",
        "",
        "Machine fields are provably untouched: the gate reads the solved rows and",
        "writes nothing back.  Every number below is a *comparison*, not a source.",
        "",
        "| request | kernel status | Jev status (noul) | match | kernel method | Jev method (noul) | match | safety (noul, lower-better) | evidence (noul) | tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if not row.get("ok"):
            lines.append(
                f"| {row.get('request_id')} "
                f"| {row.get('kernel', {}).get('status') or '-'} | error | - "
                f"| {row.get('kernel', {}).get('method') or '-'} | error | - "
                f"| - | - | - |"
            )
            continue
        safety = row.get("decision_safety_noul")
        evidence = row.get("evidence_score")
        status_cell = "-"
        if row.get("status_pred"):
            status_cell = f"{row['status_pred']} ({row.get('status_prob') if row.get('status_prob') is not None else '?'})"
        method_cell = "-"
        if row.get("method_pred"):
            method_cell = f"{row['method_pred']} ({row.get('method_prob') if row.get('method_prob') is not None else '?'})"
        lines.append(
            f"| {row['request_id']} "
            f"| {row['kernel']['status']} | {status_cell} | {'yes' if row['status_match'] else 'no'} "
            f"| {row['kernel']['method']} | {method_cell} | {'yes' if row['method_match'] else 'no'} "
            f"| {safety if safety is not None else '-'} | {evidence if evidence is not None else '-'} "
            f"| {row['tokens']} |"
        )
    totals_in = sum((row.get("usage") or {}).get("input_tokens") or 0 for row in asked)
    totals_out = sum((row.get("usage") or {}).get("output_tokens") or 0 for row in asked)
    costs = [float(row["usage"]["cost"]) for row in asked if row["usage"].get("cost") is not None]
    lines += [
        "",
        "## Summary",
        "",
        f"- status agreement: {status_match}/{len(answerable)} answered ({len(rows)} asked)",
        f"- method agreement: {method_match}/{len(answerable)} answered",
        f"- input tokens: {totals_in}, output tokens: {totals_out}, cost: {sum(costs) if costs else 0.0}",
        f"- mean safety noul: {_mean([row['decision_safety_noul'] for row in asked if row.get('decision_safety_noul') is not None])}",
        f"- mean evidence score: {_mean([row['evidence_score'] for row in asked if row.get('evidence_score') is not None])}",
        "",
        "## Reading this",
        "",
        "The free decision model answers only `noul` propositions - the probability",
        "that a stated fact is true - and refuses choice/score questions with",
        "422 \"Endpoint is unavailable\".  So the gate asks one noul question per",
        "candidate value (`status_is_affordable_now`, `method_is_wait`, ...) and the",
        "\"Jev status/method\" columns are the argmax of those probabilities, with",
        "the winning probability in parentheses.",
        "",
        "A `noul` near 0 for safety means Jev sees no plausible way the recorded",
        "decision violates the minimum-balance rule; a high value flags the row",
        "for a human.  Method/status mismatches are not errors in either side:",
        "they are where a typed System One model and a rule-following kernel",
        "disagree, and are the starting point for the next kernel calibration",
        "round.  `output.csv` was not touched.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _mean(values) -> str:
    values = [float(v) for v in values if v is not None]
    return f"{sum(values) / len(values):.3f}" if values else "n/a"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=25, help="how many sample requests to ask")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between gate calls")
    parser.add_argument("--model", default=None, help=f"Jev model (default {DEFAULT_MODEL})")
    parser.add_argument("--out", default="docs/jev-decision-gate.md")
    parser.add_argument("--json", dest="json_path", default="docs/jev-decision-gate.json")
    parser.add_argument("--dry-run", action="store_true", help="build states only, no network")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    gate = JevGate(model=args.model)
    key, source = load_api_key()
    described = gate.describe()
    if args.dry_run:
        described = dict(described, dry_run=True)
    print(json.dumps(described, indent=2))
    if not args.dry_run and not described.get("available"):
        print(f"Jev gate unavailable: {source}", file=sys.stderr)
        return 1

    # Kernel rows first: the gate audits decisions the kernel already made.
    frame, _usage = run_pipeline(
        REPO_ROOT,
        requests_path=Path(REPO_ROOT) / SAMPLE_REQUESTS,
        write_output=False,
        write_usage=False,
    )
    toolbox = Toolbox(REPO_ROOT, data=load_resolved_bundle())
    request_ids = list(frame["request_id"])[: max(0, int(args.limit))]

    rows = []
    for index, request_id in enumerate(request_ids):
        kernel_row = _row_of(frame, request_id) or {}
        state = build_state(toolbox, request_id, kernel_row)
        if state is None:
            rows.append({"request_id": request_id, "ok": False, "kernel": {"status": "-", "method": "-"}, "error": "no tool context"})
            continue
        if args.dry_run:
            rows.append({"request_id": request_id, "ok": False, "dry_run": True, "state": state, "kernel": {"status": kernel_row.get("affordability_status", "-"), "method": kernel_row.get("recommended_payment_method", "-")}})
            continue
        outcome = evaluate(gate, state)
        record = {
            "request_id": request_id,
            "ok": bool(outcome.ok),
            "error": outcome.error,
            "usage": outcome.usage,
            "latency_ms": outcome.latency_ms,
            "kernel": {
                "status": kernel_row.get("affordability_status"),
                "method": kernel_row.get("recommended_payment_method"),
            },
        }
        if outcome.ok:
            status_pred, status_prob = implied_choice(outcome, "status_is_", STATUS_OPTIONS)
            method_pred, method_prob = implied_choice(outcome, "method_is_", METHOD_OPTIONS)
            record.update({
                "status_pred": status_pred,
                "status_prob": status_prob,
                "status_match": bool(status_pred == record["kernel"]["status"]),
                "method_pred": method_pred,
                "method_prob": method_prob,
                "method_match": bool(method_pred == record["kernel"].get("method")),
                "decision_safety_noul": (outcome.answers.get("decision_safety").value
                                         if "decision_safety" in outcome.answers else None),
                "evidence_score": (outcome.answers.get("evidence_sufficiency").value
                                   if "evidence_sufficiency" in outcome.answers else None),
                "tokens": f"in={(outcome.usage.get('input_tokens') or 0)} out={(outcome.usage.get('output_tokens') or 0)}",
            })
        else:
            record["tokens"] = "-"
        rows.append(record)
        print(f"[{index + 1}/{len(request_ids)}] {request_id}: ok={record['ok']}", flush=True)
        if args.sleep > 0 and index + 1 < len(request_ids):
            time.sleep(args.sleep)

    doc_path = Path(args.out)
    _markdown(rows, {"base_url": gate.base_url, "model": gate.model}, doc_path)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"rows": rows, "meta": {"base_url": gate.base_url, "model": gate.model, "dry_run": bool(args.dry_run)}}, indent=2, default=str),
            encoding="utf-8",
            newline="\n",
        )
    successful = sum(1 for row in rows if row.get("ok"))
    print(f"Wrote {doc_path}" + (f" and {args.json_path}" if args.json_path else ""))
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
