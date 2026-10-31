"""Conversational task engine over the deterministic kernel.

The batch path answers one question per request in ``dataset/requests.csv``.
This module answers the other shape of request: a person typing an instruction.

    can I afford 40,000 on the 12th?
    stop my streaming subscription
    where am I leaking money, then tell me if I can still pay rent on the 3rd
    and if I wait until the 20th?

Design rules, in order of importance:

* **The kernel owns every number.** Each step re-solves through
  ``code.solver`` / ``code.oracle`` exactly like ``tools.tui``; nothing here does
  its own cash-flow arithmetic.
* **Session only.** Edits live in an in-memory overlay and ``undo`` restores the
  original decision, because the original inputs are re-solved rather than
  replayed. ``output.csv`` and ``dataset/`` are never written.
* **Deterministic first.** Instruction parsing is rule based today. Model intent
  detection (``code/intent.py``) plugs in beside :func:`parse_instruction` and
  falls back to it, so behaviour never depends on a model being reachable.
* **Refuse out loud.** A step that cannot be carried out says so and, when it
  can, names the nearest safe alternative instead of failing silently.

Shared plumbing (dataset loading, event editing, the solver call) lives in
``tools.tui``; it is imported lazily so this module keeps working even where the
TUI's rich console is not needed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from code.amounts import parse_amount, to_decimal
from code.intent import detect_intent, deterministic_intent
from code.trace import sanitize

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_DIR = REPO_ROOT / "traces" / "tasks"
MAX_STEPS = 8
# How many requests the default-target scan is allowed to solve.
DEFAULT_SCAN = 40
# A local model can take a minute to cold-load before it answers; the intent
# classifier must not be cut off at a network-request timeout.
INTENT_TIMEOUT_ENV = 'BUY_WAIT_INTENT_TIMEOUT'
DEFAULT_INTENT_TIMEOUT = 120.0

STATUS_OK = "ok"
STATUS_REFUSED = "refused"
STATUS_UNKNOWN = "unknown"
STATUS_ERROR = "error"

# --- instruction grammar ---------------------------------------------------

_CLAUSE_SPLIT = re.compile(r"(?:;|\bthen\b|\band then\b|\bafter that\b)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_ORDINAL = re.compile(
    r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE
)
_MONTH_DAY = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
_REQUEST_ID = re.compile(r"\brequest[_\s-]?(\d+)\b", re.IGNORECASE)
# "waits 30 days" is a waiting period, not an amount.  Strip it before the
# money reader runs, or the number lands in requested_amount and the answer
# describes a different question.
_DURATION = re.compile(
    r"\b\d+\s*(?:day|days|week|weeks|month|months|year|years)\b", re.IGNORECASE
)
_EVENT_ID = re.compile(r"\bevent[_\s-]?(\d+)\b", re.IGNORECASE)
_MONEY = re.compile(r"(?<![\w.])(\d[\d.,]*\d|\d)(?![\w])")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_UNDO = re.compile(r"^(?:please\s+)?(?:undo|revert|clear|reset)\b", re.IGNORECASE)
_REDUCE = re.compile(r"\breduce\b", re.IGNORECASE)
_STOP = re.compile(r"\b(?:stop|kill|cancel)\b", re.IGNORECASE)
_CHANGES = re.compile(
    r"\b(?:changes?|candidates?|leaks?|leaking|gut\s+check)\b", re.IGNORECASE
)
_EXPLAIN = re.compile(r"\b(?:why|explain|justify|how\s+come)\b", re.IGNORECASE)
_WHATIF = re.compile(
    r"\b(?:what\s*if|if\s+i\s+(?:wait|pay|spend|delay)|wait\s+until|until\s+the)\b",
    re.IGNORECASE,
)
_AFFORD = re.compile(
    r"\b(?:afford|can\s+i\s+(?:pay|buy|spend|transfer)|should\s+i\s+(?:pay|buy)|pay)\b",
    re.IGNORECASE,
)


def _tui():
    """The TUI module, imported lazily (it owns the shared kernel plumbing)."""
    from tools import tui

    return tui


# --- data types ------------------------------------------------------------


@dataclass
class StepSpec:
    """One instruction clause, before it is executed."""

    kind: str
    text: str
    params: dict = field(default_factory=dict)


@dataclass
class StepResult:
    """What one step actually did."""

    kind: str
    text: str
    status: str
    detail: str = ""
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "status": self.status,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass
class TaskResult:
    """The whole task: what was asked, what ran, what changed."""

    task_id: str
    instruction: str
    steps: list = field(default_factory=list)
    summary: str = ""
    trace_path: str | None = None
    actions_applied: list = field(default_factory=list)
    concerns: list = field(default_factory=list)
    changed: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(
            step.status in (STATUS_OK, STATUS_REFUSED) for step in self.steps
        )

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "instruction": self.instruction,
            "steps": [step.as_dict() for step in self.steps],
            "summary": self.summary,
            "trace_path": self.trace_path,
            "actions_applied": list(self.actions_applied),
            "concerns": list(self.concerns),
            "changed": list(self.changed),
        }


class UnknownTarget(ValueError):
    """A request/event id the loaded dataset does not contain."""


# --- small parsers ---------------------------------------------------------


def _money_value(token, currency: str = "", lang: str = "") -> Decimal | None:
    """'40,000' / 'IDR 8 000' / '1500.50' -> Decimal, or None.

    ``parse_amount`` is the locale-aware reader and it needs a currency to
    resolve an ambiguous separator, so the caller passes the home currency when
    it knows it.  Without one, the mixed-separator rule still reads EU-style
    input correctly, and the naive separator strip is only the last resort.
    """
    text = str(token or "").strip().replace(" ", "")
    if not text:
        return None
    value = parse_amount(text, currency, lang)
    if value is None:
        value = to_decimal(text.replace(",", ""))
    if value is None or not value.is_finite():
        return None
    return value


def _strip_tokens(text: str) -> str:
    """Remove dates and ids so their digits are not mistaken for money."""
    cleaned = _ISO_DATE.sub(" ", text)
    cleaned = _REQUEST_ID.sub(" ", cleaned)
    cleaned = _EVENT_ID.sub(" ", cleaned)
    cleaned = _MONTH_DAY.sub(" ", cleaned)
    return cleaned


def extract_money(text: str) -> Decimal | None:
    """First plausible money amount in the clause."""
    for match in _MONEY.finditer(_strip_tokens(text)):
        value = _money_value(match.group(1))
        if value is not None and value > 0:
            return value
    return None


def extract_request_id(text: str) -> str | None:
    match = _REQUEST_ID.search(text or "")
    return f"request_{match.group(1)}" if match else None


def extract_event_id(text: str) -> str | None:
    match = _EVENT_ID.search(text or "")
    return f"event_{match.group(1)}" if match else None


def _next_occurrence(day: int, reference: date) -> date:
    """The next time that day-of-month happens on/after the reference date."""
    if not 1 <= day <= 31:
        raise ValueError(f"day must be 1..31, got {day}")
    for offset in (0, 1, 2):
        month = reference.month - 1 + offset
        year = reference.year + month // 12
        month = month % 12 + 1
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference:
            return candidate
    raise ValueError(f"day {day} does not occur near {reference.isoformat()}")


def extract_date(text: str, reference: date | None = None) -> date | None:
    """ISO date, '12 September', or 'the 12th' relative to ``reference``."""
    reference = reference or date.today()
    match = _ISO_DATE.search(text or "")
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = _MONTH_DAY.search(text or "")
    if match:
        month = _MONTHS.get(match.group(2).lower()[:3])
        if month:
            # A month name with no year means the next occurrence, not a date in
            # the past: "on 3 Jan" asked in December is next January.  Search
            # forward until the day exists in that month, so "29 February"
            # asked in 2026 finds 2028 rather than nothing.
            for year in range(reference.year, reference.year + 9):
                try:
                    candidate = date(year, month, int(match.group(1)))
                except ValueError:
                    continue
                if candidate >= reference:
                    return candidate
    match = _DAY_ORDINAL.search(text or "")
    if match:
        try:
            return _next_occurrence(int(match.group(1)), reference)
        except ValueError:
            return None
    return None


# --- instruction parsing ---------------------------------------------------


def _classify_clause(clause: str) -> str:
    """Pick the verb of one clause. Order matters: specific beats generic."""
    if _UNDO.search(clause):
        return "undo"
    if _REDUCE.search(clause) and re.search(r"\bto\b", clause, re.IGNORECASE):
        return "apply"
    if _STOP.search(clause) and not _AFFORD.search(clause):
        return "apply"
    if _CHANGES.search(clause):
        return "changes"
    if _EXPLAIN.search(clause):
        return "explain"
    if _WHATIF.search(clause):
        return "whatif"
    if _AFFORD.search(clause):
        return "afford"
    return "unknown"


def parse_instruction(text, *, reference: date | None = None) -> list[StepSpec]:
    """Split a free-form instruction into validated step specifications."""
    clauses = [
        part.strip(" .,!?")
        for part in _CLAUSE_SPLIT.split(str(text or ""))
        if part and part.strip(" .,!?")
    ]
    specs: list[StepSpec] = []
    for clause in clauses[:MAX_STEPS]:
        kind = _classify_clause(clause)
        params: dict = {"clause": clause}
        request_id = extract_request_id(clause)
        if request_id:
            params["request_id"] = request_id
        if kind in ("afford", "whatif"):
            params["amount"] = extract_money(_DURATION.sub(" ", clause))
            # "The 12th" means nothing until the target request's own date is
            # known, so a relative date is left unset here and the step handler
            # resolves it against request_date. Resolving it against today()
            # would make the same instruction decide differently each day.
            params["date"] = (
                extract_date(clause, reference)
                if reference is not None or _ISO_DATE.search(clause)
                else None
            )
        elif kind == "apply":
            params["event_id"] = extract_event_id(clause)
            params["amount"] = extract_money(clause)
            params["action"] = "reduce_to" if _REDUCE.search(clause) else "stop"
        elif kind == "undo":
            params["request_id"] = request_id
        specs.append(StepSpec(kind=kind, text=clause, params=params))
    return specs


# --- workspace (session over the real kernel) ------------------------------


class Workspace:
    """A session-scoped view of the dataset with the TUI's edit semantics."""

    def __init__(self, data=None, session=None, repo_root=None) -> None:
        tui = _tui()
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        self._data = data
        self.session = session if session is not None else tui.Session()
        self._cache: dict[str, tuple[str, dict]] = {}
        self._manual: dict[str, dict] = {}
        self._manual_counter = 0
        self.current_request_id: str | None = None
        self.last_amount: Decimal | None = None
        self.last_date: date | None = None

    # -- dataset -----------------------------------------------------------

    @property
    def data(self) -> dict:
        if self._data is None:
            self._data = _tui().load_dataset(self.repo_root)
        return self._data

    def row(self, request_id) -> dict:
        key = str(request_id or "")
        for candidate in self.data["requests"] + list(self._manual.values()):
            if str(candidate.get("request_id", "")) == key:
                return candidate
        raise UnknownTarget(
            f"unknown request_id {key!r}; try `list` in the TUI to see the ids"
        )

    def has_request(self, request_id) -> bool:
        try:
            self.row(request_id)
        except UnknownTarget:
            return False
        return True

    def default_user(self) -> str:
        requests = self.data["requests"]
        if not requests:
            raise UnknownTarget("the dataset contains no requests")
        preferred = self.current_request_id or (
            requests[0].get("request_id") if requests else None
        )
        try:
            return str(self.row(preferred).get("user_id", "") or "")
        except UnknownTarget:
            return str(requests[0].get("user_id", "") or "")

    def add_manual(self, amount, request_date, user_id=None, desired=None) -> str:
        """Session-only manual request (mirrors the TUI's `new`)."""
        tui = _tui()
        # Continue the TUI's manual-id sequence rather than restarting at 0001:
        # `new` may already own manual_0001, and reusing that id would make this
        # question report the earlier request's decision.
        existing = {
            str(request.get("request_id", "")) for request in self.data["requests"]
        } | set(self._manual)
        index = self._manual_counter
        while f"{tui.MANUAL_PREFIX}{index + 1:04d}" in existing:
            index += 1
        self._manual_counter = index + 1
        request_id = f"{tui.MANUAL_PREFIX}{self._manual_counter:04d}"
        user_id = user_id or self.default_user()
        request_date = request_date or date.today()
        if not isinstance(request_date, date):
            request_date = date.fromisoformat(str(request_date))
        desired = desired or request_date + timedelta(days=30)
        if not isinstance(desired, date):
            desired = date.fromisoformat(str(desired))
        profile = self.data["profiles"].get(user_id, {})
        currency = str(profile.get("home_currency", "") or "").strip()
        amount_text = f"{Decimal(amount).normalize():f}"
        self._manual[request_id] = {
            "request_id": request_id,
            "user_id": user_id,
            "request_date": request_date.isoformat(),
            "request_type": "manual",
            "requested_amount": amount_text,
            "desired_completion_date": desired.isoformat(),
            "allows_partial_payment": "true",
            "request_text": f"manually entered {currency} {amount_text} on {request_date.isoformat()}",
        }
        return request_id

    # -- solving -----------------------------------------------------------

    def copy_session(self):
        tui = _tui()
        clone = tui.Session()
        clone.overrides = {
            key: dict(value) for key, value in self.session.overrides.items()
        }
        return clone

    def context(self, request_id, session=None):
        tui = _tui()
        session = self.session if session is None else session
        base = self.row(request_id)
        overlay = session.effective(base)
        row = {
            key: value
            for key, value in overlay.items()
            if not str(key).startswith(tui.PRIVATE_PREFIX)
        }
        applied = list(overlay.get(tui.APPLIED_KEY) or [])
        user_id = str(base.get("user_id", "") or "")
        profile = dict(self.data["profiles"].get(user_id, {}))
        for key in list(row):
            if key in tui.PROFILE_FIELDS:
                profile[key] = row.pop(key)
        return tui._Context(
            row,
            profile,
            self.data["events_by_user"].get(user_id, []),
            applied,
            user_id,
        )

    def decide(self, request_id, session=None, cache=None) -> dict | None:
        tui = _tui()
        session = self.session if session is None else session
        cache = self._cache if cache is None else cache
        signature = tui._signature(session, str(request_id))
        entry = cache.get(str(request_id))
        if entry is not None and entry[0] == signature:
            return entry[1]
        context = self.context(request_id, session)
        try:
            decision = tui._solve(context, self.data)
        except Exception as exc:  # a solver bug must not kill the whole task
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        cache[str(request_id)] = (signature, decision)
        return decision

    def changes(self, request_id, limit: int = 3) -> tuple[list[dict], Decimal, Decimal]:
        """(ranked candidates, baseline safe amount, requested amount)."""
        from code.changes import get_candidates, search_changes
        from code.recurrence import detect_recurrence

        tui = _tui()
        context = self.context(request_id)
        events = [dict(event) for event in context.events]
        flags = detect_recurrence(events)
        requested = _money_value(context.row.get("requested_amount")) or Decimal("0")
        baseline = tui.project_actions(
            context.user_id, context.row, context.profile, events,
            self.data["fx"], [], flags,
        )
        baseline_safe = baseline.get("safe_amount") or Decimal("0")
        try:
            recommended = search_changes(
                context.user_id, str(context.row.get("request_date")), requested,
                context.profile, events, self.data["fx"], flags,
            )
        except Exception:
            recommended = []
        rows: list[dict] = []
        seen: set[str] = set()
        for action in recommended:
            seen.add(action)
            projection = tui.project_actions(
                context.user_id, context.row, context.profile, events,
                self.data["fx"], [action], flags,
            )
            rows.append({
                "action": action,
                "safe": projection.get("safe_amount"),
                "delta": (projection.get("safe_amount") or Decimal("0")) - baseline_safe,
                "earliest": projection.get("earliest_date"),
                "recommended": True,
            })
        for event_id, action, value in get_candidates(events):
            text = (
                f"{action}:{event_id}"
                if action == "stop"
                else f"reduce_to:{event_id}:{value}"
            )
            if text in seen:
                continue
            seen.add(text)
            projection = tui.project_actions(
                context.user_id, context.row, context.profile, events,
                self.data["fx"], [text], flags,
            )
            rows.append({
                "action": text,
                "safe": projection.get("safe_amount"),
                "delta": (projection.get("safe_amount") or Decimal("0")) - baseline_safe,
                "earliest": projection.get("earliest_date"),
                "recommended": False,
            })
        rows.sort(
            key=lambda entry: (
                0 if entry["recommended"] else 1,
                -float(entry["safe"] or 0),
            )
        )
        return rows[: max(1, limit)], baseline_safe, requested

    def apply_actions(self, request_id, actions) -> list[str]:
        tui = _tui()
        context = self.context(request_id)
        fresh = [a for a in actions if a not in context.applied]
        if fresh:
            self.session.set(
                request_id, {tui.APPLIED_KEY: list(context.applied) + fresh}
            )
        return fresh

    # -- shaping -----------------------------------------------------------

    def resolve_action(self, spec: StepSpec) -> tuple[str | None, str]:
        """`stop event_NNN` / `reduce groceries to 8,000` -> action string."""
        event_id = spec.params.get("event_id")
        kind = spec.params.get("action") or "stop"
        if event_id:
            if kind == "stop":
                return f"stop:{event_id}", ""
            amount = spec.params.get("amount")
            if amount is None:
                return None, "which amount should it be reduced to?"
            return f"reduce_to:{event_id}:{amount.normalize():f}", ""
        # a category name, e.g. "stop my streaming subscription"
        request_id = spec.params.get("request_id") or self.current_request_id
        if not request_id or not self.has_request(request_id):
            return None, (
                "which request should I evaluate this against? name one, "
                "e.g. `request_NNN`"
            )
        try:
            rows, _baseline, _requested = self.changes(request_id, limit=50)
        except Exception as exc:
            return None, f"I could not project changes: {type(exc).__name__}: {exc}"
        words = {
            word
            for word in re.split(r"[^a-z_]+", spec.text.lower())
            if len(word) > 3
        }
        events = self.context(request_id).events
        category_of = {
            str(event.get("event_id", "")): str(event.get("category", "") or "").lower()
            for event in events
        }
        amount = spec.params.get("amount")
        for row in rows:
            action = row["action"]
            parts = action.split(":")
            suffix = parts[0] if len(parts) == 2 else parts[2]
            category = category_of.get(parts[1], "")
            if words & set(re.split(r"[^a-z_]+", category)):
                if kind == "reduce_to" and amount is not None:
                    return f"reduce_to:{parts[1]}:{amount.normalize():f}", ""
                if kind == "reduce_to":
                    return action, ""
                return f"stop:{parts[1]}", ""
        return None, (
            "I could not match that to a stoppable or reducible event for the user; "
            "run `changes` to see the candidates"
        )

    def floor(self, request_id, session=None):
        row = self.row(request_id)
        profile = self.data["profiles"].get(str(row.get("user_id", "")), {})
        return _money_value(profile.get("minimum_balance_to_keep"))


