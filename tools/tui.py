"""Interactive TUI for Buy or Wait?.

Launch from the repo root:

    python -m tools.tui

Safety rules baked into this module:

* Nothing is ever written to disk. ``quit`` discards the in-memory Session;
  ``output.csv`` and ``dataset/`` stay untouched.
* The LLM provider is never called. ``explain`` prints the explanation the
  solver already computed into the decision (the 50-call ceiling belongs to
  the batch run).
* Every interactive command builds the session's effective row and re-solves
  through ``code.solver.solve_request``; ``undo`` restores the original
  decision because the original inputs are re-solved, not replayed.
* Solver calls are wrapped: an exception prints a message and returns to the
  prompt instead of crashing the REPL.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tools.trace_view import DEFAULT_TRACE_DIR, TraceError, find_trace, load_trace, summarise

REPO_ROOT = Path(__file__).resolve().parent.parent

DECISION_FIELDS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

# `whatif` accepts short names; every alias resolves to a concrete row or
# profile field so the overlay stays plain data.
WHATIF_ALIASES = {
    "amount": "requested_amount",
    "date": "request_date",
    "floor": "minimum_balance_to_keep",
    "requested_amount": "requested_amount",
    "request_date": "request_date",
    "minimum_balance_to_keep": "minimum_balance_to_keep",
}
PROFILE_FIELDS = frozenset({"minimum_balance_to_keep"})
PRIVATE_PREFIX = "_"
APPLIED_KEY = "_applied_changes"

STATUSES = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
STATUS_STYLES = {
    "affordable_now": "green",
    "affordable_with_plan": "yellow",
    "affordable_later": "cyan",
    "not_affordable": "red",
}
METHODS = ("full_payment", "partial_payment", "installments", "wait", "not_recommended")
ATTENTION_LIMIT = 10
MANUAL_PREFIX = "manual_"

HELP_TEXT = """\
Commands:
  list [status]         table of requests; a status filter solves all first
  show <id>             decision panel + ledger + floor breaches + options
  dashboard             output.csv snapshot: counts, methods, needs attention
  new                   ask a new question for an existing user (session only)
  trace <id>            summarize that request's agent trace (read-only)
  whatif <id> k=v ...   set overrides, re-solve, print the before/after diff
  changes <id>          candidate actions with each one's projected effect
  apply <id> <action>   try one candidate in-session, re-solve, show delta
  diff <id>             original decision vs the current overlaid decision
  undo [<id>]           drop the overrides of one request, or of all
  explain <id>          print the decision_explanation in the decision
  ask <instruction>     run a plain-language task (multi-step, session only)
  help                  this text
  quit                  leave; nothing is written to disk

whatif fields:  amount=<N>  date=<YYYY-MM-DD>  floor=<N>
                (requested_amount, request_date, minimum_balance_to_keep)
actions:        stop:<event_id>  or  reduce_to:<event_id>:<new_amount>
`dashboard` reads the existing output.csv snapshot and never re-solves the
batch; `new` adds an in-memory manual_0001-style request and solves only that.
`trace` reads an agent trace file (never a live call) from --trace-dir
(default traces/latest) and never launches a browser.
Edits are session-only: output.csv and dataset/ are never written.

ask examples:
  ask can I afford 40,000 on the 12th
  ask why was request_NNN refused
  ask where am I leaking money, then tell me if I can still pay rent on the 3rd
  ask stop my delivery membership
  ask and if I wait until the 20th
