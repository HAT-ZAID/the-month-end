"""Per-request JSON traces for the agentic layer (Task 5A).

Tracing is **opt-in**: ``TraceRecorder(run_id)`` is disabled and every method is
a no-op until an ``output_dir`` is supplied.  The default pipeline therefore
writes nothing new and stays byte-identical.

Determinism split:

* everything except ``metadata`` and ``duration_ms`` values is content-derived
  and byte-stable for the same inputs (use :func:`deterministic_view`);
* wall-clock timestamps and durations live only in the ``metadata`` block (and
  per-call ``duration_ms``), because they can never be compared across runs.

Secrets are redacted before anything is written: a mapping key whose *name
segment* is ``key``/``api_key``/``token``/``secret``/``password``/
``authorization`` (optionally underscore-delimited, case-insensitive) has its
value replaced.  Segment matching keeps counters such as ``total_tokens`` and
``input_tokens`` intact while still hiding ``api_key`` and ``access_token``.
Key-shaped substrings inside values (``sk-...``, ``Bearer ...``) are scrubbed
too, because provider errors and prompts are free text.

Task 5B adds the agent-loop blocks: ``model_turns`` (role, bounded text, tool
calls, per-turn usage), ``retrieval`` (which backend answered, plus fallback
warnings), ``budget`` (calls used/allowed) and the existing candidate,
verification, final row and fallback reason.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from code.schema import TraceEvent, stable_json

TRACE_SCHEMA_VERSION = 2
REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(?:^|[-_.])(?:api[-_.]?key|key|token|secret|password|passwd|passphrase"
    r"|pass|authorization|auth|bearer|credentials|creds|signature|sig"
    r"|accountkey|cookie|session[-_.]?id)(?:$|[-_.])",
    re.IGNORECASE,
)
#: API-key-shaped substrings inside *values* (provider errors, prompts, args).
#: Key-name matching above cannot see these, so they are scrubbed too.  ``\b``
#: keeps ordinary hyphenated words (``desk-booking``, ``risk-free``) intact while
#: still matching a key at a token boundary.
_SECRET_VALUE = re.compile(
    r"(?:\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|(?:Bearer|BEARER|bearer)\s+[A-Za-z0-9._\-+/=]{8,}"
    r"|(?:Basic|BASIC|basic)\s+[A-Za-z0-9+/=\-]{8,}"
    r"|\bAIza[A-Za-z0-9_-]{30,}"
    r"|\b(?:hf|npm|whsec)_[A-Za-z0-9]{16,})"
)
#: ``password=hunter2`` and friends, in any case.  Kept out of the pattern
#: above because an inline ``(?i:`` group reads as a drive letter to the
#: submission scan.
_PASSWORD_ASSIGNMENT = re.compile(
    r"(?:\b(?:received|invalid|rejected|missing|wrong|bad)\s+)?"
    # Bounded runs: an unbounded ``[a-z0-9]+`` group rescans the whole string at
    # every offset, so a long alphanumeric body costs quadratic time. The
    # repeats and each run are capped, and a camelCase tail counts as a run,
    # so "sessionId=..." and "passwordHash=..." are caught too.
    r"(?:[a-z0-9]{1,12}[_-]){0,4}(?:password|passwd|pwd|pass|passphrase|secret|token|sig|signature|accountkey"
    r"|api[\s_-]?key|session|access|key|cookie)"
    r"(?:[_-][a-z0-9]{1,12}|[a-z][a-z0-9]{1,12}){0,4}\b"
    r"[\"']?\s*[=:]\s*[\"']?[^\"'\n]+"
    r"|(?:set-)?cookie\s*:\s*[^\n]+"
    r"|[?&]sig=\S+"
    r"|\bcookie\s*=\s*\S+"
    # Both runs are bounded, and the value cannot contain ":" — without that
    # exclusion a colon-dense body ("a:1,a:1,…") restarts a user run at every
    # offset and then swallows the rest of the string as the value, which is
    # quadratic. With it, a scheme-less DSN still redacts, and the scheme form
    # is handled by _URL_USERINFO before this pattern runs.
    r"|(?<![\w.])([A-Za-z0-9._~%+-]{1,256}):([^\s@/:]{1,64})@(?=[A-Za-z0-9\[])"
    # A provider error can quote a JSON body, where the name sits inside quotes
    # and the separator is a colon inside them.
    r"|\"\w*(?:api|access|session|refresh|client|private|secret|id)?(?:key|token|secret)\"\s*:\s*\"[^\"]+",
    re.IGNORECASE,
)
#: The prose form: "the api key for abcdef is wrong". It needs no separator, so
#: it runs on every string, while the assignment pattern runs only when the
#: string actually carries one.
_PASSWORD_PROSE = re.compile(
    r"\b(?:api[\s_-]?key|token|secret|password)\b\s+(?:for|is|was)\s+[^.;\n]+",
    re.IGNORECASE,
)
#: ``user:password@`` inside a URL.  A gateway may legitimately carry basic-auth
#: credentials in its base URL, and a provider error is persisted verbatim, so
#: the userinfo part has to go even though it matches no key-name pattern.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]+@")
#: CamelCase compounds (``apiKey``, ``sessionToken``, ``clientSecret``) have no
#: separator for the pattern above to anchor on, so they are matched separately.
_CAMEL_SECRET_KEY = re.compile(
    r"(?:api|access|session|refresh|private|secret|client|auth|id|cookie|set)(?:key|token|secret|cookie)",
    re.IGNORECASE,
)
_UNSTABLE_KEYS = frozenset({"duration_ms"})
_UNSTABLE_BLOCKS = frozenset({"metadata"})
_UNSAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
MAX_TEXT_CHARS = 1200


#: Literal secrets this process holds.  A provider error can quote the key it
#: rejected, and a trace outlives the process, so the exact strings are
#: registered by whoever holds them and replaced before any shape is guessed.
_KNOWN_SECRETS: set[str] = set()


def register_secret(value) -> None:
    """Remember a literal secret so every later redaction can replace it."""
    text = str(value or "").strip()
    if len(text) >= 8:
        _KNOWN_SECRETS.add(text)


def _safe_name(value) -> str:
    name = _UNSAFE_FILE_CHARS.sub("_", str(value)).strip("._") or "unknown"
    return name[:120]


def sanitize(value):
    """Recursively redact secret-like keys and key-shaped values (case-insensitive)."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = _scrub_text(str(key))
            cleaned[key_text] = (
                        REDACTED
                        if _SECRET_KEY.search(str(key))
                        or _CAMEL_SECRET_KEY.search(str(key))
                        else sanitize(item)
                    )
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return sanitize(value.as_dict())
    if isinstance(value, (set, frozenset)):
        return [sanitize(item) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


_PEM_BEGIN = re.compile(
    r"-{4,5}\s?BEGIN\s+[A-Z0-9\s]*PRIVATE KEY[A-Z\s]*\s?-{4,5}", re.IGNORECASE
)
_PEM_END = re.compile(
    r"-{4,5}\s?END\s+[A-Z0-9\s]*PRIVATE KEY[A-Z\s]*\s?-{4,5}", re.IGNORECASE
)
#: A PuTTY key carries no armour: the body follows ``Private-Lines:``.
_PUTTY_PRIVATE = re.compile(
    r"private-lines\s*:\s*\d+\s*(?:\n\s*[A-Za-z0-9+/=]{16,})+", re.IGNORECASE
)


def _redact_pem(text: str) -> str:
    """Replace each armoured private key, whatever its length.

    A regex with a bounded interior silently leaves a long key in place, and an
    unbounded one scans quadratically, so the block is found and cut directly.
    """
    pieces = []
    position = 0
    while True:
        begin = _PEM_BEGIN.search(text, position)
        if begin is None:
            pieces.append(text[position:])
            return "".join(pieces)
        pieces.append(text[position: begin.start()])
        pieces.append(REDACTED)
        end = _PEM_END.search(text, begin.end())
        if end is None:
            # No terminator: drop the tail rather than ship it.
            return "".join(pieces)
        position = end.end()


def _scrub_text(value: str) -> str:
    """One string, with every known shape and registered literal replaced."""
    cleaned = _URL_USERINFO.sub(f"{REDACTED}@", value)
    # Count-bounded: a hostile body can repeat a BEGIN marker thousands of
    # times, and each one costs a window scan.
    cleaned = _PUTTY_PRIVATE.sub(REDACTED, cleaned)
    cleaned = _redact_pem(cleaned)
    cleaned = _SECRET_VALUE.sub(REDACTED, cleaned)
    for secret in _KNOWN_SECRETS:
        if secret in cleaned:
            cleaned = cleaned.replace(secret, REDACTED)
    cleaned = _PASSWORD_PROSE.sub(REDACTED, cleaned)
    # Every remaining branch needs an assignment or a userinfo marker, so a body
    # carrying none of those characters is not rescanned at all.
    if "=" in cleaned or ":" in cleaned or "@" in cleaned:
        cleaned = _PASSWORD_ASSIGNMENT.sub(REDACTED, cleaned)
    return cleaned


def _summary_text(value) -> str:
    """Bounded one-line text for prompts/responses stored in a trace."""
    text = " ".join(str(value or "").split())
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[: MAX_TEXT_CHARS - 1] + "\u2026"


def deterministic_view(payload):
    """Strip wall-clock metadata so two equivalent runs compare byte-for-byte."""
    if isinstance(payload, dict):
        return {
            key: deterministic_view(item)
            for key, item in payload.items()
            if str(key) not in _UNSTABLE_KEYS and str(key) not in _UNSTABLE_BLOCKS
        }
    if isinstance(payload, list):
        return [deterministic_view(item) for item in payload]
    return payload


def _as_payload(payload) -> dict:
    """Coerce a recorded payload to a mapping.

    ``record`` is called with dicts almost everywhere, but a sequence is the
    natural shape for a list of concerns, and ``dict([...])`` raises on it - a
    failure the observability guard swallows, taking the whole trace with it.
    """
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, (list, tuple, set, frozenset)):
        return {"items": list(payload)}
    return {"value": payload}