# --- step execution --------------------------------------------------------


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _afford(workspace: Workspace, spec: StepSpec, result: TaskResult) -> StepResult:
    amount = spec.params.get("amount")
    if amount is None:
        return StepResult(
            "afford", spec.text, STATUS_REFUSED,
            "how much should I check? give an amount, e.g. `can I afford 40,000 on the 12th`",
        )
    if amount <= 0:
        return StepResult("afford", spec.text, STATUS_REFUSED, "the amount must be positive")

    request_id = spec.params.get("request_id")
    wanted_date = spec.params.get("date")
    if request_id and workspace.has_request(request_id):
        workspace.current_request_id = request_id
        reference = _safe_date(workspace.row(request_id).get("request_date"))
        overrides = {"requested_amount": f"{amount.normalize():f}"}
        if wanted_date is None:
            wanted_date = extract_date(spec.text, reference)
        if wanted_date is not None:
            overrides["request_date"] = wanted_date.isoformat()
        temp = workspace.copy_session()
        temp.set(request_id, overrides)
        decision = workspace.decide(request_id, session=temp)
        question = f"{request_id} at {_fmt(amount)}"
    else:
        reference = None
        if workspace.current_request_id and workspace.has_request(workspace.current_request_id):
            reference = _safe_date(
                workspace.row(workspace.current_request_id).get("request_date")
            )
        if wanted_date is None:
            wanted_date = extract_date(spec.text, reference)
        request_date = wanted_date or reference or date.today()
        user_id = workspace.default_user()
        request_id = workspace.add_manual(amount, request_date, user_id)
        # The manual request is what the user is now talking about, so a
        # following `what if I wait until the 20th` must test this request.
        workspace.current_request_id = request_id
        decision = workspace.decide(request_id)
        question = f"{_fmt(amount)} on {request_date.isoformat()}"

    workspace.last_amount = amount
    workspace.last_date = wanted_date

    if decision is None:
        return StepResult(
            "afford", spec.text, STATUS_ERROR,
            "the solver could not produce a decision for that question",
        )

    status = decision.get("affordability_status")
    method = decision.get("recommended_payment_method")
    safe = decision.get("amount_safe_to_pay")
    earliest = decision.get("earliest_date_for_full_payment") or "no safe date"
    explanation = str(decision.get("decision_explanation") or "").strip()
    safe_value = _money_value(safe) or Decimal("0")
    if status == "affordable_now":
        verdict = (
            f"Yes. {question} is affordable now; the most you can pay today is "
            f"{_fmt(safe_value)}."
        )
    elif status == "affordable_with_plan":
        verdict = (
            f"Yes, with a plan. {question}: {method} works, first leg "
            f"{_fmt(safe_value)} today."
        )
    elif status == "affordable_later":
        verdict = (
            f"Not today, but yes later. {question} becomes safe on {earliest}; "
            f"today's safe amount is {_fmt(safe_value)}."
        )
    else:
        verdict = (
            f"No. {question} is not affordable within the forecast; today's safe "
            f"amount is {_fmt(safe_value)}."
        )
    if explanation:
        verdict = f"{verdict} {explanation}"
    result.changed.append({
        "request_id": request_id,
        "status": status,
        "method": method,
        "safe_amount": _fmt(safe_value),
        "earliest_date": earliest,
    })
    return StepResult(
        "afford", spec.text, STATUS_OK, verdict,
        {"request_id": request_id, "decision": decision},
    )


