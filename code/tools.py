"""Typed, deterministic tools for the agentic layer (Task 5A).

Every tool is reachable through :meth:`Toolbox.call` and returns exactly one of::

    {"ok": True, "data": ...}
    {"ok": False, "error": "...", "fatal": False}

Tool errors are values, never exceptions: a provider that calls a tool badly (or
asks about a missing request) gets a structured error it can reason about, and
the run keeps going.  Nothing here touches the network or a model.

Memoization is per ``Toolbox`` instance and keyed by request/change set, so a
provider may call ``get_cashflow_projection`` repeatedly without recomputing the
91-day ledger.

Production parity: ``run_pipeline`` supplies the kernel row it already computed
through ``kernel_results``.  ``Toolbox`` never recomputes or overrides a supplied
row, so an agent run can never disagree with the deterministic pipeline about
what the kernel decided.
"""
from __future__ import annotations

import functools
import inspect
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from code.changes import apply_changes, permitted_actions
from code.options import get_valid_options
from code.oracle import solve_safe_amount
from code.recurrence import detect_recurrence
from code.schema import (
    CandidateValidationError,
    money_text,
    parse_candidate,
    parse_spending_changes,
)
from code.solver import solve_request
from code.verifier import enforce_decision

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Bound on oracle probes made by ``list_spending_change_candidates``.
MAX_FORECAST_PROBES = 8
MAX_EVENT_LIMIT = 1000


class ToolError(Exception):
    """An expected, non-fatal tool failure (reported as ``ok=False``)."""


def _error(message: str, *, fatal: bool = False) -> dict:
    return {"ok": False, "error": str(message), "fatal": bool(fatal)}


def _structured(fn: Callable):
    """Turn raised errors into the tool error envelope; never propagate."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except ToolError as exc:
            return _error(exc)
        except CandidateValidationError as exc:
            return _error("; ".join(exc.errors) or str(exc))
        except Exception as exc:  # defensive: a tool must never crash the run
            return _error(f"{type(exc).__name__}: {exc}")

    return wrapper


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def _clean_row(row: dict) -> dict:
    return {str(key): _text(value) for key, value in dict(row).items()}


def _amount_key(value) -> Decimal:
    """Sort key for money that may already be formatted, blank, or a Decimal."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or "0").replace(",", "").strip() or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _plain(value):
    """JSON-ready copy with Decimal/date preserved as deterministic text."""
    if isinstance(value, Decimal):
        return money_text(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _event_date(event: dict) -> str:
    return _text(event.get("settlement_date")) or _text(event.get("event_date"))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: tuple[str, ...]
    handler: str


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_request_context",
        "Request row, user profile and supplied payment-option rows.",
        ("request_id",),
        "get_request_context",
    ),
    ToolSpec(
        "get_financial_events",
        "Canonical financial-event rows for a user, date-filtered and sorted.",
        ("user_id", "start_date", "end_date", "limit"),
        "get_financial_events",
    ),
    ToolSpec(
        "search_messages",
        "Deterministic lexical search over message text; returns citations.",
        ("user_id", "query", "limit"),
        "search_messages",
    ),
    ToolSpec(
        "search_evidence",
        "RAG-first citation search over stored messages/events (citations only, never numbers).",
        ("query", "user_id", "request_id", "limit"),
        "search_evidence",
    ),
    ToolSpec(
        "get_cashflow_projection",
        "Oracle projection for the base state or a validated spending-change set.",
        ("request_id", "spending_changes"),
        "get_cashflow_projection",
    ),
    ToolSpec(
        "get_payment_options",
        "Supplied payment options plus which ones the profile makes eligible.",
        ("request_id",),
        "get_payment_options",
    ),
    ToolSpec(
        "list_spending_change_candidates",
        "Permitted stop/reduce actions and whether each moves the forecast.",
        ("request_id",),
        "list_spending_change_candidates",
    ),
    ToolSpec(
        "propose_decision",
        "Schema validation only; returns the normalized candidate or errors.",
        ("candidate",),
        "propose_decision",
    ),
    ToolSpec(
        "verify_decision",
        "Kernel plus verifier for the current request; never returns a decision it owns.",
        ("candidate",),
        "verify_decision",
    ),
    ToolSpec(
        "propose_spending_change",
        "Try one permitted stop/reduce action; the kernel re-solves and owns the numbers.",
        ("request_id", "event_id", "action", "new_amount", "rationale"),
        "propose_spending_change",
    ),
    ToolSpec(
        "search_spending_changes",
        "Rank the permitted spending changes by the safe amount each one unlocks.",
        ("request_id",),
        "search_spending_changes",
    ),
    ToolSpec(
        "compare_payment_options",
        "Compare the supplied payment options: total payable, fees and eligibility.",
        ("request_id",),
        "compare_payment_options",
    ),
    ToolSpec(
        "reconcile_receipts",
        "List the events whose amount came from an image and flag unresolved evidence.",
        ("user_id",),
        "reconcile_receipts",
    ),
    ToolSpec(
        "raise_concern",
        "Record a concern (unreadable evidence, ambiguity, conflict). Never changes a number.",
        ("kind", "detail"),
        "raise_concern",
    ),
)