Each clause becomes a step: the reply names what it did, what changed, what it
refused, and where the task trace was written."""


# ---------------------------------------------------------------------------
# Small text/number helpers
# ---------------------------------------------------------------------------

def _as_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.strip().lower() in {"nan", "nat"}:
        return ""
    return text


def _as_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def _decimal_or(value, default: Decimal) -> Decimal:
    number = _as_decimal(value)
    return default if number is None else number


def _money(value) -> str:
    text = _as_text(value)
    if not text:
        return ""
    number = _as_decimal(text)
    if number is None:
        return text
    return f"{number:,.2f}"


def _clip(value, width: int) -> str:
    text = _as_text(value)
    if len(text) <= width or width <= 4:
        return text
    return text[: width - 3] + "..."


def _without_nan(value):
    if isinstance(value, float) and value != value:
        return None
    return value


# ---------------------------------------------------------------------------
# Pure helpers targeted by tests/test_tui.py
# ---------------------------------------------------------------------------

def render_decision(row: dict) -> str:
    """Render the 8 output fields as one rich panel captured to plain text.

    `spending_changes_needed` is highlighted whenever it is not `none`; money
    is formatted; the string never touches stdout, so tests stay side-effect
    free.
    """
    source = row or {}
    body = Text()
    for field in DECISION_FIELDS:
        raw = _as_text(source.get(field))
        body.append(f"{field}: ", style="bold")
        if field == "amount_safe_to_pay":
            body.append(_money(raw), style="bold")
        elif field == "spending_changes_needed" and raw not in ("", "none"):
            body.append(raw, style="bold yellow")
        elif field == "affordability_status":
            body.append(raw, style=STATUS_STYLES.get(raw, ""))
        elif field == "payment_plan" and "|" in raw:
            for index, leg in enumerate(raw.split("|")):
                if index:
                    body.append("\n  ")
                body.append(leg)
        else:
            body.append(raw)
        body.append("\n")
    panel = Panel(
        body,
        title=_as_text(source.get("request_id")) or "decision",
        title_align="left",
        border_style="blue",
    )
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=100, force_terminal=False, highlight=False, markup=False
    )
    console.print(panel)
    return buffer.getvalue()


def read_output_snapshot(path, expected_ids):
    """Read the existing ``output.csv`` snapshot and validate it.

    Returns ``(rows, "")`` when the file covers exactly ``expected_ids`` and
    ``(None, message)`` otherwise. ``dashboard`` reports the message instead of
    fabricating a summary or solving the whole batch.
    """
    import pandas as pd

    snapshot = Path(path)
    name = snapshot.name or str(snapshot)
    if not snapshot.exists():
        return None, (
            f"no {name} snapshot at {snapshot}; run the pipeline first "
            "(dashboard never solves the full batch)."
        )
    try:
        frame = pd.read_csv(snapshot, dtype=str, keep_default_na=False)
    except Exception as exc:
        return None, (
            f"could not read {name}: {type(exc).__name__}: {exc}; "
            "run the pipeline to regenerate it."
        )
    if "request_id" not in frame.columns:
        return None, (
            f"{name} has no request_id column; run the pipeline to regenerate it."
        )
    written = {str(value) for value in frame["request_id"]}
    expected = {str(value) for value in expected_ids or ()}
    if written != expected or len(frame) != len(expected):
        missing = len(expected - written)
        unknown = len(written - expected)
        duplicate = max(len(frame) - len(written), 0)
        detail = f"{missing} missing, {unknown} unknown"
        if duplicate:
            detail += f", {duplicate} duplicate row(s)"
        return None, (
            f"{name} does not match the dataset request set ({detail}); "
            "it is a stale snapshot -- run the pipeline before using `dashboard`."
        )
    return frame.to_dict("records"), ""


def _attention_key(entry) -> tuple:
    amount = entry.get("requested_amount")
    return (
        amount is None,
        -(amount if amount is not None else Decimal("0")),
        str(entry.get("request_id", "")),
    )


def dashboard_summary(requests, decisions, limit: int = ATTENTION_LIMIT) -> dict:
    """Pure aggregation for the dashboard: counts plus a needs-attention list.

    ``requests`` supplies the requested amounts; ``decisions`` supplies the
    output fields. Rows without a decision are counted, never guessed.
    """
    by_id = {
        str(decision.get("request_id", "")): decision
        for decision in decisions or ()
        if decision
    }
    by_status = dict.fromkeys(STATUSES, 0)
    by_method = dict.fromkeys(METHODS, 0)
    spending_changes = 0
    zero_safe = 0
    fail_closed = 0
    missing = 0
    attention = []
    for request in requests or ():
        request_id = str(request.get("request_id", ""))
        decision = by_id.get(request_id)
        if decision is None:
            missing += 1
            continue
        status = _as_text(decision.get("affordability_status")).strip()
        method = _as_text(decision.get("recommended_payment_method")).strip()
        by_status[status] = by_status.get(status, 0) + 1
        by_method[method] = by_method.get(method, 0) + 1
        changes = _as_text(decision.get("spending_changes_needed")).strip()
        if changes and changes.lower() != "none":
            spending_changes += 1
        safe = _as_decimal(decision.get("amount_safe_to_pay"))
        if safe is not None and safe == 0:
            zero_safe += 1
        if status == "not_affordable" and method == "not_recommended" and (
            safe is None or safe == 0
        ):
            fail_closed += 1
        attention.append(
            {
                "request_id": request_id,
                "requested_amount": _as_decimal(request.get("requested_amount")),
                "amount_safe_to_pay": safe,
                "affordability_status": status,
                "recommended_payment_method": method,
            }
        )
    attention.sort(key=_attention_key)
    return {
        "total": len(requests or ()),
        "by_status": by_status,
        "by_method": by_method,
        "spending_changes": spending_changes,
        "zero_safe": zero_safe,
        "fail_closed": fail_closed,
        "missing_decisions": missing,
        "attention": attention[:limit],
        "attention_limit": limit,
    }


def render_dashboard(summary, source_label=None, manual_note=None) -> str:
    """Render a dashboard summary to plain text (no terminal required)."""
    source = summary or {}
    counts = Table(title="dashboard", show_header=False, box=None, pad_edge=False)
    counts.add_column("metric", style="bold")
    counts.add_column("value", justify="right")
    counts.add_column("detail")
    counts.add_row("requests", str(source.get("total", 0)), "")
    for status in STATUSES:
        counts.add_row(
            f"  {status}",
            str((source.get("by_status") or {}).get(status, 0)),
            "",
            style=STATUS_STYLES.get(status),
        )
    for method in METHODS:
        counts.add_row(
            f"  {method}", str((source.get("by_method") or {}).get(method, 0)), ""
        )
    counts.add_row(
        "spending changes",
        str(source.get("spending_changes", 0)),
        "rows with spending_changes_needed other than none",
    )
    counts.add_row(
        "zero-safe rows",
        str(source.get("zero_safe", 0)),
        f"fail-closed (not_affordable + not_recommended): {source.get('fail_closed', 0)}",
    )
    if source.get("missing_decisions"):
        counts.add_row(
            "no decision", str(source.get("missing_decisions", 0)), "not in the snapshot"
        )

    attention = Table(
        title=(
            f"needs attention (up to {source.get('attention_limit', ATTENTION_LIMIT)}, "
            "largest request first)"
        )
    )
    attention.add_column("request_id")
    attention.add_column("requested", justify="right")
    attention.add_column("safe", justify="right")
    attention.add_column("status")
    attention.add_column("method")
    for entry in source.get("attention") or ():
        attention.add_row(
            str(entry.get("request_id", "")),
            _money(entry.get("requested_amount")),
            _money(entry.get("amount_safe_to_pay")),
            str(entry.get("affordability_status", "")),
            str(entry.get("recommended_payment_method", "")),
            style=STATUS_STYLES.get(str(entry.get("affordability_status", ""))),
        )

    footer = Text()
    footer.append(source_label or "in-memory decisions", style="dim")
    if manual_note:
        footer.append("\n")
        footer.append(manual_note, style="yellow")
    panel = Panel(
        Group(counts, attention, footer),
        title="Buy or Wait? dashboard",
        title_align="left",
        border_style="blue",
    )
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=110, force_terminal=False, highlight=False, markup=False
    )
    console.print(panel)
    return buffer.getvalue()


def build_ledger_table(ledger, minimum_balance=None) -> Table:
    """91-day forecast table; every day below the floor is marked as a breach
    and the floor itself is drawn as its own row."""
    floor = _as_decimal(minimum_balance)
    table = Table(title="91-day forecast ledger")
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("date", no_wrap=True)
    table.add_column("balance", justify="right")
    table.add_column("note")
    for index, entry in enumerate(ledger or (), start=1):
        if isinstance(entry, dict):
            day = _as_text(entry.get("date")) or f"day {index}"
            raw_balance = entry.get("balance")
        else:
            day = f"day {index}"
            raw_balance = entry
        number = _as_decimal(raw_balance)
        below = floor is not None and number is not None and number < floor
        table.add_row(
            str(index),
            day,
            _money(raw_balance),
            "below floor" if below else "",
            style="bold red" if below else None,
        )
    if floor is not None:
        table.add_row("", "floor", _money(floor), "minimum_balance_to_keep", style="magenta")
    return table


def lookup_request(rows, request_id) -> dict | None:
    """Exact-id lookup; no fuzzy matching."""
    wanted = str(request_id)
    for row in rows or ():
        if row and str(row.get("request_id", "")) == wanted:
            return row
    return None


def apply_overrides(row, overrides) -> dict:
    """Return a new dict; the input row is never mutated."""
    merged = dict(row)
    merged.update(overrides or {})
    return merged


def diff_fields(original, current) -> dict:
    """Decision fields that changed: {field: (before, after)}."""
    base = original or {}
    other = current or {}
    changed = {}
    for key in dict.fromkeys([*DECISION_FIELDS, *base]):
        before = _without_nan(base.get(key))
        after = _without_nan(other.get(key))
        if before != after:
            changed[key] = (before, after)
    return changed


class Session:
    """In-memory overlay: one dict of field overrides per request_id.

    ``effective`` returns the original row object when no override applies,
    so undoing restores the exact inputs the solver saw originally.
    """

    def __init__(self) -> None:
        self.overrides: dict[str, dict] = {}

    def set(self, request_id, kv) -> None:
        self.overrides.setdefault(str(request_id), {}).update(kv)

    def effective(self, row):
        overlay = self.overrides.get(str(row.get("request_id", "")))
        if not overlay:
            return row
        return apply_overrides(row, overlay)

    def undo(self, request_id=None) -> None:
        if request_id is None:
            self.overrides.clear()
        else:
            self.overrides.pop(str(request_id), None)


# ---------------------------------------------------------------------------
# Spending-change helpers (all edits happen on copies)
# ---------------------------------------------------------------------------

def parse_action(action) -> tuple[str, str, str | None]:
    """`stop:<event_id>` / `reduce_to:<event_id>:<amount>` -> typed parts."""
    parts = str(action).split(":")
    if len(parts) == 2 and parts[0] == "stop" and parts[1]:
        return "stop", parts[1], None
    if len(parts) == 3 and parts[0] == "reduce_to" and parts[1] and parts[2]:
        number = _as_decimal(parts[2])
        if number is None or number < 0:
            raise ValueError(f"reduce amount must be a non-negative number, got {parts[2]!r}")
        return "reduce_to", parts[1], parts[2]
    raise ValueError(
        f"unrecognised action {action!r}; expected stop:<event_id> or reduce_to:<event_id>:<amount>"
    )


def edit_events(events, actions) -> list[dict]:
    """Copy events with each action's amount edit applied (never mutates)."""
    edited = [dict(e) for e in events]
    for action in actions or ():
        _kind, event_id, value = parse_action(action)
        new_amount = "0" if _kind == "stop" else str(value)
        for event in edited:
            if str(event.get("event_id", "")) == event_id:
                event["amount"] = new_amount
    return edited


