"""Hardening gates (T3/R3): 2-digit IDs, user_/GOLD/ground_truth, tools/ scope,
usage_report path, NaN/earliest, CWD fragility.

TDD RED phase: tests asserting DESIRED behavior that the current suite misses.
Each test reads the current scan/source to prove the gap (FAIL before fix,
PASS after minimal GREEN fix). No code/*.py literals added here affect the
scan (scan covers code/+tools/ only, never tests/).
"""
import re
from decimal import Decimal
from pathlib import Path

from tests.conftest import REPO_ROOT
from tests.spec_checks import check_bounds, to_decimal

OLD_REQUEST_PAT = r"request_(0\d\d|1\d\d|2[0-6]\d|27[0-5])\b"
DESIRED_PATS = [
    r"request_\d+\b",
    r"event_\d+",
    r"payment_option_\d+",
    r"user_\d+\b",
    r"\bGOLD\b",
    r"ground_truth",
]


def test_desired_scan_flags_two_digit_ids_while_old_misses():
    """Desired improved regex flags request_27/request_99/user_12/GOLD/ground_truth;
    old 3-digit-only pattern misses the 2-digit eval IDs (documents T3 gap)."""
    for s in ["request_27", "request_26", "request_99", "user_12", "GOLD", "ground_truth"]:
        assert any(re.search(p, s) for p in DESIRED_PATS), f"desired must flag {s!r}"
    missed = [s for s in ["request_27", "request_26", "request_99"]
              if not re.search(OLD_REQUEST_PAT, s)]
    assert missed == ["request_27", "request_26", "request_99"], "old must miss 2-digit"
    # RED: current test_submission.py must already carry the robust generic pattern
    text = (REPO_ROOT / "tests" / "test_submission.py").read_text(encoding="utf-8")
    assert r"request_\d+" in text, "scan still uses narrow 3-digit request pattern"


def test_submission_scan_covers_user_gold_and_tools_scope():
    """Scan must include user_\\d+ + GOLD/ground_truth variants and tools/ scope."""
    text = (REPO_ROOT / "tests" / "test_submission.py").read_text(encoding="utf-8")
    assert r"user_\d+" in text, "scan missing user_\\d+ pattern"
    assert "GOLD" in text, "scan missing GOLD variant"
    assert "ground_truth" in text, "scan missing ground_truth pattern"
    # tools/ scope: scan must enumerate tools/ in addition to code/
    assert "tools" in text, "scan scope is code/-only, tools/ stash bypasses"


def test_usage_report_scanned_path_is_real_stub():
    """Scanned path must be tools/evaluation/usage_report.md (real stub), not
    code/evaluation/... which does not exist (permanent SKIP + unscanned secrets)."""
    text = (REPO_ROOT / "tests" / "test_submission.py").read_text(encoding="utf-8")
    assert (REPO_ROOT / "tools" / "evaluation" / "usage_report.md").exists(), "real stub missing"
    assert not (REPO_ROOT / "code" / "evaluation" / "usage_report.md").exists(), "unexpected stale path"
    assert "tools" in text and "usage_report.md" in text, "scan still points at code/evaluation path"
    assert "code/evaluation" not in text and 'code" / "evaluation' not in text, \
        "stale code/evaluation path still present"


def test_to_decimal_rejects_non_finite():
    """to_decimal('NaN'/'Infinity') must return None (clean FAIL), not Decimal NaN."""
    assert to_decimal("NaN") is None
    assert to_decimal("nan") is None
    assert to_decimal("Infinity") is None
    assert to_decimal("-Infinity") is None
    assert to_decimal("100.00") == Decimal("100.00")


def test_check_bounds_nan_clean_fail_not_error():
    """amount_safe_to_pay='NaN' must be a clean FAIL list, never InvalidOperation ERROR."""
    errs = check_bounds({"amount_safe_to_pay": "NaN"}, Decimal("100"))
    assert errs != []
    errs2 = check_bounds({"amount_safe_to_pay": "None"}, Decimal("100"))
    assert errs2 != []


def test_earliest_nan_none_null_rejected():
    """earliest='NaN'/'None'/'null'/malformed must FAIL consistency, not pass."""
    from tests.spec_checks import check_status_method_consistency
    row_base = {
        "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "installments",
        "amount_safe_to_pay": "100",
        "payment_plan": "none",
        "spending_changes_needed": "none",
        "decision_explanation": "x",
    }
    req = {"requested_amount": "200", "request_date": "2024-09-04",
           "desired_completion_date": "2024-10-04", "allows_partial_payment": "true"}
    prof = {"payment_methods_user_will_consider": "installments",
            "max_installment_months": "12",
            "expense_categories_user_is_willing_to_reduce": "",
            "expense_categories_user_is_willing_to_stop": "",
            "expense_categories_to_protect": ""}
    for bad in ["NaN", "None", "null", "not-a-date", "2024-13-99"]:
        row = dict(row_base, earliest_date_for_full_payment=bad)
        assert check_status_method_consistency(row, req, prof) != [], f"earliest {bad!r} must FAIL"
    # positive control: valid date or empty passes this gate (other gates may still apply)
    row_ok = dict(row_base, earliest_date_for_full_payment="2024-09-15")
    assert check_status_method_consistency(row_ok, req, prof) == []


def test_main_uses_repo_root_not_cwd():
    """code/main.py must resolve dataset/output via Path(__file__) (REPO_ROOT),
    not CWD-relative 'dataset/...' (fragile when run from another cwd)."""
    text = (REPO_ROOT / "code" / "main.py").read_text(encoding="utf-8")
    assert "REPO_ROOT" in text, "main.py still CWD-fragile (no REPO_ROOT)"
    assert 'pd.read_csv("dataset/requests.csv"' not in text, "CWD-relative read remains"
    assert 'to_csv("output.csv"' not in text, "CWD-relative write remains"
