"""Deterministic correctness harness for the Buy or Wait? submission.

Machine-checkable summary of the hard contract (AGENTS 6.2/6.3 and
problem_statement): row count, exact column order, per-request ID set
equality with dataset/requests.csv, amount bounds, status/method
vocabularies, payment-plan grammar, partial-payment legs, installment
plans matched against supplied options, spending-change grammar and
target validity, explanation limits, anti-leak gates (no sample event
IDs, no secrets).  Prints PASS/FAIL per check plus informational counts,
and exits non-zero when any check fails.

Run: python tools/correctness_report.py
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "output.csv"
REQUESTS_PATH = REPO_ROOT / "dataset" / "requests.csv"
PROFILES_PATH = REPO_ROOT / "dataset" / "financial_profiles.csv"
EVENTS_PATH = REPO_ROOT / "dataset" / "financial_events.csv"
OPTIONS_PATH = REPO_ROOT / "dataset" / "request_payment_options.csv"

EXPECTED_ROWS = 250
COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]
STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}

CHANGE_TARGET_STATUSES = {"settled", "scheduled", "pending"}
CHANGE_EVENT_TYPES = {"expense", "subscription"}
CHANGE_FLEXIBILITIES = {"stoppable", "reducible", "reducible_or_stoppable"}

_STOP_RE = re.compile(r"^stop:([A-Za-z0-9_]+)$")
_REDUCE_RE = re.compile(r"^reduce_to:([A-Za-z0-9_]+):(\d+(?:\.\d+)?)$")
_LEG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(-?\d+(?:\.\d+)?)$")

# Public sample event IDs that must never appear in output.csv or code/
# (assembled, not literal, so this file itself carries no label).
SAMPLE_EVENT_IDS = tuple(f"event_{n}" for n in (476, 989, 1815, 1816))

# Same gates as tests/test_submission.py::test_no_secrets_in_submission_surface.
PRIVATE_KEY_PATTERN = "-----BEGIN .*" + "PRIVATE" + " KEY"
SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9]{8,}",
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[A-Za-z0-9]{8,}",
    r"xox[bpas]-[A-Za-z0-9-]+",
    r"AIza[0-9A-Za-z_-]{10,}",
    PRIVATE_KEY_PATTERN,
    r"aws_secret",
    r"Bearer [A-Za-z0-9._-]{8,}",
)
ENV_SECRET_FALLBACK = r"os\.getenv\([\"'][^\"']+[\"'],\s*[\"']sk-"
BANNED_NAMES = {".env", "credentials.json", "id_rsa", "id_ed25519", "log.txt"}
BANNED_SUFFIXES = (".env",)
SKIP_SUFFIXES = {".pyc", ".pyo", ".png", ".zip"}
SKIP_DIR_NAMES = {"__pycache__", ".git", ".venv", ".superpowers", ".pytest_cache"}

MAX_EXPLANATION_CHARS = 400


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def field(row: dict, column: str) -> str:
    return str(row.get(column) or "").strip()


def parse_decimal(value: str) -> Decimal:
    return Decimal(value.strip())


def package_surface() -> list[Path]:
    """Everything that lands in code.zip, plus README.md and docs/."""
    paths = [REPO_ROOT / "README.md", REPO_ROOT / "problem_statement.md", OUTPUT_PATH]
    for tree in ("code", "docs", "evaluation"):
        root = REPO_ROOT / tree
        if root.is_dir():
            paths.extend(sorted(root.rglob("*")))
    usage = REPO_ROOT / "tools" / "evaluation" / "usage_report.md"
    if usage.is_file():
        paths.append(usage)
    return [p for p in paths if p.is_file() and p.suffix not in SKIP_SUFFIXES]


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool]] = []

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

    @property
    def failed(self) -> list[str]:
        return [name for name, ok in self.results if not ok]


def check_rows(rows, requests, report) -> None:
    ok = len(rows) == EXPECTED_ROWS and len(rows) == len(requests)
    report.record(
        "row_count",
        ok,
        f"{len(rows)} data rows (expected {EXPECTED_ROWS}; dataset/requests.csv has {len(requests)})",
    )

    columns = [c for c in (rows[0].keys() if rows else []) if c is not None]
    report.record("column_order", columns == COLUMNS, f"columns={columns}")

    out_ids = [field(r, "request_id") for r in rows]
    req_ids = [field(r, "request_id") for r in requests]
    duplicates = sorted({i for i in out_ids if out_ids.count(i) > 1})
    missing = sorted(set(req_ids) - set(out_ids))
    extra = sorted(set(out_ids) - set(req_ids))
    ok = set(out_ids) == set(req_ids) and not duplicates
    detail = f"{len(set(out_ids))} unique IDs; missing={missing[:5]} extra={extra[:5]} dupes={duplicates[:5]}"
    report.record("request_id_set", ok, detail)


def check_amounts(rows, requests, report) -> None:
    by_id = {field(r, "request_id"): r for r in requests}
    bad = []
    for row in rows:
        rid = field(row, "request_id")
        req = by_id.get(rid)
        if req is None:
            bad.append(f"{rid}: unknown request")
            continue
        try:
            safe = parse_decimal(field(row, "amount_safe_to_pay"))
            requested = parse_decimal(field(req, "requested_amount"))
        except (InvalidOperation, ValueError):
            bad.append(f"{rid}: unparseable amount")
            continue
        if not (Decimal(0) <= safe <= requested):
            bad.append(f"{rid}: {safe} not in [0, {requested}]")
    report.record(
        "amount_safe_to_pay_bounds",
        not bad,
        "all rows within [0, requested_amount]" if not bad else f"{len(bad)} rows: {bad[:5]}",
    )


def check_vocabularies(rows, report) -> None:
    bad_status = [field(r, "request_id") for r in rows if field(r, "affordability_status") not in STATUSES]
    report.record(
        "affordability_status_vocabulary",
        not bad_status,
        f"allowed={sorted(STATUSES)}" if not bad_status else f"violations={bad_status[:5]}",
    )
    bad_method = [field(r, "request_id") for r in rows if field(r, "recommended_payment_method") not in METHODS]
    report.record(
        "recommended_payment_method_vocabulary",
        not bad_method,
        f"allowed={sorted(METHODS)}" if not bad_method else f"violations={bad_method[:5]}",
    )


def parse_plan(plan: str) -> list[tuple[date, Decimal]]:
    """Chronological legs; raises ValueError on malformed input. 'none' -> []."""
    if plan == "none":
        return []
    if not plan:
        raise ValueError("empty payment_plan")
    legs = []
    for part in plan.split("|"):
        match = _LEG_RE.match(part.strip())
        if not match:
            raise ValueError(f"malformed leg {part!r}")
        legs.append((date.fromisoformat(match.group(1)), Decimal(match.group(2))))
    dates = [d for d, _ in legs]
    if dates != sorted(dates):
        raise ValueError("legs not chronological")
    return legs


def check_plans(rows, requests, options_by_request, report) -> None:
    by_id = {field(r, "request_id"): r for r in requests}
    bad_grammar, bad_earliest, bad_partial, bad_installment = [], [], [], []

    for row in rows:
        rid = field(row, "request_id")
        status = field(row, "affordability_status")
        method = field(row, "recommended_payment_method")
        plan = field(row, "payment_plan")
        earliest = field(row, "earliest_date_for_full_payment")
        req = by_id.get(rid, {})

        try:
            legs = parse_plan(plan)
        except (ValueError, InvalidOperation) as exc:
            bad_grammar.append(f"{rid}: {exc}")
            legs = None

        if earliest:
            try:
                earliest_date = date.fromisoformat(earliest)
            except ValueError:
                bad_earliest.append(f"{rid}: earliest {earliest!r} is not an ISO date")
                earliest_date = None
        else:
            earliest_date = None
        if status == "affordable_now":
            request_date = field(req, "request_date")
            if earliest != request_date:
                bad_earliest.append(f"{rid}: affordable_now but earliest={earliest!r} != {request_date!r}")

        if method == "partial_payment" and legs is not None:
            try:
                safe = parse_decimal(field(row, "amount_safe_to_pay"))
                requested = parse_decimal(field(req, "requested_amount"))
            except (InvalidOperation, ValueError):
                bad_partial.append(f"{rid}: unparseable amount")
                continue
            problems = []
            if len(legs) != 2:
                problems.append(f"{len(legs)} legs")
            else:
                if legs[0][0].isoformat() != field(req, "request_date"):
                    problems.append(f"leg 1 on {legs[0][0].isoformat()}, not request_date")
                if legs[0][1] != safe:
                    problems.append(f"leg 1 {legs[0][1]} != amount_safe_to_pay {safe}")
                if legs[0][1] + legs[1][1] != requested:
                    problems.append(f"legs sum {legs[0][1] + legs[1][1]} != requested {requested}")
                if not (Decimal(0) < safe < requested):
                    problems.append(f"amount_safe_to_pay {safe} not in (0, {requested})")
            if problems:
                bad_partial.append(f"{rid}: " + "; ".join(problems))

        if method == "installments" and legs is not None:
            expected = expected_installment_plans(options_by_request.get(rid, []))
            if field(row, "payment_plan") not in expected:
                bad_installment.append(f"{rid}: plan does not match any supplied installment option")

    report.record(
        "payment_plan_grammar",
        not bad_grammar,
        "all plans are 'none' or chronological YYYY-MM-DD:amount legs" if not bad_grammar else f"{len(bad_grammar)} rows: {bad_grammar[:5]}",
    )
    report.record(
        "earliest_date_for_full_payment",
        not bad_earliest,
        "ISO-dated or empty; affordable_now implies request_date" if not bad_earliest else f"{len(bad_earliest)} rows: {bad_earliest[:5]}",
    )
    report.record(
        "partial_payment_legs",
        not bad_partial,
        "two legs, leg 1 on request_date == amount_safe_to_pay, sum == requested_amount" if not bad_partial else f"{len(bad_partial)} rows: {bad_partial[:5]}",
    )
    report.record(
        "installment_plan_matches_option",
        not bad_installment,
        "every installment plan equals a supplied option schedule" if not bad_installment else f"{len(bad_installment)} rows: {bad_installment[:5]}",
    )


def expected_installment_plans(options: list[dict]) -> set[str]:
    plans = set()
    for opt in options:
        if field(opt, "payment_method") != "installments":
            continue
        try:
            amount = parse_decimal(field(opt, "payment_amount"))
            count = int(float(field(opt, "number_of_payments")))
            first = date.fromisoformat(field(opt, "first_payment_date"))
        except (InvalidOperation, ValueError):
            continue
        try:
            freq = int(float(field(opt, "payment_frequency_days")))
        except (InvalidOperation, ValueError):
            freq = 30
        legs = [f"{(first + timedelta(days=i * freq)).isoformat()}:{amount}" for i in range(count)]
        plans.add("|".join(legs))
    return plans


def check_spending_changes(rows, requests, profiles, events, report) -> None:
    by_id = {field(r, "request_id"): r for r in requests}
    profile_by_user = {field(p, "user_id"): p for p in profiles}
    grammar_bad, target_bad = [], []
    changed_rows = []

    def categories(profile: dict, key: str) -> set[str]:
        return {part.strip().lower() for part in field(profile, key).split("|") if part.strip()}

    for row in rows:
        rid = field(row, "request_id")
        value = field(row, "spending_changes_needed")
        if value != "none":
            changed_rows.append(rid)
        if value == "none":
            continue

        actions = value.split("|")
        if len(actions) > 3:
            grammar_bad.append(f"{rid}: {len(actions)} actions (max 3)")
        seen: set[str] = set()
        parsed = []
        for action in actions:
            stop_match = _STOP_RE.match(action)
            reduce_match = _REDUCE_RE.match(action)
            if stop_match:
                parsed.append(("stop", stop_match.group(1), None))
                event_id = stop_match.group(1)
            elif reduce_match:
                parsed.append(("reduce_to", reduce_match.group(1), Decimal(reduce_match.group(2))))
                event_id = reduce_match.group(1)
            else:
                grammar_bad.append(f"{rid}: malformed action {action!r}")
                continue
            if event_id in seen:
                grammar_bad.append(f"{rid}: stop/reduce conflict on {event_id}")
            seen.add(event_id)

        req = by_id.get(rid, {})
        user_id = field(req, "user_id")
        profile = profile_by_user.get(user_id, {})
        protected = categories(profile, "expense_categories_to_protect")
        stop_ok = categories(profile, "expense_categories_user_is_willing_to_stop")
        reduce_ok = categories(profile, "expense_categories_user_is_willing_to_reduce")

        for kind, event_id, new_amount in parsed:
            event = events.get(event_id)
            if event is None:
                target_bad.append(f"{rid}: {event_id} not in financial_events.csv")
                continue
            status = field(event, "status").lower()
            direction = field(event, "direction").lower()
            event_type = field(event, "event_type").lower()
            flexibility = field(event, "flexibility").lower()
            category = field(event, "category").strip().lower()
            problems = []
            if field(event, "user_id") != user_id:
                problems.append("not owned by request user")
            if direction != "debit":
                problems.append(f"direction={direction}")
            if status not in CHANGE_TARGET_STATUSES:
                problems.append(f"status={status}")
            if event_type not in CHANGE_EVENT_TYPES:
                problems.append(f"event_type={event_type}")
            if flexibility not in CHANGE_FLEXIBILITIES:
                problems.append(f"flexibility={flexibility}")
            if category in protected:
                problems.append(f"protected category {category}")
            if kind == "stop" and category not in stop_ok:
                problems.append(f"category {category} not in stop list")
            if kind == "reduce_to":
                if category not in reduce_ok:
                    problems.append(f"category {category} not in reduce list")
                try:
                    original = parse_decimal(field(event, "amount"))
                    if not (Decimal(0) <= new_amount < original):
                        problems.append(f"reduce_to {new_amount} not in [0, {original})")
                except (InvalidOperation, ValueError):
                    problems.append("unparseable event amount")
            if problems:
                target_bad.append(f"{rid}: {event_id} " + "; ".join(problems))

    report.record(
        "spending_change_grammar",
        not grammar_bad,
        "'none' or <=3 stop:/reduce_to: actions with unique event IDs" if not grammar_bad else f"{len(grammar_bad)} rows: {grammar_bad[:5]}",
    )
    report.record(
        "spending_change_targets",
        not target_bad,
        "all targets are non-protected flexible debits in the user's stop/reduce lists"
        if not target_bad else f"{len(target_bad)} problems: {target_bad[:5]}",
    )
    print(
        f"INFO spending_changes_needed != 'none': {len(changed_rows)} rows "
        f"({', '.join(changed_rows) if changed_rows else 'none'})"
    )


def check_explanations(rows, report) -> None:
    bad = []
    lengths = []
    for row in rows:
        rid = field(row, "request_id")
        text = field(row, "decision_explanation")
        if not text:
            bad.append(f"{rid}: empty")
            continue
        lengths.append(len(text))
        if len(text) >= MAX_EXPLANATION_CHARS:
            bad.append(f"{rid}: {len(text)} chars")
    report.record(
        "decision_explanation",
        not bad,
        f"non-empty and < {MAX_EXPLANATION_CHARS} chars"
        + (f"; length {min(lengths)}-{max(lengths)}" if lengths else "")
        if not bad else f"{len(bad)} rows: {bad[:5]}",
    )


def check_no_sample_event_ids(report) -> None:
    hits = []
    scanned = 0
    targets = [OUTPUT_PATH] if OUTPUT_PATH.is_file() else []
    code_root = REPO_ROOT / "code"
    if code_root.is_dir():
        targets += [p for p in sorted(code_root.rglob("*")) if p.is_file() and p.suffix not in SKIP_SUFFIXES]
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="ignore")
        scanned += 1
        for sample_id in SAMPLE_EVENT_IDS:
            if sample_id in text:
                hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{sample_id}")
    report.record(
        "no_sample_event_ids",
        not hits,
        f"no sample event IDs in output.csv or code/ ({scanned} files scanned)"
        if not hits else f"leaked: {hits}",
    )


def check_no_secrets(report) -> None:
    hits = []
    scanned = 0
    for path in package_surface():
        if path.name in BANNED_NAMES or path.name.endswith(BANNED_SUFFIXES):
            hits.append(f"banned file: {path.relative_to(REPO_ROOT).as_posix()}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        scanned += 1
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {pattern}")
        if re.search(ENV_SECRET_FALLBACK, text):
            hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}: env fallback secret")
    report.record(
        "no_secrets",
        not hits,
        f"submission surface clean ({scanned} files scanned)"
        if not hits else f"leaks: {hits[:5]}",
    )


def main() -> int:
    print("== Buy or Wait? correctness report ==")
    report = Report()

    if not REQUESTS_PATH.is_file():
        print(f"FAIL dataset_missing: {REQUESTS_PATH} not found")
        return 1
    requests = load_csv(REQUESTS_PATH)
    if not OUTPUT_PATH.is_file():
        print("FAIL output_missing: output.csv not found")
        return 1
    rows = load_csv(OUTPUT_PATH)

    check_rows(rows, requests, report)
    check_amounts(rows, requests, report)
    check_vocabularies(rows, report)

    options_by_request: dict[str, list[dict]] = {}
    if OPTIONS_PATH.is_file():
        for opt in load_csv(OPTIONS_PATH):
            options_by_request.setdefault(field(opt, "request_id"), []).append(opt)
    check_plans(rows, requests, options_by_request, report)

    profiles = load_csv(PROFILES_PATH) if PROFILES_PATH.is_file() else []
    events: dict[str, dict] = {}
    if EVENTS_PATH.is_file():
        for event in load_csv(EVENTS_PATH):
            events.setdefault(field(event, "event_id"), event)
    check_spending_changes(rows, requests, profiles, events, report)

    check_explanations(rows, report)
    check_no_sample_event_ids(report)
    check_no_secrets(report)

    status_counts = Counter(field(r, "affordability_status") for r in rows)
    method_counts = Counter(field(r, "recommended_payment_method") for r in rows)
    print(f"INFO status counts: {dict(sorted(status_counts.items()))}")
    print(f"INFO method counts: {dict(sorted(method_counts.items()))}")

    failed = report.failed
    if failed:
        print(f"RESULT: FAIL ({len(failed)}/{len(report.results)} checks failed: {', '.join(failed)})")
        return 1
    print(f"RESULT: PASS ({len(report.results)}/{len(report.results)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