def suppressed_categories(events, actions) -> set[str]:
    """Categories whose forward forecast a change removes (mirrors the
    solver's category-suppression mechanism)."""
    parsed = []
    for action in actions or ():
        try:
            kind, event_id, _value = parse_action(action)
        except ValueError:
            continue
        parsed.append((kind, event_id))
    wanted: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id", "") or "")
        for _kind, target in parsed:
            if event_id == target:
                category = str(event.get("category", "") or "").strip().lower()
                if category:
                    wanted.add(category)
                break
    return wanted


def project_actions(user_id, row, profile, events, fx_rows, actions, recurrence_flags) -> dict:
    """Project candidate spending changes through the solver's own oracle
    (copy edit + category suppression) -- no TUI-side decision logic."""
    from code.oracle import solve_safe_amount

    result = solve_safe_amount(
        user_id,
        str(row.get("request_date")),
        str(row.get("requested_amount")),
        profile,
        edit_events(events, actions),
        fx_rows,
        recurrence_flags,
        suppressed_categories=suppressed_categories(events, actions),
    )
    return {"safe_amount": result.get("safe_amount"), "earliest_date": result.get("earliest_date")}


# ---------------------------------------------------------------------------
# Dataset + solve plumbing (read-only)
# ---------------------------------------------------------------------------

class _Context(NamedTuple):
    row: dict
    profile: dict
    events: list
    applied: list
    user_id: str


def load_dataset(repo_root=None) -> dict:
    """Read-only dataset load mirroring code/main.py (repo-root anchored)."""
    import pandas as pd
    from code.evidence import resolve_event_amounts
    from code.main import configure_runtime

    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    requests = pd.read_csv(root / "dataset" / "requests.csv", dtype=str)
    profiles = pd.read_csv(root / "dataset" / "financial_profiles.csv", dtype=str)
    events = pd.read_csv(
        root / "dataset" / "financial_events.csv", dtype=str, keep_default_na=False
    ).to_dict("records")
    images = pd.read_csv(root / "dataset" / "images.csv", dtype=str, keep_default_na=False)
    fx = pd.read_csv(root / "dataset" / "exchange_rates.csv", dtype=str).to_dict("records")
    options = pd.read_csv(root / "dataset" / "request_payment_options.csv", dtype=str)
    messages = pd.read_csv(root / "dataset" / "messages.csv", dtype=str)
    ocr_path = configure_runtime()
    events, warnings = resolve_event_amounts(
        images, events, root / "dataset" / "media" / "images", ocr_available=ocr_path is not None
    )
    events_by_user: dict[str, list] = {}
    for event in events:
        events_by_user.setdefault(str(event.get("user_id", "") or ""), []).append(event)
    return {
        "requests": [dict(record) for record in requests.to_dict("records")],
        "profiles": {
            str(row["user_id"]): row.to_dict() for _, row in profiles.iterrows()
        },
        "events_by_user": events_by_user,
        "fx": fx,
        "options": options,
        "messages": messages,
        "warnings": warnings,
    }


def _solve(context: _Context, data: dict) -> dict:
    """Re-solve one request through the real solver (same gating as code.main)."""
    from code.main import _fail_closed_result, _validated_amount
    from code.solver import solve_request

    events = edit_events(context.events, context.applied)
    user_event_ids = {e.get("event_id") for e in context.events}
    user_warnings = [w for w in data["warnings"] if w.get("event_id") in user_event_ids]
    amounts_valid = all(_validated_amount(e.get("amount")) for e in events)
    if user_warnings or not amounts_valid:
        return _fail_closed_result(str(context.row.get("request_id", "")))
    return solve_request(
        str(context.row.get("request_id", "")),
        context.user_id,
        context.row.get("request_date"),
        context.row.get("requested_amount"),
        context.profile,
        events,
        data["fx"],
        data["options"],
        data["messages"],
        context.row.get("desired_completion_date"),
        context.row.get("allows_partial_payment"),
        installment_safety="strict",
    )


def _signature(session: Session, request_id: str) -> str:
    overlay = session.overrides.get(request_id) or {}
    return repr(sorted((str(key), repr(value)) for key, value in overlay.items()))


