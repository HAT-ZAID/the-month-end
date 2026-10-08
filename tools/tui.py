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

import io
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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

HELP_TEXT = """\
Commands:
  list [status]         table of requests; a status filter solves all first
  show <id>             decision panel + ledger + floor breaches + options
  whatif <id> k=v ...   set overrides, re-solve, print the before/after diff
  changes <id>          candidate actions with each one's projected effect
  apply <id> <action>   try one candidate in-session, re-solve, show delta
  diff <id>             original decision vs the current overlaid decision
  undo [<id>]           drop the overrides of one request, or of all
  explain <id>          print the decision_explanation in the decision
  help                  this text
  quit                  leave; nothing is written to disk

whatif fields:  amount=<N>  date=<YYYY-MM-DD>  floor=<N>
                (requested_amount, request_date, minimum_balance_to_keep)
actions:        stop:<event_id>  or  reduce_to:<event_id>:<new_amount>
Edits are session-only: output.csv and dataset/ are never written."""


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
    def __init__(self, data=None, input_fn=input, console=None) -> None:
        self.data = data
        self.session = Session()
        self.input_fn = input_fn
        self.console = console if console is not None else Console(markup=False, highlight=False)
        # Decisions are cached per request keyed by that request's own overlay
        # signature, so edits to one request never invalidate another.
        self._cache: dict[str, tuple[str, dict]] = {}
        self._originals: dict[str, tuple[str, dict]] = {}

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


def _reconfigure_stdout() -> None:
    """Keep box-drawing characters intact when stdout is not UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def run_tui(data=None, input_fn=input) -> None:
    """The REPL. Returns None; writes nothing to disk."""
    _reconfigure_stdout()
    app = TUI(data=data, input_fn=input_fn)
    console = app.console
    console.print("Buy or Wait? interactive TUI", style="bold")
    console.print(
        "session-only: edits live in memory; output.csv and dataset/ are never written."
    )
    console.print("type 'help' for commands, 'quit' to exit")
    handlers = {
        "list": app.cmd_list,
        "show": app.cmd_show,
        "whatif": app.cmd_whatif,
        "changes": app.cmd_changes,
        "apply": app.cmd_apply,
        "diff": app.cmd_diff,
        "undo": app.cmd_undo,
        "explain": app.cmd_explain,
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


if __name__ == "__main__":
    run_tui()