def _safe_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _explain(workspace: Workspace, spec: StepSpec) -> StepResult:
    request_id = spec.params.get("request_id") or workspace.current_request_id
    if not request_id or not workspace.has_request(request_id):
        return StepResult(
            "explain", spec.text, STATUS_REFUSED,
            "which request should I explain? name one, e.g. `why was request_NNN refused`",
        )
    workspace.current_request_id = request_id
    decision = workspace.decide(request_id)
    if decision is None:
        return StepResult(
            "explain", spec.text, STATUS_ERROR, f"no decision for {request_id}"
        )
    explanation = str(decision.get("decision_explanation") or "").strip()
    detail = (
        f"{request_id}: {decision.get('affordability_status')} via "
        f"{decision.get('recommended_payment_method')}, safe "
        f"{_fmt(decision.get('amount_safe_to_pay'))}, earliest "
        f"{decision.get('earliest_date_for_full_payment') or 'no safe date'}."
    )
    if explanation:
        detail = f"{detail} {explanation}"
    return StepResult(
        "explain", spec.text, STATUS_OK, detail,
        {"request_id": request_id, "decision": decision},
    )


def _changes(workspace: Workspace, spec: StepSpec, result: TaskResult) -> StepResult:
    request_id = spec.params.get("request_id") or workspace.current_request_id
    if not request_id or not workspace.has_request(request_id):
        request_id = _pick_request(workspace, "least safe")
    if not request_id:
        return StepResult(
            "changes", spec.text, STATUS_REFUSED,
            "I need a request to evaluate spending changes against",
        )
    workspace.current_request_id = request_id
    try:
        rows, baseline_safe, requested = workspace.changes(request_id, limit=3)
    except Exception as exc:
        return StepResult(
            "changes", spec.text, STATUS_ERROR,
            f"projecting changes failed: {type(exc).__name__}: {exc}",
        )
    if not rows:
        return StepResult(
            "changes", spec.text, STATUS_OK,
            f"{request_id}: no stoppable or reducible events match the user's "
            f"allowed categories; baseline safe {_fmt(baseline_safe)}.",
            {"request_id": request_id},
        )
    if baseline_safe >= requested:
        return StepResult(
            "changes", spec.text, STATUS_OK,
            f"{request_id}: already affordable (safe {_fmt(baseline_safe)} covers the "
            f"{_fmt(requested)} request), so no spending change is needed. Cheapest "
            f"candidates would be {rows[0]['action']}.",
            {"request_id": request_id, "already_affordable": True},
        )
    lines = []
    covered = False
    for row in rows:
        action = row["action"]
        if (row["safe"] or Decimal("0")) >= requested:
            covered = True
        lines.append(
            f"{action} -> safe {_fmt(row['safe'])} "
            f"({'+' if (row['delta'] or 0) >= 0 else ''}{_fmt(row['delta'])}), "
            f"earliest {row['earliest'] or 'none'}"
        )
    detail = (
        f"{request_id}: baseline safe {_fmt(baseline_safe)} against a "
        f"{_fmt(requested)} request. Best candidates: " + "; ".join(lines)
    )
    if not covered:
        detail = (
            f"{detail}. None of these alone covers the request, so it stays "
            f"outside the safe range."
        )
    return StepResult(
        "changes", spec.text, STATUS_OK, detail,
        {"request_id": request_id, "candidates": [
            {k: (_fmt(v) if isinstance(v, Decimal) else v) for k, v in row.items()}
            for row in rows
        ]},
    )