# ---------------------------------------------------------------------------
# The REPL
# ---------------------------------------------------------------------------

class TUI:
    def __init__(self, data=None, input_fn=input, console=None, trace_dir=None) -> None:
        self.data = data
        self.session = Session()
        self.input_fn = input_fn
        self.console = console if console is not None else Console(markup=False, highlight=False)
        # Read-only trace directory for `trace <id>`; nothing here writes to it.
        # Relative paths resolve against the repo root, like the dataset.
        if trace_dir is None:
            self.trace_dir = REPO_ROOT / "traces" / "latest"
        else:
            self.trace_dir = Path(trace_dir)
            if not self.trace_dir.is_absolute():
                self.trace_dir = REPO_ROOT / self.trace_dir
        # Decisions are cached per request keyed by that request's own overlay
        # signature, so edits to one request never invalidate another.
        self._cache: dict[str, tuple[str, dict]] = {}
        self._originals: dict[str, tuple[str, dict]] = {}
        # Session-only manual requests (llm-free, never written to disk).
        self._manual_ids: set[str] = set()
        self._manual_counter = 0
        # Task traces (`ask`) are written here; the request traces above stay
        # read-only, this directory is the only thing `ask` writes.
        self.task_trace_dir = REPO_ROOT / "traces" / "tasks"
        self._provider_notice = False

    # -- plumbing ----------------------------------------------------------

    def _ensure_data(self):
        if self.data is None:
            self.console.print("loading dataset (read-only) ...", style="dim")
            try:
                self.data = load_dataset()
            except Exception as exc:
                self.console.print(
                    f"dataset unavailable: {type(exc).__name__}: {exc}", style="red"
                )
                return None
        return self.data

    def _context(self, request_id, session=None) -> _Context | None:
        session = self.session if session is None else session
        data = self._ensure_data()
        if data is None:
            return None
        base = lookup_request(data["requests"], request_id)
        if base is None:
            self.console.print(f"unknown request_id {request_id!r} -- try `list`", style="red")
            return None
        overlay = session.effective(base)
        row = {
            key: value
            for key, value in overlay.items()
            if not str(key).startswith(PRIVATE_PREFIX)
        }
        applied = list(overlay.get(APPLIED_KEY) or [])
        user_id = str(base.get("user_id", "") or "")
        profile = dict(data["profiles"].get(user_id, {}))
        for key in list(row):
            if key in PROFILE_FIELDS:
                profile[key] = row.pop(key)
        return _Context(row, profile, data["events_by_user"].get(user_id, []), applied, user_id)

    def _cached_decision(self, request_id):
        entry = self._cache.get(request_id)
        if entry is None:
            return None
        signature, decision = entry
        if signature != _signature(self.session, request_id):
            return None
        return decision

    def _decide(self, request_id, session=None, cache=None):
        """Solve one request under a session; exceptions become messages."""
        session = self.session if session is None else session
        cache = self._cache if cache is None else cache
        context = self._context(request_id, session)
        if context is None:
            return None
        signature = _signature(session, request_id)
        entry = cache.get(request_id)
        if entry is not None and entry[0] == signature:
            return entry[1]
        try:
            decision = _solve(context, self.data)
        except Exception as exc:
            self.console.print(
                f"solver error for {request_id}: {type(exc).__name__}: {exc}", style="red"
            )
            return None
        cache[request_id] = (signature, decision)
        return decision

    def _one_id(self, args, command: str):
        if len(args) != 1:
            self.console.print(f"usage: {command} <request_id>", style="red")
            return None
        return args[0]

    def _print_diff(self, before, after) -> None:
        changed = diff_fields(before or {}, after or {})
        if not changed:
            self.console.print("no change in decision fields")
            return
        table = Table(title="decision diff")
        table.add_column("field")
        table.add_column("before")
        table.add_column("after")
        for field, (old, new) in changed.items():
            table.add_row(field, _clip(_as_text(old), 46), _clip(_as_text(new), 46))
        self.console.print(table)

    # -- commands ----------------------------------------------------------

    def manual_requests(self) -> list[str]:
        """IDs created by `new` in this session (never the dataset rows)."""
        requests = (self.data or {}).get("requests") or []
        return [
            str(request.get("request_id", ""))
            for request in requests
            if str(request.get("request_id", "")) in self._manual_ids
        ]

    def _next_manual_id(self, data) -> str:
        existing = {str(request.get("request_id", "")) for request in data["requests"]}
        index = self._manual_counter
        while True:
            index += 1
            candidate = f"{MANUAL_PREFIX}{index:04d}"
            if candidate not in existing:
                self._manual_counter = index
                return candidate

    def _prompt(self, label, default=None) -> str:
        hint = f" [{default}]" if default is not None else ""
        raw = self.input_fn(f"{label}{hint}: ")
        text = "" if raw is None else str(raw).strip()
        if not text and default is not None:
            return str(default)
        return text

    @staticmethod
    def _parse_date(value):
        try:
            return date.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _prompt_manual(self, data) -> dict | None:
        """Interactive prompt sequence; returns the row or None (no state change)."""
        user_id = self._prompt("user_id")
        if not user_id:
            self.console.print("user_id is required", style="red")
            return None
        if user_id not in data["profiles"]:
            known = sorted(data["profiles"])
            shown = ", ".join(known[:8]) + (" ..." if len(known) > 8 else "")
            self.console.print(
                f"unknown user {user_id!r} -- known users: {shown}", style="red"
            )
            return None
        raw_date = self._prompt("request_date (YYYY-MM-DD)")
        request_date = self._parse_date(raw_date)
        if request_date is None:
            self.console.print(
                f"request_date must be YYYY-MM-DD, got {raw_date!r}", style="red"
            )
            return None
        raw_amount = self._prompt("requested_amount")
        amount = _as_decimal(raw_amount)
        if amount is None or amount <= 0:
            self.console.print(
                f"requested_amount must be a positive decimal, got {raw_amount!r}",
                style="red",
            )
            return None
        completion = self._prompt(
            "desired_completion_date (YYYY-MM-DD)", default=request_date.isoformat()
        )
        completion_date = self._parse_date(completion)
        if completion_date is None:
            self.console.print(
                f"desired_completion_date must be YYYY-MM-DD, got {completion!r}",
                style="red",
            )
            return None
        if completion_date < request_date:
            self.console.print(
                f"desired_completion_date {completion_date.isoformat()} is before "
                f"request_date {request_date.isoformat()}; nothing created",
                style="red",
            )
            return None
        partial = self._prompt("allows_partial_payment y/N", default="n").lower()
        request_type = self._prompt("request_type", default="other") or "other"
        request_text = self._prompt("request_text (optional)")
        return {
            "user_id": user_id,
            "request_date": request_date.isoformat(),
            "request_type": request_type,
            "requested_amount": str(amount),
            "desired_completion_date": completion_date.isoformat(),
            "allows_partial_payment": "true"
            if partial in ("y", "yes", "true")
            else "false",
            "request_text": request_text,
        }

    def cmd_help(self, args) -> None:
        self.console.print(HELP_TEXT)

    def cmd_list(self, args) -> None:
        data = self._ensure_data()
        if data is None:
            return
        requests = data["requests"]
        token = args[0] if args else None
        solving = token is not None
        filter_status = None
        if token is not None and token.lower() not in ("all", "*"):
            filter_status = token.lower()
            if filter_status not in STATUSES:
                self.console.print(
                    f"unknown status {token!r}; known: {', '.join(STATUSES)} -- showing all",
                    style="yellow",
                )
                filter_status = None
        entries = []
        if solving:
            self.console.print(
                f"solving {len(requests)} requests for affordability_status "
                f"(cached until this session's edits change) ...",
                style="dim",
            )
            for index, request in enumerate(requests, start=1):
                request_id = str(request.get("request_id", ""))
                entries.append((request, self._decide(request_id)))
                if index % 50 == 0:
                    self.console.print(f"  solved {index}/{len(requests)}", style="dim")
        else:
            for request in requests:
                request_id = str(request.get("request_id", ""))
                entries.append((request, self._cached_decision(request_id)))
        if filter_status:
            entries = [
                (request, decision)
                for request, decision in entries
                if decision and str(decision.get("affordability_status")) == filter_status
            ]
        title = "requests" + (f" -- {filter_status}" if filter_status else "")
        table = Table(title=title)
        if solving:
            table.add_column("request_id")
            table.add_column("status")
            table.add_column("method")
            table.add_column("safe", justify="right")
            table.add_column("earliest")
            table.add_column("changes")
            for request, decision in entries:
                request_id = str(request.get("request_id", ""))
                if decision is None:
                    table.add_row(request_id, "solve error", "", "", "", "", style="dim")
                    continue
                status = str(decision.get("affordability_status", ""))
                table.add_row(
                    request_id,
                    status,
                    _as_text(decision.get("recommended_payment_method")),
                    _money(decision.get("amount_safe_to_pay")),
                    _as_text(decision.get("earliest_date_for_full_payment")),
                    _clip(decision.get("spending_changes_needed"), 36),
                    style=STATUS_STYLES.get(status),
                )
        else:
            table.add_column("request_id")
            table.add_column("user")
            table.add_column("date")
            table.add_column("amount", justify="right")
            table.add_column("type")
            table.add_column("status")
            for request, decision in entries:
                request_id = str(request.get("request_id", ""))
                marker = "*" if request_id in self.session.overrides else ""
                status = str(decision.get("affordability_status", "")) if decision else ""
                table.add_row(
                    marker + request_id,
                    _as_text(request.get("user_id")),
                    _as_text(request.get("request_date")),
                    _money(request.get("requested_amount")),
                    _as_text(request.get("request_type")),
                    status,
                )
        self.console.print(table)
        if not solving and not any(decision for _request, decision in entries):
            self.console.print(
                "no decisions computed yet -- `list <status>` (e.g. list affordable_now) "
                "solves all requests, `show <id>` solves one.",
                style="dim",
            )

    def cmd_dashboard(self, args) -> None:
        if args:
            self.console.print("usage: dashboard", style="red")
            return
        data = self._ensure_data()
        if data is None:
            return
        baseline = [
            request
            for request in data["requests"]
            if str(request.get("request_id", "")) not in self._manual_ids
        ]
        rows, message = read_output_snapshot(
            REPO_ROOT / "output.csv",
            [request.get("request_id", "") for request in baseline],
        )
        manual_ids = self.manual_requests()
        manual_note = ""
        if manual_ids:
            manual_note = (
                f"session-only manual requests: {len(manual_ids)} "
                f"({', '.join(manual_ids)}) -- not in output.csv"
            )
        if rows is None:
            self.console.print(message, style="yellow")
            if manual_note:
                self.console.print(manual_note, style="yellow")
                self._print_manual_table(manual_ids)
            return
        summary = dashboard_summary(baseline, rows)
        self.console.print(
            render_dashboard(
                summary,
                source_label=(
                    f"output.csv snapshot ({len(rows)} rows) -- "
                    "not recomputed in this session"
                ),
                manual_note=manual_note or None,
            ),
            soft_wrap=True,
        )
        if manual_ids:
            self._print_manual_table(manual_ids)

    def _print_manual_table(self, manual_ids) -> None:
        table = Table(title="session-only manual requests")
        table.add_column("request_id")
        table.add_column("user")
        table.add_column("date")
        table.add_column("requested", justify="right")
        table.add_column("status")
        table.add_column("method")
        for request_id in manual_ids:
            request = lookup_request(self.data.get("requests") or [], request_id) or {}
            decision = self._cached_decision(request_id) or {}
            status = _as_text(decision.get("affordability_status"))
            table.add_row(
                request_id,
                _as_text(request.get("user_id")),
                _as_text(request.get("request_date")),
                _money(request.get("requested_amount")),
                status or "not solved",
                _as_text(decision.get("recommended_payment_method")),
                style=STATUS_STYLES.get(status),
            )
        self.console.print(table)

    def cmd_new(self, args) -> None:
        if args:
            self.console.print(
                "usage: new   (interactive; the request stays in this session)", style="red"
            )
            return
        data = self._ensure_data()
        if data is None:
            return
        try:
            row = self._prompt_manual(data)
        except (EOFError, KeyboardInterrupt):
            self.console.print("new cancelled -- no request created", style="yellow")
            return
        if row is None:
            return
        request_id = self._next_manual_id(data)
        row = dict(row, request_id=request_id)
        # The only place the in-memory request list is extended.
        data["requests"].append(row)
        self._manual_ids.add(request_id)
        self.console.print(
            f"created {request_id}: session-only, no CSV was written", style="green"
        )
        decision = self._decide(request_id)
        if decision is None:
            self.console.print(
                "the manual request is listed but has no decision; `undo` does not remove it",
                style="yellow",
            )
            return
        self.console.print(render_decision(decision), soft_wrap=True)

    def _render_task_trace(self, path) -> bool:
        """Print a task trace; ``False`` when the file is not one."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict) or not payload.get("task_id"):
            return False
        table = Table(
            title=f"task {payload['task_id']}",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        table.add_column("field", style="bold")
        table.add_column("value")
        table.add_row("instruction", str(payload.get("instruction") or ""))
        table.add_row("summary", str(payload.get("summary") or ""))
        steps = payload.get("steps") or []
        table.add_row("steps", str(len(steps)))
        self.console.print(table)
        for step in steps:
            if not isinstance(step, dict):
                continue
            intent = (step.get("data") or {}).get("intent") or {}
            source = intent.get("source") if isinstance(intent, dict) else None
            suffix = f" (intent {intent.get('intent')}, {source})" if source else ""
            self.console.print(
                f"  - {step.get('kind')}: {step.get('status')}{suffix}: "
                f"{step.get('text') or ''}",
                style="dim",
            )
        actions = payload.get("actions_applied") or []
        if actions:
            self.console.print(f"  actions applied: {', '.join(map(str, actions))}")
        concerns = payload.get("concerns") or []
        if concerns:
            self.console.print(f"  concerns: {len(concerns)}")
        return True

    def cmd_trace(self, args) -> None:
        """Read-only summary of one agent trace file (never a live call)."""
        request_id = self._one_id(args, "trace")
        if request_id is None:
            return
        path = find_trace(self.trace_dir, request_id)
        if path is None:
            # `ask` writes its own task traces; `trace task_...` should find them.
            path = find_trace(self.task_trace_dir, request_id)
        if path is None:
            self.console.print(
                f"no trace for {request_id} under {self.trace_dir}; run the agent with "
                "--trace-dir DIR, or point the TUI at it with "
                "`python -m tools.tui --trace-dir DIR`",
                style="yellow",
            )
            return
        try:
            trace = load_trace(path)
        except TraceError as exc:
            # Task traces are not request traces: they carry `task_id` and an
            # ordered step list instead of a request row. Render those directly.
            if self._render_task_trace(path):
                return
            self.console.print(f"trace unreadable: {exc}", style="red")
            return
        summary = summarise(trace)
        status = summary["status"]
        if summary["candidate"] is None:
            candidate = "none proposed (the deterministic row stands)"
        else:
            candidate = (
                f"{'valid' if summary['candidate_valid'] else 'invalid'}, "
                f"agreement {'yes' if summary['agreement'] else 'no'}, "
                f"rationale from {summary['rationale_source']}"
            )
        table = Table(
            title=f"trace {request_id}", show_header=False, box=None, pad_edge=False
        )
        table.add_column("field", style="bold")
        table.add_column("value")
        table.add_row("run id", summary["run_id"] or "unknown")
        table.add_row("provider / model", f"{summary['provider']} / {summary['model']}")
        table.add_row("model calls", str(summary["model_calls"]))
        table.add_row(
            "tokens", "not recorded" if summary["tokens"] is None else str(summary["tokens"])
        )
        table.add_row(
            "final status",
            status or "not written",
            style=STATUS_STYLES.get(status),
        )
        table.add_row("method", summary["method"] or "not written")
        table.add_row("candidate", candidate)
        table.add_row("fallback", summary["fallback_reason"] or "none")
        table.add_row("tool calls", ", ".join(summary["tool_names"]) or "none")
        self.console.print(table)
        self.console.print(f"trace file: {path}", soft_wrap=True)
        if summary["differences"]:
            diff = Table(title="candidate vs final")
            diff.add_column("field")
            diff.add_column("candidate")
            diff.add_column("final")
            for item in summary["differences"]:
                diff.add_row(
                    _clip(item.get("field"), 28),
                    _clip(item.get("candidate"), 40) or "(empty)",
                    _clip(item.get("final"), 40) or "(empty)",
                )
            self.console.print(diff)
        elif summary["candidate"] is not None:
            self.console.print("candidate matches the final row on every machine field")

    def cmd_show(self, args) -> None:
        request_id = self._one_id(args, "show")
        if not request_id:
            return
        decision = self._decide(request_id)
        if decision is None:
            return
        self.console.print(render_decision(decision), soft_wrap=True)
        overlay = self.session.overrides.get(request_id)
        if overlay:
            self.console.print(f"session overlay: {overlay}", style="yellow")
        context = self._context(request_id)
        if context is None:
            return
        try:
            ledger, floor, breaches = self._ledger(context)
            self.console.print(build_ledger_table(ledger, minimum_balance=floor))
            if floor is not None:
                self.console.print(
                    f"{breaches} of {len(ledger)} forecast days below the floor ({_money(floor)})"
                )
        except Exception as exc:
            self.console.print(
                f"ledger unavailable: {type(exc).__name__}: {exc}", style="red"
            )
        try:
            table = self._options_table(request_id, context.profile)
            if table is None:
                self.console.print("no payment option applies to this request")
            else:
                self.console.print(table)
        except Exception as exc:
            self.console.print(
                f"options unavailable: {type(exc).__name__}: {exc}", style="red"
            )

    def _ledger(self, context: _Context):
        """91-day forecast for the session's effective inputs: same oracle
        call the solver makes, with the session's applied changes."""
        from code.oracle import solve_safe_amount
        from code.recurrence import detect_recurrence

        data = self.data
        events = [dict(e) for e in context.events]
        flags = detect_recurrence(events)
        result = solve_safe_amount(
            context.user_id,
            str(context.row.get("request_date")),
            str(context.row.get("requested_amount")),
            context.profile,
            edit_events(events, context.applied),
            data["fx"],
            flags,
            suppressed_categories=suppressed_categories(events, context.applied),
        )
        floor = _as_decimal(context.profile.get("minimum_balance_to_keep"))
        start = date.fromisoformat(str(context.row.get("request_date")).strip())
        rows = []
        breaches = 0
        for index, balance in enumerate(result.get("ledger") or []):
            number = _as_decimal(balance)
            if floor is not None and number is not None and number < floor:
                breaches += 1
            rows.append(
                {"date": (start + timedelta(days=index)).isoformat(), "balance": balance}
            )
        return rows, floor, breaches

    def _options_table(self, request_id, profile):
        from code.solver import get_valid_options

        options = get_valid_options(request_id, profile, self.data["options"])
        if not options:
            return None
        table = Table(title="available payment options")
        table.add_column("option")
        table.add_column("method")
        table.add_column("amount", justify="right")
        table.add_column("payments", justify="right")
        table.add_column("first date")
        table.add_column("total", justify="right")
        table.add_column("plan")
        for option in options:
            table.add_row(
                _as_text(option.get("option_id")),
                _as_text(option.get("payment_method")),
                _money(option.get("amount")),
                _as_text(option.get("number_of_payments")),
                _as_text(option.get("first_payment_date")),
                _money(option.get("total_payable")),
                _clip(option.get("payment_plan"), 40),
            )
        return table

    def cmd_whatif(self, args) -> None:
        if len(args) < 2:
            self.console.print(
                "usage: whatif <request_id> amount=<N> date=<YYYY-MM-DD> floor=<N>",
                style="red",
            )
            return
        request_id = args[0]
        pairs = []
        for token in args[1:]:
            if "=" not in token:
                self.console.print(f"expected key=value, got {token!r}", style="red")
                return
            raw_key, _sep, value = token.partition("=")
            key = WHATIF_ALIASES.get(raw_key.strip().lower())
            if key is None:
                self.console.print(
                    f"unknown field {raw_key!r}; use amount=, date=, floor=", style="red"
                )
                return
            if key in ("requested_amount", "minimum_balance_to_keep"):
                number = _as_decimal(value)
                if number is None or number < 0:
                    self.console.print(
                        f"{key} must be a non-negative number, got {value!r}", style="red"
                    )
                    return
            elif key == "request_date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    self.console.print(f"date must be YYYY-MM-DD, got {value!r}", style="red")
                    return
            pairs.append((key, value))
        if self._context(request_id) is None:
            return
        before = self._decide(request_id)
        self.session.set(request_id, dict(pairs))
        after = self._decide(request_id)
        self.console.print(
            "override for "
            f"{request_id}: {', '.join(f'{k}={v}' for k, v in pairs)} (session only)"
        )
        if after is None:
            self.console.print(
                "re-solve failed; the override is kept -- `undo` reverts it", style="yellow"
            )
            return
        self._print_diff(before, after)

    def cmd_changes(self, args) -> None:
        request_id = self._one_id(args, "changes")
        if not request_id:
            return
        context = self._context(request_id)
        if context is None:
            return
        requested = _as_decimal(context.row.get("requested_amount"))
        if requested is None:
            self.console.print(
                "requested_amount is not numeric; undo that override first", style="red"
            )
            return
        from code.changes import get_candidates, search_changes
        from code.recurrence import detect_recurrence

        events = [dict(e) for e in context.events]
        flags = detect_recurrence(events)
        self.console.print("projecting candidates through the solver's oracle ...", style="dim")
        baseline = project_actions(
            context.user_id, context.row, context.profile, events, self.data["fx"], [], flags
        )
        baseline_safe = _decimal_or(baseline.get("safe_amount"), Decimal("0"))
        decision = self._decide(request_id)
        summary = f"baseline safe {_money(baseline_safe)}"
        if decision is not None:
            summary = (
                f"current decision {decision.get('affordability_status')} via "
                f"{decision.get('recommended_payment_method')}; {summary}"
            )
        self.console.print(summary)
        recommended = []
        try:
            recommended = search_changes(
                context.user_id,
                str(context.row.get("request_date")),
                requested,
                context.profile,
                events,
                self.data["fx"],
                flags,
            )
        except Exception as exc:
            self.console.print(
                f"search_changes failed: {type(exc).__name__}: {exc}", style="red"
            )
        rows = []
        if recommended:
            combined = "|".join(recommended)
            self.console.print(f"search_changes safe set: {combined}")
            rows.append(
                ("safe set", combined, project_actions(
                    context.user_id, context.row, context.profile, events,
                    self.data["fx"], recommended, flags,
                ))
            )
        else:
            self.console.print("search_changes found no safe change set")
        seen = set(recommended)
        for event_id, action, value in get_candidates(events):
            text = f"{action}:{event_id}" if action == "stop" else f"reduce_to:{event_id}:{value}"
            if text in seen:
                continue
            seen.add(text)
            rows.append(
                ("candidate", text, project_actions(
                    context.user_id, context.row, context.profile, events,
                    self.data["fx"], [text], flags,
                ))
            )
        if not rows:
            self.console.print("no stoppable/reducible candidates for this user")
            return
        safe_sets = [entry for entry in rows if entry[0] == "safe set"]
        candidates = sorted(
            (entry for entry in rows if entry[0] == "candidate"),
            key=lambda entry: _decimal_or(entry[2].get("safe_amount"), Decimal("-1")),
            reverse=True,
        )
        table = Table(title=f"candidate spending changes for {request_id}")
        table.add_column("action")
        table.add_column("projected safe", justify="right")
        table.add_column("vs baseline", justify="right")
        table.add_column("earliest")
        table.add_column("note")
        for kind, text, projection in safe_sets + candidates:
            safe = _decimal_or(projection.get("safe_amount"), Decimal("0"))
            delta = safe - baseline_safe
            label = f"{text}  [safe set]" if kind == "safe set" else text
            table.add_row(
                label,
                _money(safe),
                _money(delta),
                _as_text(projection.get("earliest_date")) or "none",
                "covers request" if safe >= requested else "",
            )
        self.console.print(table)

    def cmd_apply(self, args) -> None:
        if len(args) != 2:
            self.console.print(
                "usage: apply <request_id> <stop:event_id|reduce_to:event_id:amount>",
                style="red",
            )
            return
        request_id, action_token = args
        context = self._context(request_id)
        if context is None:
            return
        actions = [part for part in action_token.split("|") if part]
        if not actions:
            self.console.print("no action given", style="red")
            return
        try:
            for action in actions:
                parse_action(action)
        except ValueError as exc:
            self.console.print(str(exc), style="red")
            return
        fresh = [action for action in actions if action not in context.applied]
        if not fresh:
            self.console.print(f"already applied to {request_id}: {action_token}")
            return
        before = self._decide(request_id)
        try:
            from code.changes import search_changes
            from code.recurrence import detect_recurrence

            events = [dict(e) for e in context.events]
            flags = detect_recurrence(events)
            recommended = search_changes(
                context.user_id,
                str(context.row.get("request_date")),
                _decimal_or(context.row.get("requested_amount"), Decimal("0")),
                context.profile,
                events,
                self.data["fx"],
                flags,
            )
            if recommended:
                self.console.print(
                    f"search_changes currently recommends: {'|'.join(recommended)}",
                    style="dim",
                )
        except Exception as exc:
            self.console.print(
                f"search_changes unavailable: {type(exc).__name__}: {exc}", style="red"
            )
        self.session.set(request_id, {APPLIED_KEY: list(context.applied) + fresh})
        after = self._decide(request_id)
        self.console.print(
            f"applied {', '.join(fresh)} to {request_id} in-session (undo reverts it)"
        )
        if after is None:
            self.console.print(
                "re-solve failed; the change is kept -- `undo` reverts it", style="yellow"
            )
            return
        self._print_diff(before, after)

    def cmd_diff(self, args) -> None:
        request_id = self._one_id(args, "diff")
        if not request_id:
            return
        original = self._decide(request_id, session=Session(), cache=self._originals)
        current = self._decide(request_id)
        if original is None and current is None:
            return
        overlay = self.session.overrides.get(request_id)
        if overlay:
            self.console.print(f"active overrides for {request_id}: {overlay}", style="yellow")
        else:
            self.console.print(f"no active overrides for {request_id}")
        self._print_diff(original, current)

    def cmd_undo(self, args) -> None:
        if len(args) > 1:
            self.console.print("usage: undo [<request_id>]", style="red")
            return
        if not args:
            self.session.undo()
            self.console.print("cleared all session overrides and applied changes")
            return
        request_id = args[0]
        if request_id not in self.session.overrides:
            self.console.print(f"nothing to undo for {request_id}")
            return
        self.session.undo(request_id)
        self.console.print(
            f"undo {request_id}: overrides dropped, the original decision is restored",
            style="green",
        )
        if self.data is not None:
            decision = self._decide(request_id)
            if decision is not None:
                self.console.print(render_decision(decision), soft_wrap=True)

    def cmd_explain(self, args) -> None:
        request_id = self._one_id(args, "explain")
        if not request_id:
            return
        decision = self._decide(request_id)
        if decision is None:
            return
        explanation = _as_text(decision.get("decision_explanation"))
        self.console.print(
            explanation or "(the computed decision carries no explanation)", style="italic"
        )

    def cmd_ask(self, args) -> None:
        """Plain-language task over the same session (multi-step, session only)."""
        instruction = " ".join(args).strip()
        if not instruction:
            self.console.print(
                "usage: ask <instruction>, e.g. "
                "`ask can I afford 40,000 on the 12th`",
                style="red",
            )
            return
        data = self._ensure_data()
        if data is None:
            return
        from code.task_agent import Workspace, run_task

        provider = None
        try:
            from code.providers import build_provider

            provider, provider_reason = build_provider()
        except Exception as exc:  # offline stays the default
            provider, provider_reason = None, f"{type(exc).__name__}: {exc}"
        if provider is None and provider_reason and not self._provider_notice:
            self._provider_notice = True
            self.console.print(
                f"no model configured ({provider_reason}); classifying with the rules",
                style="dim",
            )
        workspace = Workspace(data=data, session=self.session)
        result = run_task(
            instruction,
            workspace=workspace,
            trace_dir=self.task_trace_dir,
            provider=provider,
        )
        # The task engine solves through the same kernel, so keep the TUI's
        # per-request cache hot with what it just computed.
        self._cache.update(
            {
                key: value
                for key, value in workspace._cache.items()
                if not key.startswith(MANUAL_PREFIX)
            }
        )
        for index, step in enumerate(result.steps, start=1):
            label = f"[{index}/{len(result.steps)}] {step.kind}"
            reading = (step.data or {}).get("intent") or {}
            if reading.get("intent"):
                label += (
                    f"  (intent: {reading.get('intent')}, "
                    f"{reading.get('request_kind', 'ambiguous')} via "
                    f"{reading.get('source', 'deterministic_fallback')})"
                )
            style = {
                "ok": "green",
                "refused": "yellow",
                "unknown": "yellow",
                "error": "red",
            }.get(step.status, None)
            self.console.print(f"{label}: {step.text}", style="bold")
            self.console.print(step.detail or "(no detail)", style=style, soft_wrap=True)
        if result.actions_applied:
            applied = ", ".join(
                f"{item['action']} -> {item['request_id']}"
                for item in result.actions_applied
            )
            self.console.print(f"applied in this session: {applied} (`undo` reverts)")
        else:
            self.console.print("nothing was changed in this session")
        if result.trace_path:
            self.console.print(f"task trace: {result.trace_path}", style="dim")


