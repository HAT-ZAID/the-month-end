"""Jev decision gate: a typed second opinion from OpenCode Zen (System One).

Jev is not a chat model.  It takes a compact ``state`` plus typed questions and
returns typed answers with probabilities — choice, noul or score.  This module
uses it as an **optional, fail-open audit gate** around the deterministic kernel:

* It never changes a machine field.  The kernel owns every number, and this
  module has no write path to any row.
* It records *concordance*: did the decision model, shown the same facts, pick
  the same affordability status and payment method as the kernel?  Anything the
  experiment learns about disagreements is written to docs, not to rows.
* Errors are fail-open: a missing key, a refused request, a network failure or a
  malformed answer degrades to ``JevOutcome(error=...)`` and the pipeline continues.

Credentials: the key comes from ``OPENCODE_API_KEY`` when set, otherwise from the
OpenCode desktop client's ``auth.json`` (``.opencode`` entry).  The key is never
logged and never returned; :class:`JevGate.describe` reports only its source.

Endpoint contract (System One):

``POST`` the JSON body ``{"model", "state", "questions": {qid: {type,
instructions, criteria}}}`` to ``https://opencode.ai/inference/systemone/v1/systemone``
and read back ``{"answers": {qid: {"type", "choice"|"noul"|"score"?,
"probabilities"?}}, "usage": {...}}``.

Environment:

==========================  ==============================================
``BUY_WAIT_JEV_MODEL``      default ``jev-1.13-free`` (free tier)
``BUY_WAIT_JEV_BASE_URL``   default: the System One endpoint above
``BUY_WAIT_JEV_TIMEOUT``    seconds, default 60
``OPENCODE_API_KEY``        explicit key; otherwise ``auth.json`` is read
``OPENCODE_AUTH_JSON``      override of the auth.json path
==========================  ==============================================
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SYSTEMONE_URL = "https://opencode.ai/inference/systemone/v1/systemone"
JEV_BASE_URL_ENV = "BUY_WAIT_JEV_BASE_URL"
JEV_MODEL_ENV = "BUY_WAIT_JEV_MODEL"
JEV_TIMEOUT_ENV = "BUY_WAIT_JEV_TIMEOUT"
OPENCODE_API_KEY_ENV = "OPENCODE_API_KEY"
AUTH_JSON_PATH_ENV = "OPENCODE_AUTH_JSON"
def _default_auth_json() -> Path:
    """The OpenCode CLI's own auth.json location, derived from the environment.

    The CLI stores credentials under the user data home. ``XDG_DATA_HOME`` can
    move it the standard way; otherwise the platform user profile root is used.
    Deriving from the environment keeps the code free of machine-specific paths.
    """
    data_home = os.environ.get("XDG_DATA_HOME") or Path(
        os.environ.get("USERPROFILE") or os.environ.get("HOME", ".")
    ) / ".local" / "share"
    return Path(data_home) / "opencode" / "auth.json"


DEFAULT_AUTH_JSON = _default_auth_json()
DEFAULT_MODEL = "jev-1.13-free"
DEFAULT_TIMEOUT = 60.0
#: The decision endpoint requires a normal client User-Agent; its edge
#: protection answers the bare Python signature with HTTP 403 "error code: 1010".
HTTP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

STATUS_OPTIONS = (
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
)
METHOD_OPTIONS = (
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
)


def _text(value) -> str:
    return str(value or "").strip()


def _as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


@dataclass
class JevOutcome:
    """One gate call: answers, usage, or a fail-open error."""

    answers: dict = field(default_factory=dict)  # qid -> JevAnswer
    usage: dict = field(default_factory=dict)
    model: str = ""
    latency_ms: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class JevAnswer:
    qid: str
    type: str = ""
    value: str | float | None = None
    probabilities: dict | None = None
    raw: dict | None = None


def load_api_key(env=None) -> tuple[str | None, str]:
    """Return ``(key, source)``; ``(None, reason)`` when no key exists."""
    env = os.environ if env is None else env
    from_env = _text(env.get(OPENCODE_API_KEY_ENV))
    if from_env:
        return from_env, "environment"
    path = Path(_text(env.get(AUTH_JSON_PATH_ENV)) or DEFAULT_AUTH_JSON)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"no {OPENCODE_API_KEY_ENV} and auth.json unreadable"
    entry = payload.get("opencode")
    if not isinstance(entry, dict):
        return None, "auth.json has no opencode entry"
    key = _text(entry.get("key") or entry.get("api_key") or entry.get("token"))
    if not key:
        return None, "auth.json opencode entry has no key"
    return key, f"auth.json ({path.name})"


class JevGate:
    """Typed decision questions against the free Zen System One endpoint."""

    def __init__(self, *, model=None, base_url=None, timeout=None, env=None):
        env = os.environ if env is None else env
        self.model = _text(model or env.get(JEV_MODEL_ENV)) or DEFAULT_MODEL
        self.base_url = _text(base_url or env.get(JEV_BASE_URL_ENV)) or SYSTEMONE_URL
        try:
            self.timeout = float(_text(env.get(JEV_TIMEOUT_ENV)) or DEFAULT_TIMEOUT)
        except ValueError:
            self.timeout = DEFAULT_TIMEOUT

    # -- identity ---------------------------------------------------------
    def describe(self) -> dict:
        key, source = load_api_key()
        # The key itself is never reported, only where it came from.
        return {
            "model": self.model,
            "base_url": self.base_url,
            "available": key is not None,
            "key_source": source,
            "timeout_s": self.timeout,
        }

    # -- request ----------------------------------------------------------
    def _body(self, state, questions) -> dict:
        clean = {}
        for qid, spec in dict(questions or {}).items():
            spec = dict(spec or {})
            entry = {"type": _text(spec.get("type")) or "noul"}
            for field_name in ("instructions", "criteria", "options", "lower", "upper", "concept"):
                if spec.get(field_name) is not None:
                    entry[field_name] = spec[field_name]
            clean[str(qid)] = entry
        return {"model": self.model, "state": state, "questions": clean}

    def ask(self, state, questions, *, api_key=None) -> JevOutcome:
        """Ask typed questions; never raise for network or API problems."""
        started = time.monotonic()
        key, source_err = (api_key, None) if api_key else load_api_key()
        if not key:
            return JevOutcome(model=self.model, error=f"no API key: {source_err}")
        payload = self._body(state, questions)
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                # The endpoint's edge protection rejects (HTTP 403 "error code:
                # 1010") requests signed only with Python's own urllib defaults
                # to a plain browser user-agent.
                "User-Agent": HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    return JevOutcome(model=self.model, error=f"HTTP {status}")
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # The response body may name the real problem; it never contains the key.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return JevOutcome(model=self.model, error=f"HTTP {exc.code}: {detail}".strip())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return JevOutcome(model=self.model, error=f"request failed: {type(exc).__name__}")
        latency = int((time.monotonic() - started) * 1000)
        try:
            envelope = json.loads(body)
        except ValueError:
            return JevOutcome(model=self.model, error="malformed response", latency_ms=latency)
        if not isinstance(envelope, dict):
            return JevOutcome(model=self.model, error="unexpected response shape", latency_ms=latency)
        answers = _parse_answers(envelope.get("answers"))
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return JevOutcome(
            answers=answers,
            usage={
                "input_tokens": _as_int(usage.get("input_tokens")),
                "output_tokens": _as_int(usage.get("output_tokens")),
                "cost": usage.get("cost"),
                "billing_source": usage.get("opencode", {}).get("billing_source")
                if isinstance(usage.get("opencode"), dict)
                else None,
            },
            model=_text(envelope.get("model")) or self.model,
            latency_ms=latency,
        )


def _as_int(value):
    number = _as_float(value)
    return int(number) if number is not None else None


def _parse_answers(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    answers = {}
    for qid, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        qid = str(qid)
        qtype = _text(raw.get("type"))
        if qtype == "choice":
            value = _text(raw.get("choice")) or None
        else:
            number = _as_float(raw.get(qtype) if qtype else raw.get("noul") or raw.get("score"))
            value = number
        probabilities = raw.get("probabilities")
        if not isinstance(probabilities, dict):
            probabilities = None
        answers[qid] = JevAnswer(
            qid=qid,
            type=qtype,
            value=value,
            probabilities=probabilities if not probabilities else {str(k): v for k, v in probabilities.items()},
            raw=raw,
        )
    return answers


# -- Question presets ------------------------------------------------------
# JEV's core primitive is noul: the probability that a stated proposition is
# true.  The free decision model serves noul only and answers every
# choice/score question with HTTP 422 "Endpoint is unavailable"; even the paid
# tier is a separate account decision.  So the gate asks one noul question per
# candidate value and derives the implied choice by taking the maximum.
#
# Encoding: "status_is_<status>" x4, "method_is_<method>" x5, plus safety and
# evidence confidence - all noul, all in a single call per request.

def gate_questions() -> dict:
    """Eleven noul propositions the comparison asks about every solved row."""
    questions: dict = {}
    for status in STATUS_OPTIONS:
        questions[f"status_is_{status}"] = {
            "type": "noul",
            "instructions": f"Probability that the correct affordability_status = {status}.",
        }
    for method in METHOD_OPTIONS:
        questions[f"method_is_{method}"] = {
            "type": "noul",
            "instructions": (
                f"Probability that the correct recommended_payment_method = {method}. "
                "not_recommended means do not proceed."
            ),
        }
    questions["decision_safety"] = {
        "type": "noul",
        "instructions": (
            "Probability that the recorded decision keeps the balance above the "
            "minimum after every commitment."
        ),
    }
    questions["evidence_sufficiency"] = {
        "type": "noul",
        "instructions": (
            "Probability that the supplied facts are sufficient to judge the "
            "decision safely without extra evidence."
        ),
    }
    return questions


def implied_choice(outcome: JevOutcome, prefix: str, values) -> tuple[str | None, float | None]:
    """Translate noul propositions back into one chosen value and its probability.

    Returns ``(None, None)`` when the outcome failed or no proposition answered,
    so callers can distinguish "JEV had no opinion" from "JEV agreed".
    """
    if not outcome.ok:
        return None, None
    best_value, best_prob = None, None
    for value in values:
        answer = outcome.answers.get(f"{prefix}{value}")
        if answer is None or answer.value is None:
            continue
        try:
            probability = float(answer.value)
        except (TypeError, ValueError):
            continue
        if best_prob is None or probability > best_prob:
            best_value, best_prob = value, probability
    return best_value, best_prob


def evaluate(gate: JevGate, state, *, api_key=None) -> JevOutcome:
    """One call with the full question preset; fail-open by design."""
    return gate.ask(state, gate_questions(), api_key=api_key)


def summarize(outcome: JevOutcome) -> dict:
    """A safe, serializable view of one outcome (never the key, never an error leak)."""
    view = {"ok": outcome.ok, "model": outcome.model, "latency_ms": outcome.latency_ms}
    if not outcome.ok:
        view["error"] = outcome.error
        return view
    view["answers"] = {
        qid: {"type": answer.type, "value": answer.value}
        for qid, answer in outcome.answers.items()
    }
    view["usage"] = outcome.usage
    return view


__all__ = [
    "AUTH_JSON_PATH_ENV",
    "DEFAULT_AUTH_JSON",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "JevAnswer",
    "JevGate",
    "JevOutcome",
    "JEV_BASE_URL_ENV",
    "JEV_MODEL_ENV",
    "JEV_TIMEOUT_ENV",
    "METHOD_OPTIONS",
    "OPENCODE_API_KEY_ENV",
    "STATUS_OPTIONS",
    "METHOD_OPTIONS",
    "implied_choice",
    "gate_questions",
    "SYSTEMONE_URL",
    "evaluate",
    "gate_questions",
    "load_api_key",
    "summarize",
]
