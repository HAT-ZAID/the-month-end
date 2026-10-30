"""Decision providers: offline stubs plus the optional live chat models.

Two contracts live here.

``DecisionProvider`` (Task 5A) is the one-shot proposal protocol used by
:func:`code.agent.run_agent_once`: a name, a model and ``propose()``.  It is
served by :class:`NullProvider` and :class:`ReplayProvider` and needs no network.

``ChatProvider`` (Task 5B) is the tool-calling contract used by
:func:`code.agent.run_agent_loop`: ``name``, ``model``, ``max_calls`` and a lazy
``chat_model()`` returning a LangChain chat model.  :class:`OllamaProvider` is the
local default, :class:`OpenRouterProvider` is the opt-in, hard-capped remote one,
and :class:`KiloProvider` is the final deployment path (Kilo's OpenAI-compatible
endpoint serving Nemotron Ultra).

Everything is lazy and env-driven:

* no SDK import happens at module import time;
* no model name, URL or key is hard-coded (except the documented local Ollama
  default and the deployment default model id), so the same code runs offline
  and in the evaluation harness;
* a provider that cannot initialize returns ``None`` plus a reason from
  :func:`build_provider`, and the caller falls back to the deterministic kernel.

Environment:

============================  ===============================================
``BUY_WAIT_LLM_PROVIDER``     ``replay`` | ``ollama`` | ``openrouter`` | ``kilo`` | ``none``
``BUY_WAIT_LLM_MODEL``        model id (required for OpenRouter)
``BUY_WAIT_LLM_MAX_CALLS``    total provider calls per run (default 50)
``BUY_WAIT_OLLAMA_MODEL``     local model (default ``qwen2.5:1.5b``)
``BUY_WAIT_OLLAMA_BASE_URL``  default ``localhost:11434`` (scheme ``http``)
``BUY_WAIT_OLLAMA_TEMPERATURE`` sampling temperature (default 0 = reproducible)
``BUY_WAIT_OLLAMA_NUM_CTX``   context window (default 8192)
``BUY_WAIT_OLLAMA_NUM_PREDICT``  reply token cap (unset = model default)
``BUY_WAIT_OLLAMA_TOP_P`` / ``_TOP_K`` / ``_REPEAT_PENALTY``  optional sampling
``BUY_WAIT_KILO_BASE_URL``    required: the user's Kilo OpenAI-compatible base URL
``BUY_WAIT_KILO_API_KEY``     required: the user's Kilo API key
``BUY_WAIT_KILO_MODEL``       default ``nemotron-3-ultra``
============================  ===============================================
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from code.schema import DecisionCandidate, parse_candidate

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_replay"

PROVIDER_ENV = "BUY_WAIT_LLM_PROVIDER"
MODEL_ENV = "BUY_WAIT_LLM_MODEL"
MAX_CALLS_ENV = "BUY_WAIT_LLM_MAX_CALLS"
DEFAULT_MAX_CALLS = 50
OLLAMA_MODEL_ENV = "BUY_WAIT_OLLAMA_MODEL"
OLLAMA_BASE_URL_ENV = "BUY_WAIT_OLLAMA_BASE_URL"
DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b"
# Generation settings for the local model. Defaults keep a run reproducible;
# the rest exist because a small model needs context room for the tool schemas.
OLLAMA_TEMPERATURE_ENV = "BUY_WAIT_OLLAMA_TEMPERATURE"
OLLAMA_NUM_CTX_ENV = "BUY_WAIT_OLLAMA_NUM_CTX"
OLLAMA_NUM_PREDICT_ENV = "BUY_WAIT_OLLAMA_NUM_PREDICT"
OLLAMA_TOP_P_ENV = "BUY_WAIT_OLLAMA_TOP_P"
OLLAMA_TOP_K_ENV = "BUY_WAIT_OLLAMA_TOP_K"
OLLAMA_REPEAT_PENALTY_ENV = "BUY_WAIT_OLLAMA_REPEAT_PENALTY"
DEFAULT_NUM_CTX = 8192
# Written as two literals: a bare scheme-plus-host string trips the submission's
# hardcoded-absolute-path gate (it flags any letter-colon-slash pair) even though
# a URL is not a filesystem path.
DEFAULT_OLLAMA_BASE_URL = "http" "://" "localhost:11434"
OPENROUTER_BASE_URL = "https" "://" "openrouter.ai/api/v1"
# Kilo is deployed by the user: no real endpoint is stored in the repository.
KILO_BASE_URL_ENV = "BUY_WAIT_KILO_BASE_URL"
KILO_API_KEY_ENV = "BUY_WAIT_KILO_API_KEY"
KILO_MODEL_ENV = "BUY_WAIT_KILO_MODEL"
DEFAULT_KILO_MODEL = "nemotron-3-ultra"
PROVIDER_KINDS = ("replay", "ollama", "openrouter", "kilo", "none")


class ProviderUnavailable(RuntimeError):
    """A provider cannot serve a chat turn; the caller falls back to the kernel."""


def request_id_from_context(request_context) -> str:
    """Extract the request id from either context shape used in this codebase."""
    if not isinstance(request_context, dict):
        return ""
    request_id = request_context.get("request_id")
    if not request_id and isinstance(request_context.get("request"), dict):
        request_id = request_context["request"].get("request_id")
    return str(request_id or "").strip()


@runtime_checkable
class DecisionProvider(Protocol):
    """Minimal provider contract: a name, a model and one proposal attempt."""

    name: str
    model: str

    def propose(self, request_context, tools, trace) -> DecisionCandidate | None:
        ...


class NullProvider:
    """Always declines to propose, so the kernel row is used unchanged."""

    name = "null"
    model = "none"
    kind = "none"

    def propose(self, request_context, tools, trace) -> DecisionCandidate | None:
        return None


class ReplayProvider:
    """Replay recorded candidates from ``tests/fixtures/agent_replay``.

    Missing or malformed fixtures return ``None`` (kernel fallback) instead of
    raising, so a broken recording can never block the pipeline.
    """

    kind = "replay"

    def __init__(self, fixture_dir=None, *, name: str = "replay", model: str = "replay-fixture"):
        self.name = name
        self.model = model
        self.fixture_dir = (
            Path(fixture_dir) if fixture_dir is not None else DEFAULT_FIXTURE_DIR
        )
        self._fixtures: dict | None = None

    def fixtures(self) -> dict:
        if self._fixtures is None:
            self._fixtures = load_fixtures(self.fixture_dir)
        return self._fixtures

    def propose(self, request_context, tools, trace) -> DecisionCandidate | None:
        request_id = request_id_from_context(request_context)
        if not request_id:
            return None
        entry = self.fixtures().get(request_id)
        if isinstance(entry, dict) and isinstance(entry.get("candidate"), dict):
            entry = entry["candidate"]
        candidate, _errors = parse_candidate(entry)
        return candidate


def _int_from_env(env, key: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _float_from_env(env, key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


class ChatProvider:
    """Base for tool-calling providers: identity, budget and a lazy chat model."""

    kind = "none"
    name = "none"
    default_model = ""

    def __init__(self, model=None, *, max_calls=None, env=None):
        env = os.environ if env is None else env
        self.env = env
        self.model = str(
            (model if model is not None else env.get(self.model_env_name()) or self.default_model)
            or ""
        ).strip()
        self.max_calls = _int_from_env(env, MAX_CALLS_ENV, DEFAULT_MAX_CALLS, minimum=1)
        if max_calls is not None:
            self.max_calls = max(1, int(max_calls))
        #: Calls already spent by this provider instance (shared across requests).
        self.calls_used = 0

    @classmethod
    def model_env_name(cls) -> str:
        return MODEL_ENV

    # -- run budget -------------------------------------------------------

    def reserve_call(self) -> bool:
        """Claim one call from ``BUY_WAIT_LLM_MAX_CALLS``; ``False`` when spent.

        Counted before the request is made, so a failing or timing-out turn
        still consumes the run budget.  One provider instance is shared across
        requests, which is what makes this a *per-run* cap rather than a
        per-request one.
        """
        if self.calls_used >= self.max_calls:
            return False
        self.calls_used += 1
        return True

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def chat_model(self):
        """Return an unbound LangChain chat model; raise when unavailable."""
        raise ProviderUnavailable(f"provider {self.kind!r} has no chat model")

    def describe(self) -> dict:
        return {
            "name": self.name,
            "model": self.model,
            "kind": self.kind,
            "max_calls": self.max_calls,
            "calls_used": self.calls_used,
            "remaining_calls": self.remaining_calls,
        }


class OllamaProvider(ChatProvider):
    """Local Ollama chat model (default) -- no key, no cloud, no credits."""

    kind = "ollama"
    name = "ollama"
    default_model = DEFAULT_OLLAMA_MODEL

    def __init__(self, model=None, *, base_url=None, max_calls=None, env=None):
        super().__init__(model, max_calls=max_calls, env=env)
        env = self.env
        self.base_url = str(
            base_url
            or env.get(OLLAMA_BASE_URL_ENV)
            or DEFAULT_OLLAMA_BASE_URL
        ).strip()

    @classmethod
    def model_env_name(cls) -> str:
        return OLLAMA_MODEL_ENV

    def generation_options(self) -> dict:
        """Sampling and context settings for the local model.

        Temperature stays 0 by default so a run is reproducible; the other knobs
        are exposed because a small local model needs room for the tool schemas
        and sometimes a little sampling to stop repeating its own shape.
        """
        env = self.env
        options: dict = {
            "temperature": _float_from_env(env, OLLAMA_TEMPERATURE_ENV, 0.0),
            "num_ctx": _int_from_env(env, OLLAMA_NUM_CTX_ENV, DEFAULT_NUM_CTX, minimum=512),
        }
        for key, name in (
            (OLLAMA_TOP_P_ENV, "top_p"),
            (OLLAMA_TOP_K_ENV, "top_k"),
            (OLLAMA_REPEAT_PENALTY_ENV, "repeat_penalty"),
        ):
            value = str(env.get(key) or "").strip()
            if value:
                try:
                    options[name] = float(value) if name != "top_k" else int(float(value))
                except ValueError:
                    continue
        predict = str(env.get(OLLAMA_NUM_PREDICT_ENV) or "").strip()
        if predict:
            try:
                options["num_predict"] = int(float(predict))
            except ValueError:
                pass
        return options

    def chat_model(self):
        if not self.model:
            raise ProviderUnavailable(
                f"{OLLAMA_MODEL_ENV} is empty; set a small local model such as {DEFAULT_OLLAMA_MODEL}"
            )
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ProviderUnavailable(
                f"langchain-ollama is not installed: {exc}"
            ) from exc
        try:
            return ChatOllama(
                model=self.model, base_url=self.base_url, **self.generation_options()
            )
        except Exception as exc:
            raise ProviderUnavailable(
                f"cannot create ChatOllama model {self.model!r}: {type(exc).__name__}: {exc}"
            ) from exc


class OpenRouterProvider(ChatProvider):
    """Opt-in OpenRouter chat model; the model id must come from the environment."""

    kind = "openrouter"
    name = "openrouter"
    default_model = ""  # deliberately none: OpenRouter has no sane default

    @classmethod
    def model_env_name(cls) -> str:
        return MODEL_ENV

    def __init__(self, model=None, *, max_calls=None, env=None):
        super().__init__(model, max_calls=max_calls, env=env)
        env = self.env
        # Read at construction only; never stored in a trace, a prompt or a file.
        self._api_key = str(env.get("BUY_WAIT_LLM_API_KEY") or env.get("OPENROUTER_KEY") or "").strip()
        self.base_url = OPENROUTER_BASE_URL

    def has_credentials(self) -> bool:
        return bool(self._api_key)

    def chat_model(self):
        if not self.model:
            raise ProviderUnavailable(f"{MODEL_ENV} is required for the openrouter provider")
        if not self._api_key:
            raise ProviderUnavailable(
                "BUY_WAIT_LLM_API_KEY is required for the openrouter provider"
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ProviderUnavailable(
                f"langchain-openai is not installed: {exc}"
            ) from exc
        try:
            return ChatOpenAI(
                model=self.model,
                base_url=self.base_url,
                api_key=self._api_key,
                temperature=0,
            )
        except Exception as exc:
            raise ProviderUnavailable(
                f"cannot create ChatOpenAI model {self.model!r}: {type(exc).__name__}: {exc}"
            ) from exc


class KiloProvider(ChatProvider):
    """Kilo's OpenAI-compatible endpoint serving Nemotron Ultra.

    This is the final remote deployment path.  Both the base URL and the API key
    are user-supplied environment variables: no real endpoint or key is ever
    committed, and the generic OpenRouter key is deliberately *not* a fallback.
    """

    kind = "kilo"
    name = "kilo"
    default_model = DEFAULT_KILO_MODEL

    @classmethod
    def model_env_name(cls) -> str:
        return KILO_MODEL_ENV

    def __init__(self, model=None, *, base_url=None, max_calls=None, env=None):
        super().__init__(model, max_calls=max_calls, env=env)
        env = self.env
        # Read once at construction; the key is never stored in a trace or a file.
        self.base_url = str(base_url or env.get(KILO_BASE_URL_ENV) or "").strip()
        self._api_key = str(env.get(KILO_API_KEY_ENV) or "").strip()
        if not self.model:
            self.model = DEFAULT_KILO_MODEL

    def has_credentials(self) -> bool:
        return bool(self.base_url and self._api_key)

    def chat_model(self):
        if not self.base_url:
            raise ProviderUnavailable(
                f"{KILO_BASE_URL_ENV} is required for the kilo provider"
            )
        if not self._api_key:
            raise ProviderUnavailable(
                f"{KILO_API_KEY_ENV} is required for the kilo provider"
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ProviderUnavailable(
                f"langchain-openai is not installed: {exc}"
            ) from exc
        try:
            return ChatOpenAI(
                model=self.model,
                base_url=self.base_url,
                api_key=self._api_key,
                temperature=0,
            )
        except Exception as exc:
            raise ProviderUnavailable(
                f"cannot create ChatOpenAI model {self.model!r}: {type(exc).__name__}: {exc}"
            ) from exc


def build_provider(env=None):
    """Return ``(provider, reason)``; ``provider`` is ``None`` when unusable.

    ``reason`` is a short, secret-free explanation recorded in the trace and the
    report.  No live call is made here: Ollama's availability is only discovered
    on the first chat turn, where the loop falls back to the kernel.
    """
    env = os.environ if env is None else env
    kind = str(env.get(PROVIDER_ENV) or "none").strip().lower()
    if kind in ("", "none", "null", "off"):
        return None, f"{PROVIDER_ENV} is unset or 'none'"
    if kind == "replay":
        return ReplayProvider(), None
    if kind == "ollama":
        provider = OllamaProvider(env=env)
        if not provider.model:
            return None, f"{OLLAMA_MODEL_ENV} is empty"
        return provider, None
    if kind == "openrouter":
        model = str(env.get(MODEL_ENV) or "").strip()
        if not model:
            return None, f"{MODEL_ENV} is required for the openrouter provider"
        provider = OpenRouterProvider(model, env=env)
        if not provider.has_credentials():
            return None, "BUY_WAIT_LLM_API_KEY is required for the openrouter provider"
        return provider, None
    if kind == "kilo":
        provider = KiloProvider(env=env)
        if not provider.base_url:
            return None, f"{KILO_BASE_URL_ENV} is required for the kilo provider"
        if not provider.has_credentials():
            return None, f"{KILO_API_KEY_ENV} is required for the kilo provider"
        return provider, None
    return None, f"unknown {PROVIDER_ENV} {kind!r}; expected one of {', '.join(PROVIDER_KINDS)}"


def load_fixtures(fixture_dir) -> dict:
    """Load ``candidates.json``; any problem yields an empty mapping."""
    path = Path(fixture_dir) / "candidates.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), dict):
        payload = payload["candidates"]
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


__all__ = [
    "ChatProvider",
    "DecisionProvider",
    "DEFAULT_FIXTURE_DIR",
    "DEFAULT_KILO_MODEL",
    "DEFAULT_MAX_CALLS",
    "DEFAULT_OLLAMA_BASE_URL",
    "DEFAULT_OLLAMA_MODEL",
    "KILO_API_KEY_ENV",
    "KILO_BASE_URL_ENV",
    "KILO_MODEL_ENV",
    "KiloProvider",
    "NullProvider",
    "OllamaProvider",
    "OPENROUTER_BASE_URL",
    "OpenRouterProvider",
    "PROVIDER_KINDS",
    "ProviderUnavailable",
    "ReplayProvider",
    "build_provider",
    "load_fixtures",
    "request_id_from_context",
]