def _reconfigure_stdout() -> None:
    """Keep box-drawing characters intact when stdout is not UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def run_tui(data=None, input_fn=input, trace_dir=None) -> None:
    """The REPL. Returns None; writes nothing to disk."""
    _reconfigure_stdout()
    app = TUI(data=data, input_fn=input_fn, trace_dir=trace_dir)
    console = app.console
    console.print("Buy or Wait? interactive TUI", style="bold")
    console.print(
        "session-only: edits live in memory; output.csv and dataset/ are never written."
    )
    console.print("type 'help' for commands, 'quit' to exit")
    handlers = {
        "list": app.cmd_list,
        "show": app.cmd_show,
        "dashboard": app.cmd_dashboard,
        "new": app.cmd_new,
        "trace": app.cmd_trace,
        "whatif": app.cmd_whatif,
        "changes": app.cmd_changes,
        "apply": app.cmd_apply,
        "diff": app.cmd_diff,
        "undo": app.cmd_undo,
        "explain": app.cmd_explain,
        "ask": app.cmd_ask,
        "help": app.cmd_help,
    }
    while True:
        try:
            line = input_fn("tui> ")
        except (EOFError, KeyboardInterrupt):
            console.print("")
            break
        line = (line or "").strip()
        if not line:
            continue
        parts = line.split()
        command, args = parts[0].lower(), parts[1:]
        if command in ("quit", "exit", "q"):
            break
        handler = handlers.get(command)
        if handler is None:
            console.print(f"unknown command {command!r}; type 'help'", style="red")
            continue
        try:
            handler(args)
        except Exception as exc:
            console.print(f"error: {type(exc).__name__}: {exc}", style="red")
    console.print("bye -- session discarded; output.csv and dataset/ untouched.")


def main(argv=None) -> int:
    """CLI entry point: ``--help`` works without starting the REPL."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.tui",
        description=(
            "Buy or Wait? interactive TUI. Session-only: it never writes "
            "output.csv, dataset/ or any trace."
        ),
    )
    parser.add_argument(
        "--trace-dir",
        metavar="DIR",
        default=DEFAULT_TRACE_DIR,
        help=(
            "directory the read-only `trace <request_id>` command searches "
            f"(default: {DEFAULT_TRACE_DIR}; relative paths resolve against the "
            "repo root)"
        ),
    )
    args = parser.parse_args(argv)
    run_tui(trace_dir=args.trace_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
