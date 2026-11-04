"""Issue #8: the CLI text must not claim PR mode rewrites ``output.csv``.

``run_pr`` executes the production pipeline with ``write_output=False`` and G4
reports the hash of the existing snapshot, so neither the module docstring nor
the argparse description may say that PR mode regenerates the deliverable.
"""
from __future__ import annotations

import csv
import re

import pytest

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


def _flat(text: str) -> str:
    return " ".join((text or "").replace("`", "").split())


def _says_pr_leaves_output_alone(text: str) -> bool:
    return bool(
        re.search(
            r"pr mode.{0,240}?does not (?:rewrite|regenerate|write|touch) "
            r"(?:the )?output\.csv",
            _flat(text),
            re.IGNORECASE,
        )
    )


def test_parser_description_says_pr_mode_leaves_output_csv_alone():
    parser = evalrun.build_parser()
    description = _flat(parser.description)

    assert "regenerates output.csv" not in description.lower()
    assert _says_pr_leaves_output_alone(description), (
        "the argparse description must say PR mode does not rewrite output.csv; "
        f"got: {description}"
    )
    assert "scores the existing" in description.lower(), (
        "the argparse description must say PR mode scores the existing snapshot; "
        f"got: {description}"
    )


def test_module_docstring_says_pr_mode_leaves_output_csv_alone():
    docstring = _flat(evalrun.__doc__)

    assert "regenerates output.csv" not in docstring.lower()
    assert _says_pr_leaves_output_alone(docstring), (
        "the module docstring must say PR mode does not rewrite output.csv; "
        f"got: {docstring}"
    )


def test_pr_mode_cli_never_writes_output_csv(tmp_path, monkeypatch):
    import pandas as pd

    sample_row = _row()
    sample_dir = tmp_path / "dataset"
    sample_dir.mkdir()
    with (sample_dir / "sample_requests.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
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

    exit_code = evalrun.main(
        [
            "--mode",
            "pr",
            "--repo-root",
            str(tmp_path),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )

    # The single-row fixture cannot meet G2's sample thresholds, so only the
    # write contract is asserted here; the CLI path itself must complete.
    assert isinstance(exit_code, int)
    assert (tmp_path / "reports" / "report.json").is_file()
    assert calls, "PR mode must run the production pipeline"
    assert all(call["write_output"] is False for call in calls)
    assert all(call["write_usage"] is False for call in calls)
    assert not (tmp_path / "output.csv").exists()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