def _apply(workspace: Workspace, spec: StepSpec, result: TaskResult) -> StepResult:
    request_id = spec.params.get("request_id") or workspace.current_request_id
    if not request_id or not workspace.has_request(request_id):
        request_id = _pick_request(workspace, "least safe")
    if not request_id:
        return StepResult(
            "apply", spec.text, STATUS_REFUSED,
            "which request should I evaluate that change against?",
        )
    workspace.current_request_id = request_id
    action, problem = workspace.resolve_action(spec)
    if action is None:
        return StepResult("apply", spec.text, STATUS_REFUSED, problem)
    before = workspace.decide(request_id)
    previous_applied = list(workspace.context(request_id).applied)
    fresh = workspace.apply_actions(request_id, [action])
    if not fresh:
        return StepResult(
            "apply", spec.text, STATUS_OK,
            f"{action} is already applied to {request_id}",
        )
    after = workspace.decide(request_id)
    changed = _tui().diff_fields(before or {}, after or {})
    if not changed:
        alternative = _nearest_alternative(workspace, request_id, action)
        _restore_applied(workspace, request_id, previous_applied)
        return StepResult(
            "apply", spec.text, STATUS_REFUSED,
            f"applied {action} to {request_id} and the decision did not move, so I "
            f"put it back. {alternative}",
        )
    result.actions_applied.append({"request_id": request_id, "action": action})
    result.changed.append({
        "request_id": request_id,
        "action": action,
        "fields": {key: [_fmt(before and before.get(key)), _fmt(after and after.get(key))]
                   for key, (_b, _a) in changed.items()},
    })
    summary = ", ".join(
        f"{key} {_fmt(before_v)} -> {_fmt(after_v)}"
        for key, (before_v, after_v) in changed.items()
    )
    return StepResult(
        "apply", spec.text, STATUS_OK,
        f"{action} applied to {request_id} in this session: {summary}. `undo` reverts it.",
        {"request_id": request_id, "action": action, "changed": list(changed)},
    )


