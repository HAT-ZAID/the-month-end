"""Core tests for the Task 1 evaluation harness.

Focused coverage only: semantic normalization, one unsafe and one safe G1 row,
release-mode ``NOT_APPLICABLE`` slots, and stable report emission. Broader
edge cases belong to the final verification pass near project completion.
"""
from __future__ import annotations

import csv

import pytest

from evaluation import semantics
from evaluation import run as evalrun


def _row(**overrides):
    base = {
        "request_id": "request_x",
        "amount_safe_to_pay": "100",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2024-01-01:100",
        "earliest_date_for_full_payment": "2024-01-01",
        "spending_changes_needed": "none",
    }
    base.update(overrides)
    return base


def test_semantic_normalization_of_machine_fields():
    # Amounts are cent-tolerant; more than one cent is a mismatch.
    assert semantics.amounts_equal("100.01", "100")
    assert not semantics.amounts_equal("100.02", "100")

    # Status and method vocabulary must match exactly.
    assert semantics.statuses_equal("affordable_now", "affordable_now")
    assert not semantics.statuses_equal("affordable_now", "affordable_with_plan")

    # Plans compare parsed legs in order, with cent-tolerant amounts.
    assert semantics.plans_equal(
        "2024-01-01:100.00|2024-02-01:50.004",
        "2024-01-01:100|2024-02-01:50",
    )
    assert not semantics.plans_equal(
        "2024-02-01:50|2024-01-01:100",
        "2024-01-01:100|2024-02-01:50",
    )
    assert semantics.plans_equal("none", None)

    # Blank/NaN earliest dates normalize to "no date"; real dates are exact.
    assert semantics.dates_equal("", float("nan"))
    assert semantics.dates_equal("2024-03-03", "2024-03-03")
    assert not semantics.dates_equal("2024-03-03", "")

    # Spending changes compare as a canonical unordered set.
    assert semantics.spending_changes_equal(
        "stop:event_b|reduce_to:event_a:23.50",
        "reduce_to:event_a:23.504|stop:event_b",
    )


def test_unsafe_synthetic_row_fails_g1():
    reference = [
        _row(
            request_id="synth_bad",
            amount_safe_to_pay="10",
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment="",
        )
    ]
    actual = [
        _row(
            request_id="synth_bad",
            amount_safe_to_pay="500",
            affordability_status="affordable_now",
            recommended_payment_method="full_payment",
            payment_plan="2024-01-01:500",
        )
    ]

    metrics = evalrun.safety_metrics(actual, reference)
    assert metrics["status_upgrades"] == 1
    assert metrics["safe_overclaims"] == 1
    gate = evalrun.safety_gate(metrics)
    assert gate["status"] == "FAIL"
    assert gate["required"] is True


def test_safe_synthetic_row_passes_g1():
    reference = [_row(request_id="synth_safe")]
    actual = [_row(request_id="synth_safe")]

    metrics = evalrun.safety_metrics(actual, reference)
    assert metrics["rows_compared"] == 1
    assert metrics["status_upgrades"] == 0
    assert metrics["safe_overclaims"] == 0
    assert evalrun.safety_gate(metrics)["status"] == "PASS"
    assert semantics.row_matches(actual[0], reference[0])


def test_release_mode_gates_are_not_applicable():
    gates = evalrun.release_gate_slots(archive_path=None)
    assert [gate["id"] for gate in gates] == ["G5", "G6", "G7"]
    assert all(gate["status"] == "NOT_APPLICABLE" for gate in gates)
    assert all(gate["required"] is True for gate in gates)
    assert evalrun.overall_status(
        [evalrun.make_gate("G0", "structural", True, "PASS", "ok")] + gates
    ) == ("INCOMPLETE", 2)


def test_agent_robustness_gate_passes_when_the_agent_crashes():
    from pathlib import Path

    root = Path(evalrun.__file__).resolve().parent.parent
    gate = evalrun.agent_robustness_gate(root)

    assert gate["id"] == "G7"
    assert gate["status"] == "PASS", gate["detail"]
    names = {check["name"] for check in gate["checks"]}
    assert "machine fields identical to the kernel" in names
    assert "crashes recorded, not swallowed" in names


def test_report_emission_is_stable(tmp_path):
    gates = [evalrun.make_gate("G0", "structural", True, "PASS", "ok")]
    metrics = {"safety": {"status_upgrades": 0, "safe_overclaims": 0}}
    report_a = evalrun.assemble_report(
        mode="pr",
        gates=gates,
        metrics=metrics,
        provenance={"generated_at": "2026-01-01T00:00:00Z"},
    )
    report_b = evalrun.assemble_report(
        mode="pr",
        gates=gates,
        metrics=metrics,
        provenance={"generated_at": "2026-01-02T00:00:00Z"},
    )

    # Volatile provenance does not change the machine fingerprint.
    assert report_a["machine_sha256"] == report_b["machine_sha256"]
    assert report_a["provenance"] != report_b["provenance"]

    # Identical report input emits byte-identical JSON and Markdown.
    json_a, markdown_a = evalrun.write_reports(report_a, tmp_path / "a")
    json_b, markdown_b = evalrun.write_reports(report_a, tmp_path / "b")
    assert json_a.read_bytes() == json_b.read_bytes()
    assert markdown_a.read_bytes() == markdown_b.read_bytes()


def test_spending_changes_reject_conflicting_targets_and_action_cap():
    with pytest.raises(ValueError):
        semantics.parse_spending_changes("stop:event_1|reduce_to:event_1:5")
    with pytest.raises(ValueError):
        semantics.parse_spending_changes(
            "stop:event_1|stop:event_2|stop:event_3|stop:event_4"
        )


def test_pr_production_pass_does_not_overwrite_submission_artifacts(tmp_path, monkeypatch):
    import pandas as pd

    root = tmp_path
    sample_dir = root / "dataset"
    sample_dir.mkdir()
    sample_row = _row()
    with (sample_dir / "sample_requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sample_row))
        writer.writeheader()
        writer.writerow(sample_row)

    calls = []

    def fake_run_pipeline(repo_root=None, **kwargs):
        calls.append({"repo_root": repo_root, **kwargs})
        return pd.DataFrame([sample_row]), {}

    monkeypatch.setattr("code.main.run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        evalrun,
        "run_structural_checks",
        lambda _root: (
            evalrun.make_gate("G0", "structural", True, "PASS", "ok"),
            {
                "checks_total": 1,
                "checks_passed": 1,
                "checks_failed": 0,
                "failed_checks": [],
                "returncode": 0,
            },
        ),
    )
    monkeypatch.setattr(
        evalrun,
        "provenance_metrics",
        lambda _root: {
            "commit_sha": "abc123",
            "output_sha256": "out",
            "usage_report_sha256": "usage",
            "usage_report_mirror_sha256": "usage",
            "usage_report_mirrors_match": True,
            "usage_report_fields_present": True,
        },
    )

    evalrun.run_pr(root)

    assert calls[0]["write_output"] is False
    assert calls[0]["write_usage"] is False