_SPECS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


@dataclass(frozen=True)
class DatasetBundle:
    """Read-only dataset view shared by the tools (loaded lazily when needed)."""

    requests: Any
    profiles: Any
    events: tuple
    fx: tuple
    options: Any
    messages: Any
    images: Any = None
    evidence_warnings: tuple = ()

    @classmethod
    def from_frames(
        cls,
        *,
        requests,
        profiles,
        events,
        fx,
        options,
        messages,
        images=None,
        evidence_warnings=(),
    ) -> "DatasetBundle":
        return cls(
            requests=requests,
            profiles=profiles,
            events=tuple(events or ()),
            fx=tuple(fx or ()),
            options=options,
            messages=messages,
            images=images,
            evidence_warnings=tuple(evidence_warnings or ()),
        )

    @classmethod
    def load(cls, repo_root=None) -> "DatasetBundle":
        import pandas as pd

        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        dataset = root / "dataset"

        def read(name: str):
            return pd.read_csv(dataset / name, dtype=str, keep_default_na=False)

        return cls(
            requests=read("requests.csv"),
            profiles=read("financial_profiles.csv"),
            events=tuple(read("financial_events.csv").to_dict("records")),
            fx=tuple(read("exchange_rates.csv").to_dict("records")),
            options=read("request_payment_options.csv"),
            messages=read("messages.csv"),
            images=read("images.csv"),
        )