def _restore_applied(workspace: Workspace, request_id, previous) -> None:
    """Put the applied-action list back, keeping every other session override.

    ``session.undo`` drops the whole override dict for the request, which would
    silently discard a ``whatif`` amount or an earlier action the user still
    expects to be in force.
    """
    tui = _tui()
    overlay = workspace.session.overrides.get(str(request_id), {})
    if previous:
        overlay[tui.APPLIED_KEY] = list(previous)
    else:
        overlay.pop(tui.APPLIED_KEY, None)
    if not overlay:
        workspace.session.undo(request_id)


def _nearest_alternative(workspace: Workspace, request_id, action) -> str:
    try:
        rows, _baseline, _requested = workspace.changes(request_id, limit=1)
    except Exception:
        return ""
    if not rows:
        return "No other safe spending change exists for this user."
    return f"The nearest candidate that does move it is {rows[0]['action']}."


def _whatif(workspace: Workspace, spec: StepSpec) -> StepResult:
    request_id = spec.params.get("request_id") or workspace.current_request_id
    if not request_id or not workspace.has_request(request_id):
        return StepResult(
            "whatif", spec.text, STATUS_REFUSED,
            "what should I test that against? name a request, e.g. "
            "`what if request_NNN waits until 2025-09-20`",
        )
    workspace.current_request_id = request_id
    reference = _safe_date(workspace.row(request_id).get("request_date"))
    wanted_date = spec.params.get("date")
    if wanted_date is None:
        wanted_date = extract_date(spec.text, reference)
    amount = spec.params.get("amount") or workspace.last_amount
    overrides: dict = {}
    if wanted_date is not None:
        overrides["request_date"] = wanted_date.isoformat()
    if amount is not None:
        overrides["requested_amount"] = f"{Decimal(amount).normalize():f}"
    if not overrides:
        return StepResult(
            "whatif", spec.text, STATUS_REFUSED,
            "I need a date or an amount to test; e.g. `what if I wait until the 20th`",
        )
    before = workspace.decide(request_id)
    temp = workspace.copy_session()
    temp.set(request_id, overrides)
    after = workspace.decide(request_id, session=temp)
    if after is None:
        return StepResult(
            "whatif", spec.text, STATUS_ERROR, "the solver could not re-run that scenario"
        )
    changed = _tui().diff_fields(before or {}, after or {})
    if not changed:
        return StepResult(
            "whatif", spec.text, STATUS_OK,
            f"{request_id}: no decision field changes under "
            f"{', '.join(f'{k}={v}' for k, v in overrides.items())}.",
            {"request_id": request_id, "overrides": overrides},
        )
    lines = [
        f"{key} {_fmt(before_v)} -> {_fmt(after_v)}"
        for key, (before_v, after_v) in changed.items()
    ]
    return StepResult(
        "whatif", spec.text, STATUS_OK,
        f"{request_id} with {', '.join(f'{k}={v}' for k, v in overrides.items())}: "
        + "; ".join(lines) + " (analysis only, nothing applied).",
        {"request_id": request_id, "overrides": overrides},
    )


