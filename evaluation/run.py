"""Single-command evaluation authority for the Buy or Wait? submission.

Run the PR gate::

    python -m evaluation.run --mode pr

Run the (still partial) release gate::

    python -m evaluation.run --mode release --provider replay --archive code.zip

The harness executes the real production pipeline in ``code.main.run_pipeline``:
it never synthesizes rows and never bypasses OCR/evidence resolution.  PR mode
runs the full 250-row production pipeline once (writing ``output.csv`` and the
usage reports, exactly like ``python -m code.main``) and the 25-row sample
pipeline twice from a clean state for semantic quality plus determinism.

Gate slots G5 (archive), G6 (security archive/history) and G7 (agent
robustness) are reported as ``NOT_APPLICABLE`` until the later tasks create
their artifacts.  A required ``NOT_APPLICABLE`` gate makes the overall status
``INCOMPLETE`` and the process exits non-zero; it never fakes a pass.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from evaluation import semantics

from code.paths import fixture_dir

SCHEMA_VERSION = 1
PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"

# Targets from the approved plan ("Target metric set after deterministic kernel
# work"). They are deliberately explicit and report the raw counts next to them.
SAMPLE_THRESHOLDS = {
    "full_row_matches": 20,
    "per_field": {
        "amount_safe_to_pay": 22,
        "affordability_status": 23,
        "recommended_payment_method": 23,
        "payment_plan": 22,
        "earliest_date_for_full_payment": 22,
    },
    "spending_recall": (2, 3),
    "spending_precision": 0.5,
}

LLM_ENV_KEYS = (
    "BUY_WAIT_LLM_ENDPOINT",
    "BUY_WAIT_LLM_API_KEY",
    "BUY_WAIT_LLM_MODEL",
    "BUY_WAIT_LLM_PROVIDER",
    "OPENROUTER_KEY",
)

# Measured once, before any kernel task, with decision logic at dde3b48. These
# values are reported for context only; the gates always use the live run.
PRE_FIX_BASELINE = {
    "commit": "dde3b486a896",
    "captured_on": "2026-10-28",
    "note": (
        "Measured by the first harness run before kernel work. The earlier "
        "manual review recorded 2/25 full rows; the semantic comparator also "
        "accepts cent-equivalent amounts and equivalent no-plan/no-date sentinels."
    ),
    "unit_tests": "293 passed, 1 skipped (pre-Task-1 suite)",
    "G0": "PASS 15/15 checks",
    "G1": "FAIL 5 status upgrades, 16 safe overclaims, 0 unparsed (25 rows)",
    "G2_full_row_matches": "3/25",
    "G2_per_field_correct": {
        "amount_safe_to_pay": 3,
        "affordability_status": 17,
        "recommended_payment_method": 20,
        "payment_plan": 18,
        "earliest_date_for_full_payment": 10,
        "spending_changes_needed": 21,
    },
    "G2_spending": (
        "rows TP 0 / FP 1 / FN 3 of 3 changed; "
        "actions TP 0 / FP 1 / FN 4"
    ),
    "G3": "PASS 2 clean runs, machine columns byte-identical",
    "G4": "PASS output 42b3fe53a609, usage 20f449802e05",
    "G5_G7": "NOT_APPLICABLE (later-task artifacts)",
}


def repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def make_gate(gate_id: str, name: str, required: bool, status: str, detail: str, checks=None) -> dict:
    return {
        "id": gate_id,
        "name": name,
        "required": bool(required),
        "status": status,
        "detail": detail,
        "checks": list(checks or []),
    }


def overall_status(gates) -> tuple[str, int]:
    """Return ``(status, exit_code)``; required N/A gates are INCOMPLETE (exit 2)."""
    required = [gate for gate in gates if gate.get("required", True)]
    if any(gate.get("status") == FAIL for gate in required):
        return FAIL, 1
    if any(gate.get("status") not in (PASS, FAIL) for gate in required):
        return "INCOMPLETE", 2
    return PASS, 0


def _request_id(row: dict) -> str:
    return str(row.get("request_id") or "").strip()


def _amount_text(value) -> str:
    amount = semantics.parse_amount(value)
    return "" if amount is None else format(amount, "f")


def safety_metrics(actual_rows, expected_rows) -> dict:
    """Raw G1 counts: status upgrades and safe-amount overclaims versus gold."""
    actual = semantics.as_records(actual_rows)
    expected = semantics.as_records(expected_rows)
    actual_by_id: dict[str, dict] = {}
    for row in actual:
        actual_by_id.setdefault(_request_id(row), row)

    status_upgrades: list[dict] = []
    safe_overclaims: list[dict] = []
    unparsed: list[dict] = []
    compared = 0

    for expected_row in expected:
        request_id = _request_id(expected_row)
        actual_row = actual_by_id.get(request_id)
        if actual_row is None:
            continue
        compared += 1

        actual_status = actual_row.get("affordability_status")
        expected_status = expected_row.get("affordability_status")
        if semantics.status_upgrade(actual_status, expected_status):
            status_upgrades.append(
                {
                    "request_id": request_id,
                    "expected": str(expected_status),
                    "actual": str(actual_status),
                }
            )

        expected_amount = semantics.parse_amount(expected_row.get("amount_safe_to_pay"))
        actual_amount = semantics.parse_amount(actual_row.get("amount_safe_to_pay"))
        if expected_amount is not None and actual_amount is None:
            unparsed.append(
                {
                    "request_id": request_id,
                    "expected": _amount_text(expected_row.get("amount_safe_to_pay")),
                    "actual": str(actual_row.get("amount_safe_to_pay")),
                }
            )
            continue
        if semantics.safe_overclaim(actual_amount, expected_amount):
            band = max(abs(expected_amount) * Decimal("0.01"), Decimal("1"))
            safe_overclaims.append(
                {
                    "request_id": request_id,
                    "expected": _amount_text(expected_row.get("amount_safe_to_pay")),
                    "actual": _amount_text(actual_row.get("amount_safe_to_pay")),
                    "allowed_max": format(expected_amount + band, "f"),
                }
            )

    return {
        "rows_compared": compared,
        "status_upgrades": len(status_upgrades),
        "status_upgrade_rows": status_upgrades,
        "safe_overclaims": len(safe_overclaims),
        "safe_overclaim_rows": safe_overclaims,
        "unparsed_amounts": len(unparsed),
        "unparsed_amount_rows": unparsed,
        "rule": "overclaim when actual > expected + max(abs(expected)*0.01, 1)",
    }


def safety_gate(metrics: dict) -> dict:
    ok = (
        metrics.get("rows_compared", 0) > 0
        and metrics.get("status_upgrades", 0) == 0
        and metrics.get("safe_overclaims", 0) == 0
        and metrics.get("unparsed_amounts", 0) == 0
    )
    detail = (
        f"{metrics.get('status_upgrades', 0)} status upgrades, "
        f"{metrics.get('safe_overclaims', 0)} safe overclaims, "
        f"{metrics.get('unparsed_amounts', 0)} unparsed amounts across "
        f"{metrics.get('rows_compared', 0)} rows (require 0/0/0)"
    )
    return make_gate("G1", "safety", True, PASS if ok else FAIL, detail)


def sample_quality_gate(metrics: dict, thresholds: dict | None = None) -> dict:
    targets = {
        "full_row_matches": SAMPLE_THRESHOLDS["full_row_matches"],
        "per_field": dict(SAMPLE_THRESHOLDS["per_field"]),
        "spending_recall": SAMPLE_THRESHOLDS["spending_recall"],
        "spending_precision": SAMPLE_THRESHOLDS["spending_precision"],
    }
    if thresholds:
        targets.update(thresholds)

    total = metrics.get("total", 0)
    full_row_matches = metrics.get("full_row_matches", 0)
    per_field = metrics.get("per_field_correct", {})
    spending = metrics.get("spending_changes", {})
    problems: list[str] = []

    if total <= 0:
        problems.append("no reference rows compared")
    if full_row_matches < targets["full_row_matches"]:
        problems.append(
            f"full_row_matches {full_row_matches} < {targets['full_row_matches']}"
        )
    for field, minimum in targets["per_field"].items():
        correct = per_field.get(field, 0)
        if correct < minimum:
            problems.append(f"{field} {correct} < {minimum}")
    if metrics.get("missing_request_ids"):
        problems.append(f"missing_request_ids {len(metrics['missing_request_ids'])}")
    if metrics.get("unexpected_request_ids"):
        problems.append(f"unexpected_request_ids {len(metrics['unexpected_request_ids'])}")
    if metrics.get("duplicate_request_ids"):
        problems.append(f"duplicate_request_ids {len(metrics['duplicate_request_ids'])}")
    if spending.get("parse_errors"):
        problems.append(f"spending_change_parse_errors {len(spending['parse_errors'])}")

    gold_positives = spending.get("gold_positives", 0)
    true_positives = spending.get("true_positives", 0)
    false_positives = spending.get("false_positives", 0)
    precision = spending.get("precision")
    if gold_positives:
        numerator, denominator = targets["spending_recall"]
        needed = (gold_positives * numerator + denominator - 1) // denominator
        if true_positives < needed:
            problems.append(
                f"spending true_positives {true_positives} < {needed} of {gold_positives}"
            )
    if false_positives and (precision is None or precision < targets["spending_precision"]):
        problems.append(
            f"spending precision {precision} < {targets['spending_precision']} with "
            f"{false_positives} false positives"
        )

    gold_changed_rows = spending.get("rows_with_changes_gold", 0)
    row_true_positives = spending.get("row_true_positives", 0)
    row_false_positives = spending.get("row_false_positives", 0)
    row_precision = spending.get("row_precision")
    if gold_changed_rows:
        numerator, denominator = targets["spending_recall"]
        needed = (gold_changed_rows * numerator + denominator - 1) // denominator
        if row_true_positives < needed:
            problems.append(
                f"spending row_true_positives {row_true_positives} < {needed} of "
                f"{gold_changed_rows} changed rows"
            )
    if row_false_positives and (
        row_precision is None or row_precision < targets["spending_precision"]
    ):
        problems.append(
            f"spending row_precision {row_precision} < {targets['spending_precision']} "
            f"with {row_false_positives} false-positive rows"
        )

    summary = (
        f"full-row {full_row_matches}/{total}; "
        + ", ".join(
            f"{field} {per_field.get(field, 0)}/{total}"
            for field in semantics.MACHINE_FIELDS
        )
        + f"; spending rows TP {row_true_positives}/FP {row_false_positives}/"
        f"FN {spending.get('row_false_negatives', 0)} of {gold_changed_rows} changed; "
        f"actions TP {true_positives}/FP {false_positives}/"
        f"FN {spending.get('false_negatives', 0)}"
    )
    detail = summary if not problems else "; ".join(problems) + " | " + summary
    return make_gate("G2", "sample_quality", True, PASS if not problems else FAIL, detail)


def _machine_bytes(frame) -> bytes:
    columns = ["request_id", *semantics.MACHINE_FIELDS]
    return frame.loc[:, columns].to_csv(index=False, lineterminator="\n").encode("utf-8")


def determinism_metrics(run_a, run_b) -> dict:
    """Compare two clean sample runs byte-for-byte over machine columns."""
    try:
        left, right = _machine_bytes(run_a), _machine_bytes(run_b)
    except Exception as exc:  # missing columns is a pipeline defect, not a skip
        return {
            "scope": "dataset/sample_requests.csv",
            "runs": 2,
            "rows": 0,
            "identical": False,
            "sha256_run_a": None,
            "sha256_run_b": None,
            "differences": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    differences: list[dict] = []
    if left != right:
        left_rows = {_request_id(row): row for row in semantics.as_records(run_a)}
        right_rows = {_request_id(row): row for row in semantics.as_records(run_b)}
        for request_id in sorted(set(left_rows) | set(right_rows)):
            left_row = left_rows.get(request_id, {})
            right_row = right_rows.get(request_id, {})
            for field in semantics.MACHINE_FIELDS:
                left_value = str(left_row.get(field))
                right_value = str(right_row.get(field))
                if left_value != right_value:
                    differences.append(
                        {
                            "request_id": request_id,
                            "field": field,
                            "run_a": left_value,
                            "run_b": right_value,
                        }
                    )

    return {
        "scope": "dataset/sample_requests.csv",
        "runs": 2,
        "rows": len(run_a),
        "identical": left == right and len(left) > 0,
        "sha256_run_a": hashlib.sha256(left).hexdigest(),
        "sha256_run_b": hashlib.sha256(right).hexdigest(),
        "differences": differences[:20],
    }


def determinism_gate(metrics: dict) -> dict:
    ok = bool(metrics.get("identical")) and metrics.get("rows", 0) > 0
    detail = (
        f"{metrics.get('runs', 0)} clean runs over {metrics.get('rows', 0)} sample rows; "
        + ("machine columns byte-identical" if ok else "machine columns differ")
    )
    if metrics.get("error"):
        detail += f" ({metrics['error']})"
    return make_gate("G3", "determinism", True, PASS if ok else FAIL, detail)


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


USAGE_REPORT_FIELDS = (
    "provider",
    "model",
    "model calls",
    "input tokens",
    "output tokens",
    "total tokens",
    "average tokens per request",
    "estimated total cost",
    "estimated cost per request",
)


def usage_report_fields_present(text: str) -> bool:
    lowered = text.lower()
    return all(field in lowered for field in USAGE_REPORT_FIELDS)


def provenance_metrics(root: Path) -> dict:
    output_path = root / "output.csv"
    usage_path = root / "evaluation" / "usage_report.md"
    mirror_path = root / "tools" / "evaluation" / "usage_report.md"
    output_sha = sha256_file(output_path)
    usage_sha = sha256_file(usage_path)
    mirror_sha = sha256_file(mirror_path)
    fields_present = False
    if usage_sha is not None:
        fields_present = usage_report_fields_present(
            usage_path.read_text(encoding="utf-8", errors="ignore")
        )
    return {
        "commit_sha": git_commit(root),
        "output_path": "output.csv",
        "output_sha256": output_sha,
        "usage_report_path": "evaluation/usage_report.md",
        "usage_report_sha256": usage_sha,
        "usage_report_mirror_path": "tools/evaluation/usage_report.md",
        "usage_report_mirror_sha256": mirror_sha,
        "usage_report_mirrors_match": (
            usage_sha is not None and usage_sha == mirror_sha
        ),
        "usage_report_fields_present": fields_present,
    }


def provenance_gate(metrics: dict) -> dict:
    problems = []
    if not metrics.get("commit_sha"):
        problems.append("git commit unavailable")
    if not metrics.get("output_sha256"):
        problems.append("output.csv missing")
    if not metrics.get("usage_report_sha256"):
        problems.append("evaluation/usage_report.md missing")
    if not metrics.get("usage_report_mirror_sha256"):
        problems.append("tools/evaluation/usage_report.md missing")
    if not metrics.get("usage_report_mirrors_match"):
        problems.append("usage-report mirrors differ")
    if not metrics.get("usage_report_fields_present"):
        problems.append("usage report lacks AGENTS 6.5 fields")
    detail = (
        f"commit {str(metrics.get('commit_sha'))[:12]}; "
        f"output {str(metrics.get('output_sha256'))[:12]}; "
        f"usage {str(metrics.get('usage_report_sha256'))[:12]}"
        if not problems
        else "; ".join(problems)
    )
    return make_gate("G4", "provenance", True, PASS if not problems else FAIL, detail)


def agent_robustness_gate(root: Path) -> dict:
    """G7: a crashing agent must change no row and must not crash the run.

    The loop is deliberately broken for the first two sample requests. The gate
    passes only when the pipeline still completes, every machine field matches the
    deterministic baseline, and each crash is recorded instead of swallowed.
    """
    from code.main import run_pipeline

    sample = fixture_dir(root) / "sample_requests.csv"
    checks: list[dict] = []

    baseline, _ = run_pipeline(
        repo_root=root, requests_path=sample, write_output=False, write_usage=False
    )

    invoked: list[str] = []

    def explode(context, **_kwargs):
        invoked.append(str(context.get("request_id")))
        raise RuntimeError("gate: deliberate agent failure")

    crashed, usage = None, {}
    stderr_text = ""
    try:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            crashed, usage = run_pipeline(
                repo_root=root,
                requests_path=sample,
                write_output=False,
                write_usage=False,
                agent_limit=2,
                agent=explode,
            )
        stderr_text = buffer.getvalue()
    except Exception as exc:  # a gate must report, never explode
        stderr_text = f"gate harness error: {type(exc).__name__}: {exc}"
    errors = [item for item in (usage.get("agent_loop_errors") or []) if isinstance(item, dict)]

    checks.append({
        "name": "pipeline survives a crashing agent",
        "status": PASS if crashed is not None and len(crashed) == len(baseline) else FAIL,
        "detail": f"{len(crashed) if crashed is not None else 0} rows produced",
    })
    identical = crashed is not None and baseline.equals(crashed)
    checks.append({
        "name": "machine fields identical to the kernel",
        "status": PASS if identical else FAIL,
        "detail": "row-for-row identical" if identical else "a crashed agent changed a row",
    })
    attempted = len(invoked) == 2
    checks.append({
        "name": "loop invoked for the requested requests",
        "status": PASS if attempted else FAIL,
        "detail": f"invoked {len(invoked)}: {', '.join(invoked) or 'none'}",
    })
    recorded = len(errors) == len(invoked) and all(item.get("error") for item in errors)
    checks.append({
        "name": "crashes recorded, not swallowed",
        "status": PASS if recorded else FAIL,
        "detail": f"{len(errors)} recorded error(s)",
    })
    announced = all(rid in stderr_text for rid in invoked) if invoked else False
    checks.append({
        "name": "crash announced on stderr",
        "status": PASS if announced else FAIL,
        "detail": "each crashed request named on stderr" if announced else "no stderr line for a crashed request",
    })

    failed = [check["name"] for check in checks if check["status"] == FAIL]
    detail = (
        f"{len(checks)} checks passed: crashed agent changed nothing"
        if not failed
        else f"failed: {', '.join(failed)}"
    )
    return make_gate("G7", "agent_robustness", True, PASS if not failed else FAIL, detail, checks)


def release_gate_slots(archive_path=None) -> list[dict]:
    if archive_path is None:
        archive_detail = "no --archive supplied"
    else:
        path = Path(archive_path)
        if not path.is_file():
            archive_detail = f"archive not found: {path.name}"
        else:
            archive_detail = (
                f"archive present: {path.name} sha256 {str(sha256_file(path))[:12]}; "
                "clean-room execution is deferred (no container build in this environment)"
            )
    return [
        make_gate(
            "G5",
            "archive",
            True,
            NOT_APPLICABLE,
            archive_detail,
        ),
        make_gate(
            "G6",
            "security",
            True,
            NOT_APPLICABLE,
            "tree secret scan runs in G0; archive/history scanning needs an --archive",
        ),
        make_gate(
            "G7",
            "agent_robustness",
            True,
            NOT_APPLICABLE,
            "replaced by the live robustness gate in release mode",
        ),
    ]


def assemble_report(mode: str, gates, metrics: dict, provenance: dict) -> dict:
    machine = {"gates": list(gates), "metrics": metrics}
    canonical = json.dumps(
        machine,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    status, exit_code = overall_status(gates)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "evaluation.run",
        "mode": mode,
        "overall_status": status,
        "exit_code": exit_code,
        "machine_sha256": fingerprint,
        "gates": list(gates),
        "metrics": metrics,
        "provenance": provenance,
    }


@contextlib.contextmanager
def replay_environment(provider: str):
    """Guarantee no live LLM call when the harness requests replay mode."""
    if provider != "replay":
        yield
        return
    saved = {key: os.environ.get(key) for key in LLM_ENV_KEYS}
    for key in LLM_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _empty_spending_metrics() -> dict:
    return {
        "gold_positives": 0,
        "predicted_positives": 0,
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "precision": None,
        "recall": None,
        "rows_with_changes_gold": 0,
        "rows_with_changes_actual": 0,
        "rows_change_set_exact": 0,
        "row_true_positives": 0,
        "row_false_positives": 0,
        "row_false_negatives": 0,
        "row_precision": None,
        "row_recall": None,
        "parse_errors": [],
    }


def _empty_sample_metrics(error: str) -> dict:
    return {
        "total": 0,
        "compared": 0,
        "full_row_matches": 0,
        "per_field_correct": {field: 0 for field in semantics.MACHINE_FIELDS},
        "missing_request_ids": [],
        "unexpected_request_ids": [],
        "duplicate_request_ids": [],
        "mismatches": [],
        "spending_changes": _empty_spending_metrics(),
        "error": error,
    }


def run_structural_checks(root: Path) -> tuple[dict, dict]:
    """Run tools/correctness_report.py exactly as a human would; parse its lines."""
    command = [sys.executable, str(root / "tools" / "correctness_report.py")]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        metrics = {
            "checks_total": 0,
            "checks_passed": 0,
            "checks_failed": 1,
            "failed_checks": ["correctness_report"],
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
        gate = make_gate(
            "G0", "structural", True, FAIL, f"correctness_report could not run: {metrics['error']}"
        )
        return gate, metrics

    checks: list[dict] = []
    for line in result.stdout.splitlines():
        if line.startswith("PASS ") or line.startswith("FAIL "):
            status, rest = line.split(" ", 1)
            name, _, detail = rest.partition(": ")
            checks.append({"name": name, "status": status, "detail": detail})

    failed = [check["name"] for check in checks if check["status"] == FAIL]
    parsed_ok = bool(checks) and not failed and result.returncode == 0
    metrics = {
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "checks_failed": len(failed),
        "failed_checks": failed,
        "returncode": result.returncode,
    }
    if result.returncode != 0 and not failed:
        metrics["stdout_tail"] = result.stdout[-1000:]
        metrics["stderr_tail"] = result.stderr[-1000:]
    detail = (
        f"{metrics['checks_passed']}/{metrics['checks_total']} checks passed"
        if parsed_ok
        else (
            f"exit {result.returncode}; failed: {', '.join(failed)}"
            if failed
            else f"exit {result.returncode}; unparseable correctness output"
        )
    )
    gate = make_gate("G0", "structural", True, PASS if parsed_ok else FAIL, detail, checks)
    return gate, metrics


def _pr_release_slots() -> list[dict]:
    slots = release_gate_slots(archive_path=None)
    reasons = {
        "G5": "archive clean-room checks need an --archive",
        "G6": "archive/history scanning needs an --archive; tree secret scan runs in G0",
        "G7": "the live agent-robustness gate runs in release mode",
    }
    for slot in slots:
        slot["required"] = False
        slot["detail"] = f"release-only gate: {reasons[slot['id']]}; not required in PR mode"
    return slots


def _exception_text(exc: Exception, limit: int = 300) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text[:limit]


def run_pr(root: Path, provider: str = "replay") -> dict:
    """Execute the PR gate and return the assembled report dict."""
    started = time.time()
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    root = Path(root)

    from code.main import run_pipeline

    sample_path = fixture_dir(root) / "sample_requests.csv"
    production_error = None
    sample_error = None
    production_usage: dict = {}
    sample_a = sample_b = None

    with replay_environment(provider):
        try:
            _, production_usage = run_pipeline(
                repo_root=root,
                write_output=False,
                write_usage=False,
            )
        except Exception as exc:  # fail closed: the report records the crash
            production_error = _exception_text(exc)
        if production_error is None:
            try:
                sample_a, _ = run_pipeline(
                    repo_root=root,
                    requests_path=sample_path,
                    write_output=False,
                    write_usage=False,
                )
                sample_b, _ = run_pipeline(
                    repo_root=root,
                    requests_path=sample_path,
                    write_output=False,
                    write_usage=False,
                )
            except Exception as exc:
                sample_error = _exception_text(exc)

    structural_gate, structural_metrics = run_structural_checks(root)

    import pandas as pd

    if sample_error is None:
        gold = pd.read_csv(sample_path, dtype=str, keep_default_na=False)
        safety = safety_metrics(sample_a, gold)
        safety_gate_result = safety_gate(safety)
        sample_metrics = semantics.compare_rows(sample_a, gold)
        sample_gate_result = sample_quality_gate(sample_metrics)
        determinism = determinism_metrics(sample_a, sample_b)
        determinism_gate_result = determinism_gate(determinism)
    else:
        safety = safety_metrics([], [])
        safety_gate_result = make_gate(
            "G1", "safety", True, FAIL, f"sample pipeline crashed: {sample_error}"
        )
        sample_metrics = _empty_sample_metrics(sample_error)
        sample_gate_result = make_gate(
            "G2", "sample_quality", True, FAIL, f"sample pipeline crashed: {sample_error}"
        )
        determinism = {
            "scope": "dataset/sample_requests.csv",
            "runs": 0,
            "rows": 0,
            "identical": False,
            "sha256_run_a": None,
            "sha256_run_b": None,
            "differences": [],
            "error": sample_error,
        }
        determinism_gate_result = make_gate(
            "G3", "determinism", True, FAIL, f"sample pipeline crashed: {sample_error}"
        )

    if production_error is not None and not sample_error:
        # The samples still ran, but the deliverable itself is not trustworthy.
        structural_gate = make_gate(
            "G0",
            "structural",
            True,
            FAIL,
            f"production pipeline crashed: {production_error}",
            structural_gate["checks"],
        )

    provenance_machine = provenance_metrics(root)
    provenance_gate_result = provenance_gate(provenance_machine)
    if production_error is not None:
        provenance_gate_result = make_gate(
            "G4",
            "provenance",
            True,
            FAIL,
            f"production pipeline crashed: {production_error}",
        )

    gates = [
        structural_gate,
        safety_gate_result,
        sample_gate_result,
        determinism_gate_result,
        provenance_gate_result,
        *_pr_release_slots(),
    ]
    metrics = {
        "structural": structural_metrics,
        "safety": safety,
        "sample_quality": sample_metrics,
        "determinism": determinism,
        "provenance": provenance_machine,
    }

    usage = production_usage or {}
    cost_status = usage.get("cost_status")
    provenance = {
        "generated_at": generated_at,
        "duration_seconds": round(time.time() - started, 3),
        "commit_sha": provenance_machine.get("commit_sha"),
        "output_path": "output.csv",
        "output_sha256": provenance_machine.get("output_sha256"),
        "usage_report_path": "evaluation/usage_report.md",
        "usage_report_sha256": provenance_machine.get("usage_report_sha256"),
        "usage_report_mirror_path": "tools/evaluation/usage_report.md",
        "usage_report_mirror_sha256": provenance_machine.get("usage_report_mirror_sha256"),
        "provider": usage.get("provider", "none"),
        "model": usage.get("model", "none"),
        "model_calls": int(usage.get("calls", 0)),
        "successful_calls": int(usage.get("successful", 0)),
        "failed_calls": int(usage.get("failed", 0)),
        "fallback_explanations": int(usage.get("fallbacks", 0)),
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "estimated_cost_usd": (
            usage.get("estimated_cost_usd") if cost_status == "estimated" else None
        ),
        "cost_status": cost_status or "unavailable",
        "pipeline_runs": 1 + (0 if sample_error else 2),
        "python_version": sys.version.split()[0],
        "provider_mode": provider,
    }
    if production_error:
        provenance["production_error"] = production_error
    if sample_error:
        provenance["sample_error"] = sample_error

    return assemble_report(
        mode="pr",
        gates=gates,
        metrics=metrics,
        provenance=provenance,
    )


def _status_cell(status: str) -> str:
    return status


def _table(headers, rows) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render_markdown(report: dict) -> str:
    lines: list[str] = ["# Buy or Wait? evaluation report", ""]
    lines.append(f"- Mode: `{report.get('mode')}`")
    lines.append(f"- Overall: **{report.get('overall_status')}** (exit {report.get('exit_code')})")
    lines.append(f"- Machine fingerprint: `sha256:{report.get('machine_sha256')}`")
    provenance = report.get("provenance", {})
    lines.append(f"- Generated: {provenance.get('generated_at')}")
    lines.append(f"- Commit: `{provenance.get('commit_sha')}`")
    lines.append(f"- Output SHA256: `{provenance.get('output_sha256')}`")
    lines.append(f"- Usage report: `{provenance.get('usage_report_path')}`")
    lines.append(f"- Usage report SHA256: `{provenance.get('usage_report_sha256')}`")
    lines.append("")

    lines.append("## Gates")
    lines.append("")
    lines.extend(
        _table(
            ("ID", "Gate", "Required", "Status", "Detail"),
            [
                (
                    gate.get("id"),
                    gate.get("name"),
                    "yes" if gate.get("required") else "no",
                    _status_cell(gate.get("status")),
                    gate.get("detail"),
                )
                for gate in report.get("gates", [])
            ],
        )
    )
    lines.append("")

    metrics = report.get("metrics", {})
    structural = metrics.get("structural", {})
    lines.append("## G0 structural")
    lines.append("")
    lines.append(
        f"Correctness report: {structural.get('checks_passed', 0)}/"
        f"{structural.get('checks_total', 0)} checks passed "
        f"(exit {structural.get('returncode')})."
    )
    lines.append("")
    structural_checks = []
    for gate in report.get("gates", []):
        if gate.get("id") == "G0":
            structural_checks = gate.get("checks", [])
    if structural_checks:
        lines.extend(
            _table(
                ("Check", "Status", "Detail"),
                [
                    (check.get("name"), check.get("status"), check.get("detail"))
                    for check in structural_checks
                ],
            )
        )
        lines.append("")

    safety = metrics.get("safety", {})
    lines.append("## G1 safety")
    lines.append("")
    lines.append(
        f"- Rows compared: {safety.get('rows_compared', 0)}\n"
        f"- Status upgrades above gold: **{safety.get('status_upgrades', 0)}**\n"
        f"- Safe-amount overclaims: **{safety.get('safe_overclaims', 0)}**\n"
        f"- Unparsed predicted amounts: {safety.get('unparsed_amounts', 0)}\n"
        f"- Rule: {safety.get('rule', '')}"
    )
    lines.append("")
    for title, key in (
        ("Status upgrades", "status_upgrade_rows"),
        ("Safe-amount overclaims", "safe_overclaim_rows"),
    ):
        rows = safety.get(key, [])
        if rows:
            lines.append(f"### {title}")
            lines.append("")
            lines.extend(
                _table(
                    ("Request", "Gold", "Actual", "Allowed max"),
                    [
                        (
                            row.get("request_id"),
                            row.get("expected"),
                            row.get("actual"),
                            row.get("allowed_max", ""),
                        )
                        for row in rows
                    ],
                )
            )
            lines.append("")

    sample = metrics.get("sample_quality", {})
    lines.append("## G2 sample quality")
    lines.append("")
    total = sample.get("total", 0)
    lines.append(f"- Reference rows: {total}")
    lines.append(f"- Full-row semantic matches: **{sample.get('full_row_matches', 0)}/{total}**")
    lines.append(
        f"- Missing request IDs: {len(sample.get('missing_request_ids', []))}; "
        f"unexpected: {len(sample.get('unexpected_request_ids', []))}; "
        f"duplicates: {len(sample.get('duplicate_request_ids', []))}"
    )
    lines.append("")
    lines.extend(
        _table(
            ("Field", "Correct", "Total", "Target"),
            [
                (
                    field,
                    sample.get("per_field_correct", {}).get(field, 0),
                    total,
                    SAMPLE_THRESHOLDS["per_field"].get(field, "-"),
                )
                for field in semantics.MACHINE_FIELDS
            ],
        )
    )
    lines.append("")
    spending = sample.get("spending_changes", {})
    lines.append(
        "Spending changes (rows): "
        f"TP {spending.get('row_true_positives', 0)}, "
        f"FP {spending.get('row_false_positives', 0)}, "
        f"FN {spending.get('row_false_negatives', 0)}, "
        f"precision {spending.get('row_precision')}, recall {spending.get('row_recall')}; "
        f"gold changed rows {spending.get('rows_with_changes_gold', 0)}, "
        f"predicted changed rows {spending.get('rows_with_changes_actual', 0)}."
    )
    lines.append(
        "Spending changes (actions): "
        f"TP {spending.get('true_positives', 0)}, "
        f"FP {spending.get('false_positives', 0)}, "
        f"FN {spending.get('false_negatives', 0)}, "
        f"precision {spending.get('precision')}, recall {spending.get('recall')}; "
        f"gold actions {spending.get('gold_positives', 0)}, "
        f"predicted actions {spending.get('predicted_positives', 0)}."
    )
    lines.append("")
    mismatches = sample.get("mismatches", [])
    if mismatches:
        preview = mismatches[:200]
        lines.append(f"### Mismatches ({len(mismatches)}; showing {len(preview)})")
        lines.append("")
        lines.extend(
            _table(
                ("Request", "Field", "Gold", "Actual"),
                [
                    (
                        row.get("request_id"),
                        row.get("field"),
                        row.get("expected"),
                        row.get("actual"),
                    )
                    for row in preview
                ],
            )
        )
        lines.append("")
    if sample.get("error"):
        lines.append(f"Sample error: {sample['error']}")
        lines.append("")

    determinism = metrics.get("determinism", {})
    lines.append("## G3 determinism")
    lines.append("")
    lines.append(
        f"- Scope: `{determinism.get('scope')}`; runs: {determinism.get('runs', 0)}; "
        f"rows: {determinism.get('rows', 0)}\n"
        f"- Machine columns identical: **{determinism.get('identical')}**\n"
        f"- Run A SHA256: `{determinism.get('sha256_run_a')}`\n"
        f"- Run B SHA256: `{determinism.get('sha256_run_b')}`"
    )
    differences = determinism.get("differences", [])
    if differences:
        lines.append("")
        lines.extend(
            _table(
                ("Request", "Field", "Run A", "Run B"),
                [
                    (
                        row.get("request_id"),
                        row.get("field"),
                        row.get("run_a"),
                        row.get("run_b"),
                    )
                    for row in differences
                ],
            )
        )
    lines.append("")

    provenance_metrics = metrics.get("provenance", {})
    lines.append("## G4 provenance")
    lines.append("")
    lines.append(
        f"- Commit: `{provenance_metrics.get('commit_sha')}`\n"
        f"- output.csv SHA256: `{provenance_metrics.get('output_sha256')}`\n"
        f"- evaluation/usage_report.md SHA256: `{provenance_metrics.get('usage_report_sha256')}`\n"
        f"- tools/evaluation/usage_report.md SHA256: `{provenance_metrics.get('usage_report_mirror_sha256')}`\n"
        f"- Mirrors match: {provenance_metrics.get('usage_report_mirrors_match')}\n"
        f"- AGENTS 6.5 fields present: {provenance_metrics.get('usage_report_fields_present')}\n"
        f"- Provider: {provenance.get('provider')} / {provenance.get('model')}; "
        f"calls {provenance.get('model_calls')}; "
        f"tokens {provenance.get('total_tokens')}; "
        f"cost status {provenance.get('cost_status')}"
    )
    lines.append("")

    lines.append("## Later-task gates")
    lines.append("")
    for gate in report.get("gates", []):
        if gate.get("id") in {"G5", "G6", "G7"}:
            lines.append(
                f"- **{gate.get('id')} {gate.get('name')}**: {gate.get('status')} - "
                f"{gate.get('detail')}"
            )
    lines.append("")

    lines.append(f"## Baseline (pre-fix, `{PRE_FIX_BASELINE['commit']}`)")
    lines.append("")
    lines.append(f"- Captured: {PRE_FIX_BASELINE['captured_on']}")
    lines.append(f"- Note: {PRE_FIX_BASELINE['note']}")
    lines.append(f"- Unit tests: {PRE_FIX_BASELINE['unit_tests']}")
    for gate_id in ("G0", "G1", "G3", "G4", "G5_G7"):
        lines.append(f"- {gate_id}: {PRE_FIX_BASELINE[gate_id]}")
    lines.append(f"- G2 full-row matches: {PRE_FIX_BASELINE['G2_full_row_matches']}")
    lines.append(f"- G2 spending: {PRE_FIX_BASELINE['G2_spending']}")
    lines.append("")
    lines.extend(
        _table(
            ("Field", "Correct (pre-fix)"),
            [
                (field, PRE_FIX_BASELINE["G2_per_field_correct"].get(field, "-"))
                for field in semantics.MACHINE_FIELDS
            ],
        )
    )
    lines.append("")

    lines.append("## Reproduce")
    lines.append("")
    lines.append("```text")
    lines.append("python -m evaluation.run --mode pr")
    lines.append("python -m evaluation.run --mode release --provider replay --archive code.zip")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "report.json"
    markdown_path = report_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, markdown_path


def run_release(root: Path, archive_path=None, provider: str = "replay") -> dict:
    report = run_pr(root, provider=provider)
    release_slots = release_gate_slots(archive_path=archive_path)
    # G7 is real in release mode: break the loop on purpose and prove nothing moves.
    robustness = agent_robustness_gate(Path(root))
    release_slots = [
        robustness if gate.get("id") == "G7" else gate for gate in release_slots
    ]
    gates = [
        gate for gate in report["gates"] if gate.get("id") not in {"G5", "G6", "G7"}
    ] + release_slots
    report = assemble_report(
        mode="release",
        gates=gates,
        metrics=report["metrics"],
        provenance=report["provenance"],
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Buy or Wait? evaluation harness against the real production pipeline. "
            "PR mode regenerates output.csv, then scores the 25 public sample rows "
            "semantically and checks safety, determinism and provenance."
        )
    )
    parser.add_argument("--mode", choices=("pr", "release"), default="pr")
    parser.add_argument(
        "--provider",
        choices=("replay", "auto"),
        default="replay",
        help="replay (default) disables live LLM env vars; auto leaves them untouched",
    )
    parser.add_argument("--archive", default=None, help="release-mode archive path")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--report-dir", default=None)
    args = parser.parse_args(argv)

    if args.mode == "release" and args.provider != "replay":
        parser.error("release mode requires --provider replay (no live provider calls)")

    root = Path(args.repo_root).resolve() if args.repo_root else repo_root_default()
    report_dir = Path(args.report_dir).resolve() if args.report_dir else root / "evaluation"

    if args.mode == "release":
        report = run_release(root, archive_path=args.archive, provider=args.provider)
    else:
        report = run_pr(root, provider=args.provider)

    json_path, markdown_path = write_reports(report, report_dir)

    print(f"== Buy or Wait? evaluation ({report['mode']}) ==")
    for gate in report["gates"]:
        required = "required" if gate.get("required") else "release-only"
        print(f"{gate['status']:<14} {gate['id']} {gate['name']:<18} [{required}] {gate['detail']}")
    print(f"OVERALL: {report['overall_status']} (exit {report['exit_code']})")
    print(f"Reports: {json_path.name}, {markdown_path.name} in {report_dir}")
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
