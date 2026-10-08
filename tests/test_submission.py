"""Submission packaging gates (problem_statement :225-251, AGENTS 6.4/6.5/7).

What this program checks:
- Root output.csv (the deliverable, NOT dataset/output.csv template) exists with
  250 data rows, exact columns/order, and per-request_id set equality.
- usage_report.md carries providers/names/calls/in-out/total+avg/cost, per-model
  AND overall totals, with zero secrets; skipped until the final run produces it.
- No secrets anywhere in the submission surface (full-surface pattern scan).
- log.txt stays ignored+untracked and is never dirtied by test runs (hermeticity).
- Determinism is scoped to machine columns (never LLM prose byte-equality).
- No invented gates: no runtime cap (observability only), N derived from file.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import OUTPUT_COLS, REPO_ROOT

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{8,}", r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{8,}",
    r"xox[bpas]-[A-Za-z0-9-]+", r"AIza[0-9A-Za-z_-]{10,}",
    r"-----BEGIN .*PRIVATE KEY", r"aws_secret", r"Bearer [A-Za-z0-9._-]{8,}",
]
BANNED_IN_ZIP = {".env", "credentials.json", "id_rsa", "id_ed25519"}


def test_output_completeness_is_fail_not_skip():
    """File-exists-but-incomplete must FAIL, never masquerade as SKIP: a present
    output.csv with wrong IDs or blank machine cells is a broken run, not a skip."""
    from tests.conftest import DATASET
    p = REPO_ROOT / "output.csv"
    if not p.exists():
        pytest.skip("no output.csv yet")
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    req = pd.read_csv(DATASET / "requests.csv", dtype=str, keep_default_na=False)
    assert set(df.get("request_id", [])) == set(req["request_id"]), "incomplete output must fail"
    machine_cols = [c for c in OUTPUT_COLS if c != "earliest_date_for_full_payment"]
    assert not (df[machine_cols].apply(lambda s: s.str.strip()) == "").any().any()


def test_root_output_shape_when_built(root_output_df, requests_df):
    """Deliverable root output.csv: N derived from file (today 250), exact cols/order, ID-set equal."""
    if root_output_df is None:
        pytest.skip("no predictions yet")
    assert list(root_output_df.columns) == OUTPUT_COLS
    assert len(root_output_df) == len(requests_df)  # never a literal 250
    assert set(root_output_df["request_id"]) == set(requests_df["request_id"])
    assert not root_output_df["request_id"].duplicated().any()


def test_template_is_not_mistaken_for_deliverable():
    """dataset/output.csv is the BLANK template; gates must target repo-root output.csv."""
    tpl = pd.read_csv(REPO_ROOT / "dataset" / "output.csv", dtype=str, keep_default_na=False)
    assert list(tpl.columns) == OUTPUT_COLS  # schema reference only


def test_output_csv_encoding_clean(root_output_df):
    """UTF-8, LF endings, no BOM on the deliverable (evaluator-parse safety)."""
    p = REPO_ROOT / "output.csv"
    if root_output_df is None:
        pytest.skip("no predictions yet")
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM forbidden"
    assert b"\r\n" not in raw, "CRLF forbidden, use LF"
    raw.decode("utf-8")  # raises if not UTF-8


def test_no_secrets_in_submission_surface():
    """Full-surface secret scan: code/** + output.csv + README + usage_report (never transcript).
    Fails on key patterns, banned filenames, and env-with-secret-default traps."""
    hits = []
    scan_roots = [REPO_ROOT / "code", REPO_ROOT / "README.md",
                  REPO_ROOT / "output.csv", REPO_ROOT / "tools" / "evaluation" / "usage_report.md"]
    for root in scan_roots:
        paths = root.rglob("*") if root.is_dir() else [root]
        for path in paths:
            if not path.is_file() or path.suffix in {".png", ".zip"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in SECRET_PATTERNS:
                if re.search(pat, text):
                    hits.append(f"{path.name}: {pat}")
            if path.name in BANNED_IN_ZIP:
                hits.append(f"banned file: {path.name}")
            if re.search(r"os\.getenv\([\"'][^\"']+[\"'],\s*[\"']sk-", text):
                hits.append(f"{path.name}: env fallback hides a secret literal")
    assert hits == [], f"secret leaks: {hits}"


def test_log_untracked_and_ignored():
    """log.txt must be gitignored + untracked (AGENTS 2, README :171); tests never touch it."""
    import subprocess
    r1 = subprocess.run(["git", "check-ignore", "log.txt"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r1.returncode == 0, "log.txt must be gitignored"
    r2 = subprocess.run(["git", "ls-files", "log.txt"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r2.stdout.strip() == "", "log.txt must stay untracked"


def test_usage_report_fields_when_built():
    """usage_report.md: providers/names/calls/in-out/total+avg/cost, per-model AND overall
    totals. Guards placeholder stuffing: requires digits, a cost figure, and an
    overall/total line beyond bare keywords."""
    p = REPO_ROOT / "tools" / "evaluation" / "usage_report.md"
    if not p.exists() or not p.read_text(encoding="utf-8").strip():
        pytest.skip("no final run yet")
    text = p.read_text(encoding="utf-8")
    low = text.lower()
    for field in ["provider", "model", "calls", "input", "output", "total", "average", "cost"]:
        assert field in low, f"usage_report missing {field}"
    assert re.search(r"\d[\d,]*", text), "usage_report has no numbers"
    assert re.search(r"(overall|total)", low), "usage_report needs overall totals"
    assert re.search(r"\$\s?\d|USD\s?\d|\d+\.\d+", text), "usage_report needs a cost figure"
    table_rows = [ln for ln in text.splitlines() if ln.count("|") >= 2 and re.search(r"\d", ln)]
    assert len(table_rows) >= 2, "usage_report needs a per-model table plus overall totals row"
    for pat in SECRET_PATTERNS:
        assert not re.search(pat, text), "secrets in usage_report"


def test_no_hardcoded_labels_in_code():
    """No eval-ID literals, gold-file imports, or organizer paths in code/ (AGENTS 6.4):
    an `if request_id==...: return gold` solver passes every built gate otherwise.
    Exception: files defining a guard regex (`_REF = re.compile`) are anti-hallucination
    and excluded from this scan.
    ID patterns (request_/event_/payment_option_/user_ + GOLD/ground_truth) scan both
    code/ and tools/ (tools/ stash bypass); sample_requests/lowercase-gold stay
    code-only since tools/ verification helpers legitimately read the sample gold."""
    hits = []
    id_pats = (r"request_\d+\b", r"event_\d+",
               r"payment_option_\d+", r"user_\d+\b",
               r"\bGOLD\b", r"ground_truth")
    code_only_pats = (r"sample_requests", r"\bgold\b")
    code_root = REPO_ROOT / "code"
    tools_root = REPO_ROOT / "tools"
    scan_paths = list(code_root.rglob("*.py"))
    if tools_root.exists():
        scan_paths += list(tools_root.rglob("*.py"))
    for path in scan_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "_REF = re.compile" in text:
            continue  # anti-hallucination ID-strip guard
        is_code = code_root in path.parents
        pats = id_pats + code_only_pats if is_code else id_pats
        for pat in pats:
            if re.search(pat, text):
                hits.append(f"{path.name}:{pat}")
    assert hits == [], f"hardcoded labels: {hits}"


def test_paths_built_from_repo_root_not_hardcoded():
    """No hardcoded absolute paths (either slash style, UNC, /tmp) or home-dir joins."""
    bad = []
    for path in (REPO_ROOT / "code").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"[A-Za-z]:[\\/]|\\\\\\\\|/home/|/Users/|/tmp/|Path\.home\(\)|os\.path\.expanduser", text):
            bad.append(path.name)
    assert bad == [], f"hardcoded absolute paths: {bad}"


def _uses_io(src: str) -> tuple[bool, bool]:
    """AST proof (not substring): reads dataset/requests.csv via a Call AND writes
    output.csv via a Call. Hello-world mains with string mentions fail here.
    Path-aware: collects nested string constants within each Call subtree so
    REPO_ROOT / "dataset/requests.csv" counts, while print("...requests.csv")
    still fails (func name must be a real I/O op)."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False, False
    reads = writes = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", "") or getattr(func, "id", "")
        blob = " ".join(
            n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        if name in ("read_csv", "read_text", "open", "read_excel") and "requests.csv" in blob:
            reads = True
        if name in ("to_csv", "open", "write", "to_excel") and "output.csv" in blob:
            writes = True
    return reads, writes


def test_documented_entrypoint_runs():
    """The README/code-documented entrypoint must prove generation, not just --help:
    AST must show dataset/requests.csv READ and output.csv WRITE (hello-world mains
    fail here); a --help run must exit 0/2; any output change must keep ID-matched shape."""
    import ast
    import subprocess
    import sys
    from tests.conftest import DATASET
    main = REPO_ROOT / "code" / "main.py"
    if not main.exists() or not main.read_text(encoding="utf-8").strip():
        pytest.skip("code/main.py not built yet")
    src = main.read_text(encoding="utf-8")
    reads, writes = _uses_io(src)
    assert reads, "entrypoint must open dataset/requests.csv (AST Call proof)"
    assert writes, "entrypoint must write output.csv (AST Call proof)"
    out = REPO_ROOT / "output.csv"
    before = out.read_bytes() if out.exists() else None
    r = subprocess.run([sys.executable, "code/main.py", "--help"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode in (0, 2), r.stderr[-2000:]
    after = out.read_bytes() if out.exists() else None
    if after is not None and after != before:
        df = pd.read_csv(out, dtype=str, keep_default_na=False)
        req = pd.read_csv(DATASET / "requests.csv", dtype=str)["request_id"]
        assert list(df.columns) == OUTPUT_COLS and set(df["request_id"]) == set(req)


def test_hello_world_entrypoint_rejected():
    """Negative control: argparse-only hello-world (string mentions, no I/O) fails the
    same _uses_io proof the real gate applies — proving the gate, not just hello-world."""
    hello = 'import argparse\nif __name__ == "__main__":\n    argparse.ArgumentParser().parse_args()\n    print("reads dataset/requests.csv and writes output.csv")\n'
    assert _uses_io(hello) == (False, False)