def _undo(workspace: Workspace, spec: StepSpec) -> StepResult:
    request_id = spec.params.get("request_id")
    if request_id:
        if request_id not in workspace.session.overrides:
            return StepResult(
                "undo", spec.text, STATUS_OK, f"nothing to undo for {request_id}"
            )
        workspace.session.undo(request_id)
        return StepResult(
            "undo", spec.text, STATUS_OK,
            f"undid the session changes for {request_id}",
        )
    workspace.session.undo()
    return StepResult("undo", spec.text, STATUS_OK, "cleared every session change")


def _pick_request(workspace: Workspace, why: str) -> str | None:
    """Most useful default target when the instruction names none.

    Bounded scan: the first ``DEFAULT_SCAN`` requests, preferring one that is
    currently unsafe with the smallest safe amount. Solving is cached per
    request, so repeated steps reuse the same work.
    """
    try:
        requests = workspace.data["requests"][:DEFAULT_SCAN]
    except Exception:
        return None
    if not requests:
        return None
    best = None
    best_key = None
    for row in requests:
        request_id = str(row.get("request_id", ""))
        decision = workspace.decide(request_id)
        if decision is None:
            continue
        status = str(decision.get("affordability_status") or "")
        safe = _money_value(decision.get("amount_safe_to_pay")) or Decimal("0")
        key = (0 if status != "affordable_now" else 1, safe)
        if best_key is None or key < best_key:
            best, best_key = request_id, key
    return best