class Toolbox:
    """Dataset-scoped tool registry with per-run memoization and optional trace."""

    def __init__(self, repo_root=None, *, data=None, kernel_results=None, trace=None, retriever=None):
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        self._data = data
        self._kernel_results = kernel_results if kernel_results is not None else {}
        self._cache: dict = {}
        self.trace = trace
        # Optional RAG retriever (code.rag).  ``None`` keeps the tool layer fully
        # offline: search_evidence then uses its own lexical SQLite-free fallback.
        self.retriever = retriever

    # -- registry ---------------------------------------------------------

    def describe(self) -> list[dict]:
        """Tool metadata for a provider prompt (names, parameters, purpose)."""
        return [
            {
                "name": spec.name,
                "parameters": list(spec.parameters),
                "description": spec.description,
            }
            for spec in TOOL_SPECS
        ]

    def call(self, name: str, **kwargs) -> dict:
        """Invoke one tool and always return the structured envelope."""
        started = time.perf_counter()
        spec = _SPECS_BY_NAME.get(str(name))
        if spec is None:
            result = _error(
                f"unknown tool {name!r}; available: {', '.join(sorted(_SPECS_BY_NAME))}"
            )
        else:
            method = getattr(self, spec.handler)
            try:
                inspect.signature(method).bind(**kwargs)
            except TypeError as exc:
                result = _error(f"invalid arguments for {spec.name}: {exc}")
            else:
                try:
                    result = method(**kwargs)
                except Exception as exc:  # belt and braces; wrapper already fails soft
                    result = _error(f"{type(exc).__name__}: {exc}")
        if self.trace is not None:
            self.trace.record_tool_call(
                str(name),
                kwargs,
                ok=bool(result.get("ok")),
                error=None if result.get("ok") else result.get("error"),
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        return result

    # -- dataset access ---------------------------------------------------

    def _bundle(self) -> DatasetBundle:
        if self._data is None:
            self._data = DatasetBundle.load(self.repo_root)
        return self._data

    def _first_row(self, frame, column: str, value, label: str) -> dict:
        try:
            matches = frame[frame[column].astype(str) == str(value)]
        except Exception as exc:
            raise ToolError(f"cannot read {label}: {exc}") from exc
        if len(matches) == 0:
            raise ToolError(f"unknown {label} {value!r}")
        return _clean_row(matches.iloc[0].to_dict())

    def _request_row(self, request_id) -> dict:
        return self._first_row(self._bundle().requests, "request_id", request_id, "request_id")

    def _profile_row(self, user_id) -> dict:
        return self._first_row(self._bundle().profiles, "user_id", user_id, "user_id")

    def _option_rows(self, request_id) -> list[dict]:
        frame = self._bundle().options
        matches = frame[frame["request_id"].astype(str) == str(request_id)]
        rows = [_clean_row(row) for row in matches.to_dict("records")]
        return sorted(rows, key=lambda row: row.get("payment_option_id", ""))

    def _user_events(self, user_id) -> list[dict]:
        key = ("events", str(user_id))
        if key not in self._cache:
            rows = [
                _clean_row(event)
                for event in self._bundle().events
                if _text(event.get("user_id")) == str(user_id)
            ]
            rows.sort(key=lambda event: (_event_date(event), _text(event.get("event_id"))))
            self._cache[key] = rows
        return self._cache[key]

    def _recurrence_flags(self, user_id):
        key = ("recurrence", str(user_id))
        if key not in self._cache:
            self._cache[key] = detect_recurrence(self._user_events(user_id))
        return self._cache[key]

    def _permitted_actions(self, user_id) -> list[dict]:
        key = ("permitted", str(user_id))
        if key not in self._cache:
            self._cache[key] = permitted_actions(
                str(user_id),
                self._profile_row(user_id),
                self._user_events(user_id),
                self._recurrence_flags(user_id),
            )
        return self._cache[key]

    # -- kernel -----------------------------------------------------------

    def kernel_result_for(self, request_id) -> dict:
        """The kernel row for one request (production-supplied or solved once)."""
        request_id = str(request_id)
        if request_id in self._kernel_results:
            return self._kernel_results[request_id]
        key = ("kernel", request_id)
        if key in self._cache:
            return self._cache[key]
        request = self._request_row(request_id)
        user_id = request.get("user_id", "")
        profile = self._profile_row(user_id)
        bundle = self._bundle()
        try:
            result = solve_request(
                request_id,
                user_id,
                request.get("request_date", ""),
                request.get("requested_amount", ""),
                profile,
                self._user_events(user_id),
                list(bundle.fx),
                bundle.options,
                bundle.messages,
                request.get("desired_completion_date") or None,
                request.get("allows_partial_payment") or None,
                installment_safety="strict",
            )
        except Exception as exc:
            raise ToolError(f"kernel failed for {request_id}: {type(exc).__name__}: {exc}") from exc
        self._cache[key] = result
        return result

    # -- change validation / projection -----------------------------------

    def _change_text(self, spending_changes) -> str:
        if spending_changes is None:
            return "none"
        if isinstance(spending_changes, str):
            return spending_changes.strip() or "none"
        if isinstance(spending_changes, (list, tuple)):
            if not spending_changes:
                return "none"
            return "|".join(str(item).strip() for item in spending_changes)
        raise ToolError(
            "spending_changes must be 'none', a '|'-joined string, or a list of actions"
        )

    def _validate_change_tokens(self, user_id, profile, tokens) -> list[dict]:
        if not tokens:
            return []
        permitted = {
            (str(action["event_id"]), str(action["action"])): action
            for action in self._permitted_actions(user_id)
        }
        events = {event.get("event_id"): event for event in self._user_events(user_id)}
        actions: list[dict] = []
        for verb, event_id, amount in tokens:
            action = permitted.get((event_id, verb))
            if action is None:
                raise ToolError(
                    f"spending change {verb}:{event_id} is not permitted for user {user_id}"
                )
            event = events.get(event_id) or {}
            if verb == "reduce_to":
                floor = action.get("value")
                if not isinstance(floor, Decimal):
                    floor = Decimal(str(floor))
                if amount is None or amount < floor:
                    raise ToolError(
                        f"reduce_to:{event_id} must be at least {money_text(floor)}"
                    )
            actions.append(
                {
                    "event_id": event_id,
                    "action": verb,
                    "value": Decimal("0") if verb == "stop" else amount,
                    "category": str(action.get("category") or event.get("category") or ""),
                }
            )
        return actions

    def _projection(self, request_id, tokens) -> dict:
        request_id = str(request_id)
        key = ("projection", request_id, tuple(tokens))
        if key in self._cache:
            return self._cache[key]
        request = self._request_row(request_id)
        user_id = request.get("user_id", "")
        profile = self._profile_row(user_id)
        actions = self._validate_change_tokens(user_id, profile, tokens)
        modified, suppressed, overrides = apply_changes(self._user_events(user_id), actions)
        try:
            result = solve_safe_amount(
                user_id,
                request.get("request_date", ""),
                request.get("requested_amount", ""),
                profile,
                modified,
                list(self._bundle().fx),
                self._recurrence_flags(user_id),
                suppressed_categories=suppressed,
                category_overrides=overrides,
            )
        except Exception as exc:
            raise ToolError(
                f"oracle projection failed for {request_id}: {type(exc).__name__}: {exc}"
            ) from exc
        ledger = result.get("ledger") or []
        summary = None
        if ledger:
            try:
                balances = [Decimal(str(value)) for value in ledger]
                summary = {
                    "days": len(balances),
                    "start_balance": money_text(balances[0]),
                    "min_balance": money_text(min(balances)),
                    "end_balance": money_text(balances[-1]),
                    "minimum_balance_to_keep": _text(profile.get("minimum_balance_to_keep")),
                }
            except Exception:
                summary = None
        payload = {
            "request_id": request_id,
            "requested_amount": _text(request.get("requested_amount")),
            "spending_changes": "|".join(
                f"stop:{token[1]}" if token[0] == "stop" else f"reduce_to:{token[1]}:{money_text(token[2])}"
                for token in tokens
            )
            or "none",
            "safe_amount": money_text(result.get("safe_amount", "0")),
            "earliest_date": result.get("earliest_date"),
            "fail_closed": bool(result.get("fail_closed")),
            "fail_closed_reason": result.get("fail_closed_reason"),
            "ledger_summary": summary,
            # Internal: kept for change-effect probes, never returned by a tool.
            "ledger": list(ledger),
        }
        self._cache[key] = payload
        return payload

    # -- tools ------------------------------------------------------------

    @_structured
    def get_request_context(self, request_id: str) -> dict:
        request = self._request_row(request_id)
        user_id = request.get("user_id", "")
        return {
            "request": request,
            "profile": self._profile_row(user_id),
            "options": self._option_rows(request_id),
        }

    @_structured
    def get_financial_events(
        self,
        user_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> dict:
        try:
            start = date.fromisoformat(str(start_date)) if start_date else None
            end = date.fromisoformat(str(end_date)) if end_date else None
        except (TypeError, ValueError) as exc:
            raise ToolError(f"start_date/end_date must be YYYY-MM-DD: {exc}") from exc
        if start and end and end < start:
            raise ToolError("end_date must not be before start_date")
        try:
            capped = max(0, min(int(limit), MAX_EVENT_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ToolError("limit must be an integer") from exc
        rows = []
        for event in self._user_events(user_id):
            event_day = _event_date(event)
            try:
                ordinal = date.fromisoformat(event_day) if event_day else None
            except ValueError:
                ordinal = None
            if start is not None and (ordinal is None or ordinal < start):
                continue
            if end is not None and (ordinal is None or ordinal > end):
                continue
            rows.append(event)
        return {
            "user_id": str(user_id),
            "count": len(rows[:capped]),
            "matched": len(rows),
            "events": rows[:capped],
        }

    @_structured
    def search_messages(self, user_id: str, query: str, limit: int = 10) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        try:
            capped = max(0, min(int(limit), MAX_EVENT_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ToolError("limit must be an integer") from exc
        tokens = [token for token in re.split(r"[^0-9a-z]+", query.lower()) if token]
        if not tokens:
            raise ToolError("query has no searchable terms")
        frame = self._bundle().messages
        try:
            rows = frame[frame["user_id"].astype(str) == str(user_id)].to_dict("records")
        except Exception as exc:
            raise ToolError(f"cannot read messages: {exc}") from exc
        scored = []
        for row in rows:
            text = " ".join(
                part
                for part in (_text(row.get("message_text")), _text(row.get("description")))
                if part
            ).lower()
            if not text:
                continue
            score = sum(1 for token in tokens if token in text)
            if score == 0:
                continue
            scored.append((score, row))
        scored.sort(
            key=lambda item: (
                -item[0],
                -_ordinal(_text(item[1].get("sent_at"))),
                _text(item[1].get("message_id")),
            )
        )
        citations = [
            {
                "message_id": _text(row.get("message_id")),
                "event_id": _text(row.get("related_event_id")),
                "sent_at": _text(row.get("sent_at")),
                "text": _text(row.get("message_text")),
            }
            for _, row in scored[:capped]
        ]
        return {"user_id": str(user_id), "count": len(citations), "citations": citations}

    @_structured
    def search_evidence(
        self,
        query: str,
        user_id: str | None = None,
        request_id: str | None = None,
        limit: int = 8,
    ) -> dict:
        """RAG-first evidence lookup: citations only, never authoritative numbers."""
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        try:
            capped = max(1, min(int(limit), MAX_EVENT_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ToolError("limit must be an integer") from exc
        if user_id is not None and not str(user_id).strip():
            raise ToolError("user_id must be a non-empty string when supplied")
        if request_id is not None and not str(request_id).strip():
            raise ToolError("request_id must be a non-empty string when supplied")

        warnings: list[dict] = []
        retriever = self.retriever
        if retriever is None:
            citations = self._lexical_citations(query, user_id=user_id, limit=capped)
            backend = "lexical"
        else:
            citations = list(
                retriever.search(
                    query,
                    user_id=str(user_id) if user_id else None,
                    request_id=str(request_id) if request_id else None,
                    limit=capped,
                )
                or []
            )
            backend = str(getattr(retriever, "last_backend", "fts") or "fts")
            warnings = [dict(item) for item in (getattr(retriever, "warnings", None) or [])]

        note_retrieval = getattr(self.trace, "note_retrieval", None)
        if callable(note_retrieval):
            note_retrieval(backend, warnings=warnings, query=query)

        return {
            "query": str(query),
            "backend": backend,
            "authoritative": False,
            "note": (
                "Citations are candidate evidence only. Amounts, dates and balances "
                "must come from get_financial_events / get_cashflow_projection."
            ),
            "warnings": warnings,
            "count": len(citations[:capped]),
            "citations": [_plain(citation) for citation in citations[:capped]],
        }

    def _lexical_citations(self, query: str, *, user_id=None, limit: int = 8) -> list[dict]:
        """Offline fallback when no retriever is configured (messages + events)."""
        tokens = [token for token in re.split(r"[^0-9a-z]+", query.lower()) if len(token) > 1]
        if not tokens:
            raise ToolError("query has no searchable terms")
        scored: list[tuple[int, str, dict]] = []
        for row in self._bundle().messages.to_dict("records"):
            if user_id and _text(row.get("user_id")) != str(user_id):
                continue
            text = _text(row.get("message_text"))
            if not text:
                continue
            score = sum(1 for token in tokens if token in text.lower())
            if score:
                scored.append(
                    (
                        score,
                        f"message:{_text(row.get('message_id'))}",
                        {
                            "chunk_id": f"message:{_text(row.get('message_id'))}",
                            "source_type": "message",
                            "source_id": _text(row.get("message_id")),
                            "user_id": _text(row.get("user_id")),
                            "request_id": _text(row.get("request_id")),
                            "text": text,
                            "metadata": {"sent_at": _text(row.get("sent_at"))},
                            "score": None,
                            "backend": "lexical",
                        },
                    )
                )
        if user_id:
            for event in self._user_events(user_id):
                text = _text(event.get("description"))
                if not text:
                    continue
                score = sum(1 for token in tokens if token in text.lower())
                if score:
                    scored.append(
                        (
                            score,
                            f"event:{_text(event.get('event_id'))}",
                            {
                                "chunk_id": f"event:{_text(event.get('event_id'))}",
                                "source_type": "event",
                                "source_id": _text(event.get("event_id")),
                                "user_id": _text(event.get("user_id")),
                                "request_id": "",
                                "text": text,
                                "metadata": {"category": _text(event.get("category"))},
                                "score": None,
                                "backend": "lexical",
                            },
                        )
                    )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [citation for _, _, citation in scored[:limit]]

    @_structured
    def get_cashflow_projection(self, request_id: str, spending_changes=None) -> dict:
        try:
            tokens = parse_spending_changes(self._change_text(spending_changes))
        except CandidateValidationError as exc:
            raise ToolError("; ".join(exc.errors)) from exc
        payload = dict(self._projection(request_id, tokens))
        payload.pop("ledger", None)
        return payload

    @_structured
    def get_payment_options(self, request_id: str) -> dict:
        request = self._request_row(request_id)
        profile = self._profile_row(request.get("user_id", ""))
        eligible = get_valid_options(request_id, profile, self._bundle().options)
        return {
            "request_id": str(request_id),
            "home_currency": _text(profile.get("home_currency")),
            "max_installment_months": _text(profile.get("max_installment_months")),
            "payment_methods_user_will_consider": _text(
                profile.get("payment_methods_user_will_consider")
            ),
            "supplied": self._option_rows(request_id),
            "eligible": [_plain(option) for option in eligible],
        }

    @_structured
    def list_spending_change_candidates(self, request_id: str) -> dict:
        request = self._request_row(request_id)
        user_id = request.get("user_id", "")
        actions = self._permitted_actions(user_id)
        base_ledger = self._projection(request_id, ()).get("ledger") or []
        candidates: list[dict] = []
        probed = 0
        for action in actions:
            entry = {
                "event_id": _text(action.get("event_id")),
                "action": _text(action.get("action")),
                "value": money_text(action.get("value", "0")),
                "category": _text(action.get("category")),
                "savings": money_text(action.get("savings", "0")),
                "date": _text(action.get("date")),
                "changes_forecast": None,
            }
            if base_ledger and probed < MAX_FORECAST_PROBES:
                probed += 1
                try:
                    projected = self._projection(
                        request_id,
                        ((entry["action"], entry["event_id"], action.get("value")),),
                    )
                except ToolError:
                    entry["changes_forecast"] = None
                else:
                    entry["changes_forecast"] = bool(
                        not projected.get("fail_closed")
                        and projected.get("ledger") != base_ledger
                    )
            candidates.append(entry)
        return {
            "request_id": str(request_id),
            "count": len(candidates),
            "forecast_probes": probed,
            "candidates": candidates,
        }

    @_structured
    def propose_spending_change(
        self,
        request_id: str,
        event_id: str,
        action: str,
        new_amount=None,
        rationale=None,
    ) -> dict:
        """Try one permitted change. Accepted only when the kernel says it helps."""
        request = self._request_row(request_id)
        user_id = request.get("user_id", "")
        kind = str(action or "").strip().lower()
        if kind not in ("stop", "reduce_to"):
            raise ToolError(f"action must be 'stop' or 'reduce_to', got {action!r}")
        permitted = {
            (_text(item.get("event_id")), _text(item.get("action"))): item
            for item in self._permitted_actions(user_id)
        }
        key = (_text(event_id), kind)
        if key not in permitted:
            allowed = sorted(
                {entry for entry in permitted if entry[0] == _text(event_id)}
            )
            if allowed:
                raise ToolError(
                    f"{event_id} cannot be {kind}; the user permits "
                    + ", ".join(sorted(item[1] for item in allowed))
                )
            raise ToolError(
                f"{event_id} is not a permitted spending change for this user "
                "(protected category, fixed cost, or outside the categories the "
                "user will change)"
            )
        entry = permitted[key]
        value = entry.get("value")
        if kind == "reduce_to":
            try:
                target = Decimal(str(new_amount).replace(",", ""))
            except (InvalidOperation, ValueError, TypeError):
                raise ToolError("reduce_to needs a numeric new_amount")
            # Compare against what the event costs today, not against the
            # permitted action's floor: the floor is the lower bound that
            # `_validate_change_tokens` enforces, so using it here as a ceiling
            # would make every reduce_to impossible.
            current = None
            for event in self._user_events(user_id):
                if _text(event.get("event_id")) != _text(event_id):
                    continue
                try:
                    current = Decimal(str(event.get("amount")).replace(",", ""))
                except (InvalidOperation, TypeError, ValueError):
                    current = None
                break
            if current is None:
                raise ToolError(
                    f"{event_id} has no readable current amount, so it cannot be reduced"
                )
            if target <= 0 or target >= current:
                raise ToolError(
                    f"new_amount must be above 0 and below the current {money_text(current)}"
                )
            value = target
        token = (
            ("stop", _text(event_id), None)
            if kind == "stop"
            else ("reduce_to", _text(event_id), value)
        )
        baseline = self._projection(request_id, ())
        projected = self._projection(request_id, (token,))
        if projected.get("fail_closed"):
            return {
                "request_id": str(request_id),
                "accepted": False,
                "reason": str(projected.get("fail_closed_reason") or "projection failed"),
                "changes": "none",
            }
        baseline_safe = Decimal(str(baseline.get("safe_amount") or "0"))
        safe = Decimal(str(projected.get("safe_amount") or "0"))
        requested = Decimal(str(request.get("requested_amount") or "0"))
        accepted = safe > baseline_safe
        change_text = (
            f"stop:{_text(event_id)}"
            if kind == "stop"
            else f"reduce_to:{_text(event_id)}:{money_text(value)}"
        )
        return {
            "request_id": str(request_id),
            "accepted": bool(accepted),
            "changes": change_text if accepted else "none",
            "category": _text(entry.get("category")),
            "baseline_safe_amount": money_text(baseline_safe),
            "safe_amount": money_text(safe),
            "covers_request": bool(safe >= requested),
            "earliest_date": _text(projected.get("earliest_date")),
            "reason": (
                "the kernel re-solved and this change raises the safe amount"
                if accepted
                else "the kernel re-solved and this change does not raise the safe amount"
            ),
            "rationale": _text(rationale),
        }

    @_structured
    def search_spending_changes(self, request_id: str) -> dict:
        """Rank permitted changes by the safe amount each one unlocks."""
        listing = self.list_spending_change_candidates(request_id)
        data = listing.get("data") if listing.get("ok") else {}
        rows = []
        for entry in (data or {}).get("candidates") or []:
            if not entry.get("changes_forecast"):
                continue
            try:
                projected = self._projection(
                    request_id,
                    ((
                        entry["action"],
                        entry["event_id"],
                        None if entry["action"] == "stop" else _amount_key(entry.get("value")),
                    ),),
                )
            except ToolError:
                continue
            rows.append(
                {
                    "action": entry["action"],
                    "event_id": entry["event_id"],
                    "category": entry.get("category"),
                    "safe_amount": money_text(projected.get("safe_amount") or "0"),
                    "earliest_date": _text(projected.get("earliest_date")),
                }
            )
        rows.sort(key=lambda item: _amount_key(item.get("safe_amount")), reverse=True)
        return {
            "request_id": str(request_id),
            "ranked": rows[:5],
            "count": len(rows),
        }

    @_structured
    def compare_payment_options(self, request_id: str) -> dict:
        """Arithmetic over the supplied options; no new terms are invented."""
        listing = self.get_payment_options(request_id)
        data = listing.get("data") if listing.get("ok") else {}
        eligible = {
            _text(option.get("payment_option_id"))
            for option in (data or {}).get("eligible") or []
        }
        rows = []
        priced = []
        for option in (data or {}).get("supplied") or []:
            option_id = _text(option.get("payment_option_id"))
            total = option.get("total_payable_amount")
            fee = option.get("financing_fee")
            try:
                total_value = Decimal(str(total).replace(",", ""))
            except (InvalidOperation, ValueError):
                total_value = None
            try:
                fee_value = Decimal(str(fee).replace(",", "")) if fee not in (None, "") else Decimal("0")
            except (InvalidOperation, ValueError):
                fee_value = None
            rows.append(
                {
                    "payment_option_id": option_id,
                    "payment_method": _text(option.get("payment_method")),
                    "number_of_payments": _text(option.get("number_of_payments")),
                    "total_payable_amount": money_text(total_value) if total_value is not None else _text(total),
                    "financing_fee": money_text(fee_value) if fee_value is not None else _text(fee),
                    "eligible": option_id in eligible,
                }
            )
            # An option whose total cannot be read is not the cheapest one; it
            # is an option with an unknown price.
            if option_id in eligible and total_value is not None:
                priced.append((total_value, option_id))
        priced.sort(key=lambda item: item[0])
        return {
            "request_id": str(request_id),
            "options": rows,
            "cheapest_eligible": priced[0][1] if priced else "",
            "eligible_count": len([row for row in rows if row["eligible"]]),
            "priced_eligible_count": len(priced),
        }

    @_structured
    def reconcile_receipts(self, user_id: str) -> dict:
        """Which events owe their amount to an image, and which are still unknown."""
        rows = []
        unresolved = []
        for event in self._bundle().events:
            if _text(event.get("user_id")) != _text(user_id):
                continue
            source = _text(event.get("amount_source"))
            amount = event.get("amount")
            if source == "linked_image_ocr":
                rows.append(
                    {
                        "event_id": _text(event.get("event_id")),
                        "amount": money_text(amount) if amount not in (None, "") else "",
                        "backend": _text(event.get("amount_evidence_backend")),
                        "image": _text(event.get("amount_evidence_id")),
                    }
                )
            elif amount in (None, "") or str(amount).strip().lower() in {"nan", "none", ""}:
                unresolved.append(
                    {
                        "event_id": _text(event.get("event_id")),
                        "category": _text(event.get("category")),
                        "date": _text(event.get("event_date")),
                    }
                )
        total = sum(
            (Decimal(str(row["amount"].replace(",", ""))) for row in rows if row["amount"]),
            Decimal("0"),
        )
        return {
            "user_id": str(user_id),
            "from_images": rows,
            "from_images_total": money_text(total),
            "unresolved": unresolved,
        }

    @_structured
    def raise_concern(self, kind: str, detail: str) -> dict:
        """Keep a concern on the toolbox; it never changes a number."""
        text = _text(kind)
        message = _text(detail)
        if not text or not message:
            raise ToolError("raise_concern needs both kind and detail")
        concerns = getattr(self, "concerns", None)
        if concerns is None:
            concerns = []
            self.concerns = concerns
        concerns.append({"kind": text, "detail": message})
        return {"kind": text, "detail": message, "count": len(concerns)}

    @_structured
    def propose_decision(self, candidate) -> dict:
        parsed, errors = parse_candidate(candidate)
        if parsed is None:
            raise ToolError("candidate rejected: " + "; ".join(errors))
        return {"valid": True, "candidate": parsed.as_dict()}

    @_structured
    def verify_decision(self, candidate) -> dict:
        parsed, _errors = parse_candidate(candidate)
        request_id = ""
        if parsed is not None:
            request_id = parsed.request_id
        elif isinstance(candidate, dict):
            request_id = _text(candidate.get("request_id"))
        else:
            request_id = _text(getattr(candidate, "request_id", ""))
        if not request_id:
            raise ToolError("candidate has no request_id to verify against")
        request = self._request_row(request_id)
        context = {
            "request_id": request_id,
            "request": request,
            "profile": self._profile_row(request.get("user_id", "")),
        }
        return enforce_decision(candidate, self.kernel_result_for(request_id), request_context=context)


def _ordinal(timestamp: str) -> int:
    """Sort key for an ISO timestamp; unparsable values sort last."""
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).toordinal()
    except (TypeError, ValueError):
        return 0


def build_toolbox(*args, **kwargs) -> Toolbox:
    return Toolbox(*args, **kwargs)


__all__ = [
    "DatasetBundle",
    "Toolbox",
    "ToolError",
    "ToolSpec",
    "TOOL_SPECS",
    "build_toolbox",
    "MAX_FORECAST_PROBES",
]