class TraceRecorder:
    """Collect events for one request and, when enabled, write one JSON file."""

    def __init__(self, run_id, output_dir=None):
        self.run_id = str(run_id)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self._request_id: str | None = None
        self._events: list[TraceEvent] = []
        self._tool_calls: list[dict] = []
        self._model_turns: list[dict] = []
        self._retrieval: dict = {"backend": None, "warnings": [], "queries": 0}
        self._started: float | None = None

    @property
    def enabled(self) -> bool:
        return self.output_dir is not None

    def begin(self, request_id) -> None:
        self._request_id = str(request_id)
        self._events = []
        self._tool_calls = []
        self._model_turns = []
        self._retrieval = {"backend": None, "warnings": [], "queries": 0}
        self._started = time.perf_counter()
        self.record("request_start", {"request_id": self._request_id})

    def record(self, kind, payload=None):
        if not self.enabled:
            return None
        event = TraceEvent(kind=str(kind), payload=sanitize(_as_payload(payload)))
        self._events.append(event)
        return event

    def note_retrieval(self, backend, *, warnings=None, query=None):
        """Remember which retrieval backend actually answered (Task 5B)."""
        if not self.enabled:
            return None
        self._retrieval["backend"] = str(backend or "none")
        self._retrieval["queries"] = int(self._retrieval.get("queries") or 0) + 1
        existing = list(self._retrieval.get("warnings") or [])
        for warning in sanitize(list(warnings or [])):
            if isinstance(warning, dict) and warning not in existing:
                existing.append(warning)
        self._retrieval["warnings"] = existing
        return self.record(
            "retrieval",
            {
                "backend": self._retrieval["backend"],
                "query": _summary_text(query),
                "warnings": existing,
            },
        )

    def retrieval_summary(self) -> dict:
        """Snapshot of the retrieval block for :meth:`finish` (Task 5B)."""
        return {
            "backend": self._retrieval.get("backend"),
            "warnings": list(self._retrieval.get("warnings") or []),
            "queries": int(self._retrieval.get("queries") or 0),
        }

    def record_model_turn(self, role, text=None, *, tool_calls=None, usage=None, latency_ms=None):
        """Append one chat turn (role, bounded text, tool calls) to the trace."""
        if not self.enabled:
            return None
        turn = {
            "role": str(role),
            "text": _summary_text(text),
            "tool_calls": [
                {
                    "name": str(call.get("name")),
                    "args": sanitize(dict(call.get("args") or {})),
                }
                for call in list(tool_calls or [])
                if isinstance(call, dict)
            ],
        }
        if usage:
            turn["usage"] = sanitize(dict(usage))
        if latency_ms is not None:
            turn["duration_ms"] = round(float(latency_ms), 3)
        self._model_turns.append(turn)
        return turn

    def record_tool_call(self, name, args=None, *, ok, error=None, duration_ms=None):
        if not self.enabled:
            return None
        call = {
            "name": str(name),
            "args": sanitize(dict(args or {})),
            "ok": bool(ok),
            "error": None if error is None else str(error),
        }
        if duration_ms is not None:
            call["duration_ms"] = round(float(duration_ms), 3)
        self._tool_calls.append(call)
        return call

    def finish(
        self,
        *,
        request_id=None,
        provider="none",
        model="none",
        prompt_version="",
        prompt_hash="",
        candidate=None,
        verification=None,
        final_row=None,
        usage=None,
        fallback_reason=None,
        model_turns=None,
        retrieval=None,
        budget=None,
    ) -> str | None:
        """Write the trace and return its path (``None`` when disabled)."""
        if not self.enabled:
            return None
        request_id = str(request_id or self._request_id or "unknown")
        duration_ms = None
        if self._started is not None:
            duration_ms = round((time.perf_counter() - self._started) * 1000.0, 3)
        turns = list(self._model_turns) if model_turns is None else list(model_turns)
        retrieval_block = dict(self._retrieval) if retrieval is None else dict(retrieval)
        payload = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "request_id": request_id,
            "provider": str(provider),
            "model": str(model),
            "prompt_version": str(prompt_version),
            "prompt_hash": str(prompt_hash),
            "events": [event.as_dict() for event in self._events],
            "model_turns": turns,
            "tool_calls": list(self._tool_calls),
            "retrieval": retrieval_block,
            "candidate": candidate,
            "verification": verification,
            "final_row": final_row,
            "usage": dict(usage or {}),
            "budget": dict(budget or {}),
            "fallback_reason": fallback_reason,
            "metadata": {
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "duration_ms": duration_ms,
                "tool_calls": len(self._tool_calls),
                "model_turns": len(turns),
            },
        }
        payload = sanitize(payload)
        path = self.output_dir / _safe_name(self.run_id) / f"{_safe_name(request_id)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stable_json(payload) + "\n", encoding="utf-8", newline="\n")
        return str(path)


__all__ = [
    "TraceRecorder",
    "deterministic_view",
    "sanitize",
    "REDACTED",
    "TRACE_SCHEMA_VERSION",
]