# --- the task loop ---------------------------------------------------------


def _spec_from_intent(intent, clause: str) -> StepSpec | None:
    """Turn a model classification into a step the engine can execute.

    Returns ``None`` when the classification says a parameter is still missing,
    which becomes a clarifying question rather than a guess.
    """
    params: dict = {"clause": clause}
    if intent.target_request_id:
        params["request_id"] = intent.target_request_id
    if intent.target_event_id:
        params["event_id"] = intent.target_event_id
    if intent.intent == "action":
        if intent.target_event_id:
            params["action"] = "stop"
            return StepSpec("apply", clause, params)
        return StepSpec("changes", clause, params)
    if intent.intent == "follow_up":
        # A waiting period is not an amount, the same rule as parse_instruction:
        # "2 weeks" must not become a 2-unit request.
        params["amount"] = extract_money(_DURATION.sub(" ", clause))
        # Same rule as parse_instruction: only an absolute date is resolvable
        # without the target request.
        params["date"] = extract_date(clause) if _ISO_DATE.search(clause) else None
        return StepSpec("whatif", clause, params)
    if intent.intent == "question":
        return StepSpec("explain", clause, params)
    if intent.intent in ("analysis", "compound"):
        return StepSpec("changes", clause, params)
    return None


def run_task(
    instruction,
    *,
    workspace: Workspace | None = None,
    data=None,
    session=None,
    trace_dir=None,
    max_steps: int = MAX_STEPS,
    reference_date: date | None = None,
    provider=None,
    intent_timeout_s: float | None = None,
) -> TaskResult:
    """Parse and execute one instruction, step by step.

    ``provider`` is optional: when given, clauses the grammar cannot classify are
    sent to the model's intent classifier, and every step records which reading
    drove it (``model`` or ``deterministic_fallback``). Without a provider the
    rules answer alone.
    """
    text = str(instruction or "").strip()
    task_id = (
        f"task_{datetime.now().strftime('%Y%m%dT%H%M%S')}_"
        f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"
    )
    result = TaskResult(task_id=task_id, instruction=text)
    if intent_timeout_s is None:
        raw_timeout = os.environ.get(INTENT_TIMEOUT_ENV, '')
        try:
            intent_timeout_s = float(raw_timeout) if raw_timeout.strip() else DEFAULT_INTENT_TIMEOUT
        except ValueError:
            intent_timeout_s = DEFAULT_INTENT_TIMEOUT
    workspace = workspace or Workspace(data=data, session=session)
    if workspace.data is None:
        result.concerns.append("dataset unavailable")
        result.summary = "I could not load the dataset, so nothing was evaluated."
        return result

    specs = parse_instruction(text, reference=reference_date)
    known_requests = [
        str(row.get("request_id", "")) for row in workspace.data.get("requests", [])
    ]
    known_events = sorted(
        {
            str(event.get("event_id", ""))
            for events in workspace.data.get("events_by_user", {}).values()
            for event in events
        }
    )
    for spec in specs:
        reading = (
            detect_intent(
                spec.text,
                provider=provider,
                timeout_s=intent_timeout_s,
                known_requests=known_requests,
                known_events=known_events,
                clause_count=len(specs),
            )
            if spec.kind == "unknown" and provider is not None
            else deterministic_intent(
                spec.text,
                known_requests=known_requests,
                known_events=known_events,
                clause_count=len(specs),
            )
        )
        spec.params["intent"] = reading.as_dict()
        if spec.kind != "unknown" or reading.source != "model":
            continue
        if reading.missing_parameters:
            spec.params["clarify"] = reading.missing_parameters
            continue
        replacement = _spec_from_intent(reading, spec.text)
        if replacement is not None:
            replacement.params["intent"] = reading.as_dict()
            spec.kind, spec.params = replacement.kind, replacement.params

    if not specs:
        result.summary = (
            "I did not catch an instruction. Try `can I afford 40,000 on the 12th`, "
            "`where am I leaking money`, or `stop my delivery membership`."
        )
        return result

    for spec in specs[:max_steps]:
        if spec.kind == "afford":
            step = _afford(workspace, spec, result)
        elif spec.kind == "explain":
            step = _explain(workspace, spec)
        elif spec.kind == "changes":
            step = _changes(workspace, spec, result)
        elif spec.kind == "apply":
            step = _apply(workspace, spec, result)
        elif spec.kind == "whatif":
            step = _whatif(workspace, spec)
        elif spec.kind == "undo":
            step = _undo(workspace, spec)
        elif spec.params.get("clarify"):
            step = StepResult(
                "clarify", spec.text, STATUS_REFUSED,
                "I need one more thing before I can act: "
                + str(spec.params["clarify"])
                + ".",
            )
        else:
            step = StepResult(
                "unknown", spec.text, STATUS_UNKNOWN,
                "I did not understand that clause. I can check affordability, explain "
                "a decision, list spending changes, apply one, run a what-if, or undo.",
            )
        step.data.setdefault("intent", spec.params.get("intent") or {})
        result.steps.append(step)

    result.summary = "\n".join(
        step.detail for step in result.steps if step.detail
    ) or "Nothing to report."

    if trace_dir is not None:
        result.trace_path = _write_trace(result, Path(trace_dir))
    return result


def _trace_safe_action(action) -> str:
    """An applied action with its amount removed: ``reduce_to:evt_003``.

    ``actions_applied`` records ``reduce_to:<event_id>:<amount>``, and that
    amount is the user's own money. The trace keeps the action and the event it
    touched, which is what makes the run legible, and drops the figure.
    """
    parts = str(action or "").split(":")
    return ":".join(parts[:2]) if len(parts) > 1 else parts[0]


#: The intent reading is provenance, not user text: these fields say which
#: classifier ran and what it concluded, and carry no clause text or amounts.
_TRACE_INTENT_FIELDS = ("intent", "request_kind", "source", "confidence", "ambiguity")


def _write_trace(result: TaskResult, directory: Path) -> str | None:
    """Write the task trace; a failed write never fails the task.

    The trace is a diagnostic, not a transcript.  It keeps the shape of the run
    -- step kinds, statuses, ids and the provider metadata -- and a hash of the
    instruction instead of its text, because the text is the user's own
    financial question.  Everything written is passed through
    :func:`code.trace.sanitize`, so a provider error that echoes a URL password
    or a key cannot be persisted either.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{result.task_id}.json"
        record = result.as_dict()
        instruction = str(record.pop("instruction", "") or "")
        record["instruction"] = {
            "sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            "length": len(instruction),
        }
        record["summary"] = ""
        # `changed` carries the safe amount an afford computed and the
        # before/after of every decision field an apply moved, so it is
        # rebuilt from non-monetary metadata rather than copied.
        changed = []
        for entry in record.get("changed") or []:
            if not isinstance(entry, dict):
                continue
            safe_entry = {
                key: entry[key]
                for key in ("request_id", "status", "method")
                if key in entry
            }
            if "action" in entry:
                safe_entry["action"] = _trace_safe_action(entry["action"])
            if isinstance(entry.get("fields"), dict):
                safe_entry["fields"] = sorted(entry["fields"])
            changed.append(safe_entry)
        record["changed"] = changed
        actions = []
        for entry in record.get("actions_applied") or []:
            if isinstance(entry, dict):
                safe = dict(entry)
                safe["action"] = _trace_safe_action(safe.get("action"))
                actions.append(safe)
            else:
                actions.append(_trace_safe_action(entry))
        record["actions_applied"] = actions
        steps = []
        for step in record.get("steps") or []:
            entry = dict(step) if isinstance(step, dict) else {"step": str(step)}
            entry["text"] = ""
            entry["detail"] = ""
            data = entry.pop("data", None)
            if isinstance(data, dict):
                # `data` carries the same clause text and the parsed amounts.
                # Keep only the intent provenance, so a trace still shows which
                # classifier produced the reading.
                reading = data.get("intent")
                entry["data"] = (
                    {
                        "intent": {
                            key: reading[key]
                            for key in _TRACE_INTENT_FIELDS
                            if key in reading
                        }
                    }
                    if isinstance(reading, dict)
                    else {}
                )
            steps.append(entry)
        record["steps"] = steps
        payload = sanitize(
            {
                "kind": "task",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                **record,
            }
        )
        path.write_text(
            json.dumps(payload, indent=2, default=str, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return str(path)
    except OSError:
        return None


def main(argv=None) -> int:
    """``python -m code.task_agent "can I afford 40,000 on the 12th"``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m code.task_agent",
        description=(
            "Run one plain-language task over the deterministic kernel. "
            "Session only: output.csv and dataset/ are never written."
        ),
    )
    parser.add_argument("instruction", nargs="+", help="the instruction to run")
    parser.add_argument(
        "--trace-dir",
        default=str(DEFAULT_TRACE_DIR),
        help=f"where to write the task trace (default: {DEFAULT_TRACE_DIR})",
    )
    parser.add_argument(
        "--no-trace", action="store_true", help="do not write a trace file"
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=("none", "ollama", "openrouter", "kilo", "paid"),
        help=(
            "model used for intent classification when the rules cannot read a "
            "clause (default: none, rules only)"
        ),
    )
    parser.add_argument("--model", default=None, help="model id for --provider")
    args = parser.parse_args(argv)

    provider = None
    if args.provider and args.provider != "none":
        import os

        from code.providers import build_provider

        os.environ["BUY_WAIT_LLM_PROVIDER"] = args.provider
        if args.model:
            model_env = {
                "ollama": "BUY_WAIT_OLLAMA_MODEL",
                "openrouter": "BUY_WAIT_LLM_MODEL",
                "kilo": "BUY_WAIT_KILO_MODEL",
                "paid": "BUY_WAIT_PAID_MODEL",
            }[args.provider]
            os.environ[model_env] = args.model
        provider, reason = build_provider()
        if provider is None:
            print(f"provider unavailable: {reason}")
        else:
            from code.providers import privacy_warning

            notice = privacy_warning(provider, honour_acceptance=False)
            if notice:
                print(notice)

    instruction = " ".join(args.instruction)
    result = run_task(
        instruction,
        trace_dir=None if args.no_trace else Path(args.trace_dir),
        provider=provider,
    )
    for index, step in enumerate(result.steps, start=1):
        reading = (step.data or {}).get("intent") or {}
        label = f"[{index}/{len(result.steps)}] {step.kind} ({step.status})"
        if reading.get("intent"):
            label += (
                f" intent={reading['intent']}/{reading.get('request_kind', 'ambiguous')}"
                f" via {reading.get('source')}"
            )
        print(label)
        print(f"  {step.detail}")
    if result.trace_path:
        print(f"task trace: {result.trace_path}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import sys

    sys.exit(main())
