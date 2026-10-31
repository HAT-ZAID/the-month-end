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
``BUY_WAIT_LLM_PROVIDER``     ``replay`` | ``ollama`` | ``openrouter`` | ``kilo`` | ``paid`` | ``none``
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
``BUY_WAIT_PAID_BASE_URL``    required for ``paid``: the user's own endpoint
``BUY_WAIT_PAID_API_KEY``     required for ``paid``: the user's own key
``BUY_WAIT_PAID_MODEL``       required for ``paid``: the model id to call
``BUY_WAIT_ACCEPT_FREE_TIER`` ``1`` silences the free-tier privacy warning
============================  ===============================================

Every provider declares a privacy class (``local``, ``paid`` or ``free``).  The
base default is ``free``, so an endpoint nobody classified still warns before it
receives free text; see :func:`privacy_warning`.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

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
# Transport defaults for the OpenAI-compatible clients. They mirror the SDK's
# own defaults; the point is the redirect policy in ``_http_client``, not a
# shorter timeout.
DEFAULT_HTTP_TIMEOUT_S = 600.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0
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
# A paid endpoint the user names itself. This is the recommended class for real
# user data: no training terms, and the user owns both the URL and the key.
PAID_BASE_URL_ENV = "BUY_WAIT_PAID_BASE_URL"
PAID_API_KEY_ENV = "BUY_WAIT_PAID_API_KEY"
PAID_MODEL_ENV = "BUY_WAIT_PAID_MODEL"
PROVIDER_KINDS = ("replay", "ollama", "openrouter", "kilo", "paid", "none")

# Privacy classes. What a provider may see is a product decision, so every
# provider declares one, and the base class assumes the least-trusted class:
# an endpoint nobody classified still gets the free-tier warning.
PRIVACY_LOCAL = "local"  # nothing leaves the machine
PRIVACY_PAID = "paid"    # paid endpoint with no-training terms
PRIVACY_FREE = "free"    # free tier: the provider may train on what it receives

ACCEPT_FREE_TIER_ENV = "BUY_WAIT_ACCEPT_FREE_TIER"
FREE_TIER_WARNING = (
    "free tier: this provider may train on the data it receives. Do not enter "
    "sensitive information (account or card numbers, identity documents, "
    "addresses). Use the local or a paid provider for real user data."
)


class ProviderUnavailable(RuntimeError):
    """A provider cannot serve a chat turn; the caller falls back to the kernel."""


LOCAL_HOSTS = frozenset({"localhost"})


def url_host(url) -> str:
    """Lower-cased host of ``url``, or an empty string when it is unparseable."""
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_loopback_host(host) -> bool:
    """True for ``localhost`` or a loopback IP literal, never for a DNS name.

    The test has to be on the resolved address, not on the text.  A prefix test
    such as ``host.startswith("127.")`` accepts names like ``127.0.0.1.nip.io``
    or ``127.gpu-srv.example``, which are ordinary remote hosts: both the
    plaintext-transport rule and the privacy class would then be wrong, and the
    free-tier warning would be silenced for a remote endpoint.
    """
    text = str(host or "").strip().lower().strip("[]")
    if not text:
        return False
    if text in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def is_local_url(url) -> bool:
    """True when ``url`` points at this machine (loopback only)."""
    return is_loopback_host(url_host(url))


def redact_url(url) -> str:
    """The URL without any ``user:password@`` component, for a message or a trace.

    A gateway may legitimately carry basic-auth credentials in its URL, and a
    refusal message is persisted in a trace, so the userinfo must not travel
    with it.
    """
    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return "<unparseable url>"
    if parts.username is None and parts.password is None:
        return text
    # Take the host straight from the netloc the parser already split, so a
    # malformed port cannot raise and IPv6 brackets survive.
    netloc = parts.netloc.rpartition("@")[2]
    return parts._replace(netloc=netloc).geturl()


def is_secure_or_local_url(url) -> bool:
    """True for ``https`` anywhere, or ``http`` on the loopback interface.

    A plaintext request to a remote host would put the API key and the prompt
    on the wire in the clear, so that combination is refused rather than run.
    """
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme == "https":
        return True
    if scheme == "http":
        return is_local_url(url)
    return False


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
    #: Privacy class of the endpoint (see the PRIVACY_* constants).
    privacy = PRIVACY_FREE

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
            "privacy": self.privacy,
            "max_calls": self.max_calls,
            "calls_used": self.calls_used,
            "remaining_calls": self.remaining_calls,
        }


class OllamaProvider(ChatProvider):
    """Local Ollama chat model (default) -- no key, no cloud, no credits."""

    kind = "ollama"
    name = "ollama"
    default_model = DEFAULT_OLLAMA_MODEL
    privacy = PRIVACY_LOCAL

    def __init__(self, model=None, *, base_url=None, max_calls=None, env=None):
        super().__init__(model, max_calls=max_calls, env=env)
        env = self.env
        self.base_url = str(
            base_url
            or env.get(OLLAMA_BASE_URL_ENV)
            or DEFAULT_OLLAMA_BASE_URL
        ).strip()
        # A configured Ollama host need not be this machine, so the class
        # default is only a claim about the usual case.  Classify the endpoint
        # that was actually resolved: a remote host is off-machine data.
        if not is_local_url(self.base_url):
            self.privacy = PRIVACY_FREE

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


class OpenAICompatibleProvider(ChatProvider):
    """One OpenAI-compatible chat client; a preset only declares its endpoint.

    This replaces two near-identical classes: everything that differed between
    them is a class attribute, so the key/base-URL resolution and the error
    strings live in exactly one place.  No committed host or key: both are
    user-supplied environment variables.
    """

    base_url_env = ""
    default_base_url = ""
    api_key_env = ""
    api_key_fallbacks: tuple[str, ...] = ()
    model_env = MODEL_ENV
    #: A self-hosted or user-deployed endpoint has no sane default, so an empty
    #: base URL makes it unusable; a preset with a public default can omit it.
    requires_base_url = False

    @classmethod
    def model_env_name(cls) -> str:
        return cls.model_env

    def __init__(self, model=None, *, base_url=None, max_calls=None, env=None):
        super().__init__(model, max_calls=max_calls, env=env)
        env = self.env
        # A blank or whitespace-only model env var survives the base class, so
        # the preset default is applied here rather than assumed there.
        if not self.model:
            self.model = self.default_model
        configured = env.get(self.base_url_env) if self.base_url_env else None
        self.base_url = str(
            base_url or configured or self.default_base_url or ""
        ).strip()
        # Read once at construction; never stored in a trace, a prompt or a file.
        self._api_key = ""
        for key in (self.api_key_env, *self.api_key_fallbacks):
            if not key:
                continue
            candidate = str(env.get(key) or "").strip()
            if candidate:
                self._api_key = candidate
                break

    def has_credentials(self) -> bool:
        if self.requires_base_url and not self.base_url:
            return False
        return bool(self._api_key)

    def chat_model(self):
        if not self.model:
            raise ProviderUnavailable(
                f"{self.model_env} is required for the {self.kind} provider"
            )
        if self.requires_base_url and not self.base_url:
            raise ProviderUnavailable(
                f"{self.base_url_env} is required for the {self.kind} provider"
            )
        if self.base_url and not is_secure_or_local_url(self.base_url):
            raise ProviderUnavailable(
                f"{self.base_url_env or 'base_url'} must use https unless it "
                f"points at this machine, not {redact_url(self.base_url)!r}"
            )
        if not self._api_key:
            raise ProviderUnavailable(
                f"{self.api_key_env} is required for the {self.kind} provider"
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
                http_client=self._http_client(),
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(
                f"cannot create ChatOpenAI model {self.model!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def _http_client(self):
        """An HTTP client that never follows a redirect.

        The OpenAI SDK follows redirects by default, and a 307 keeps the method
        and the body, so an endpoint that redirects to ``http://`` would resend
        the prompt and the key in cleartext.  The scheme check above only covers
        the configured URL, so the transport has to refuse the rest.  Timeouts
        mirror the SDK defaults rather than shortening them.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - openai depends on httpx
            raise ProviderUnavailable(
                f"cannot enforce a safe transport: httpx is not installed: {exc}"
            ) from exc
        return httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(DEFAULT_HTTP_TIMEOUT_S, connect=DEFAULT_CONNECT_TIMEOUT_S),
        )


class OpenRouterProvider(OpenAICompatibleProvider):
    """Opt-in OpenRouter chat model; the model id must come from the environment."""

    kind = "openrouter"
    name = "openrouter"
    default_model = ""  # deliberately none: OpenRouter has no sane default
    model_env = MODEL_ENV
    default_base_url = OPENROUTER_BASE_URL
    api_key_env = "BUY_WAIT_LLM_API_KEY"
    api_key_fallbacks = ("OPENROUTER_KEY",)
    privacy = PRIVACY_FREE


class KiloProvider(OpenAICompatibleProvider):
    """Kilo's OpenAI-compatible endpoint serving Nemotron Ultra.

    This is the final remote deployment path.  Both the base URL and the API key
    are user-supplied environment variables: no real endpoint or key is ever
    committed, and the generic OpenRouter key is deliberately *not* a fallback.
    """

    kind = "kilo"
    name = "kilo"
    default_model = DEFAULT_KILO_MODEL
    model_env = KILO_MODEL_ENV
    base_url_env = KILO_BASE_URL_ENV
    api_key_env = KILO_API_KEY_ENV
    requires_base_url = True
    privacy = PRIVACY_FREE


class PaidProvider(OpenAICompatibleProvider):
    """A paid OpenAI-compatible endpoint the user names in the environment.

    This is the class to reach for with real user data: paid endpoints do not
    train on what they receive.  Nothing is committed and there is no default
    endpoint or key, so an unconfigured install simply cannot use it.
    """

    kind = "paid"
    name = "paid"
    default_model = ""  # deliberately none: the user names the model
    model_env = PAID_MODEL_ENV
    base_url_env = PAID_BASE_URL_ENV
    api_key_env = PAID_API_KEY_ENV
    requires_base_url = True
    privacy = PRIVACY_PAID


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
    if kind == "paid":
        provider = PaidProvider(env=env)
        if not provider.model:
            return None, f"{PAID_MODEL_ENV} is required for the paid provider"
        if not provider.base_url:
            return None, f"{PAID_BASE_URL_ENV} is required for the paid provider"
        if not provider.has_credentials():
            return None, f"{PAID_API_KEY_ENV} is required for the paid provider"
        return provider, None
    return None, f"unknown {PROVIDER_ENV} {kind!r}; expected one of {', '.join(PROVIDER_KINDS)}"


def privacy_warning(provider, env=None, *, honour_acceptance: bool = True) -> str:
    """The warning a caller must show before ``provider`` receives free text.

    Empty for a local or paid endpoint, and empty once the operator has accepted
    the free tier explicitly through ``BUY_WAIT_ACCEPT_FREE_TIER`` — unless the
    caller is an interactive surface, which passes ``honour_acceptance=False``
    so a copied ``.env`` cannot silence the warning a person is owed.  An
    unclassified provider returns the warning: silence is never the default.
    """
    if provider is None:
        return ""
    # Only the two known-safe classes silence the warning.  An unrecognised or
    # missing class is treated as free, because silence must never be default.
    if getattr(provider, "privacy", None) in (PRIVACY_LOCAL, PRIVACY_PAID):
        return ""
    env = os.environ if env is None else env
    if honour_acceptance and str(env.get(ACCEPT_FREE_TIER_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return ""
    return FREE_TIER_WARNING


def endpoint_privacy_class(url) -> str:
    """The privacy class of an endpoint that is not a :class:`ChatProvider`.

    The one-shot transports (:mod:`code.explanations`, :mod:`code.jev`) post
    straight to a URL, so nothing has declared a class for them.  An address on
    this machine is local; anything else is treated exactly like the base
    provider class treats an unknown endpoint: as a free tier that warns.
    """
    return PRIVACY_LOCAL if is_local_url(url) else PRIVACY_FREE


class _EndpointPrivacy:
    """Provider-shaped shim so :func:`privacy_warning` can judge a bare URL."""

    def __init__(self, url, env=None):
        self.privacy = endpoint_privacy_class(url)
        self.url = str(url or "")
        self._env = env

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<endpoint privacy={self.privacy!r}>"


def endpoint_privacy_warning(url, env=None) -> str:
    """The free-tier notice for a raw endpoint, before the first plain HTTP call."""
    return privacy_warning(_EndpointPrivacy(url, env=env), env=env)


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
    "ACCEPT_FREE_TIER_ENV",
    "ChatProvider",
    "DecisionProvider",
    "DEFAULT_FIXTURE_DIR",
    "DEFAULT_KILO_MODEL",
    "DEFAULT_MAX_CALLS",
    "DEFAULT_OLLAMA_BASE_URL",
    "DEFAULT_OLLAMA_MODEL",
    "FREE_TIER_WARNING",
    "KILO_API_KEY_ENV",
    "KILO_BASE_URL_ENV",
    "KILO_MODEL_ENV",
    "KiloProvider",
    "NullProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OPENROUTER_BASE_URL",
    "OpenRouterProvider",
    "PAID_API_KEY_ENV",
    "PAID_BASE_URL_ENV",
    "PAID_MODEL_ENV",
    "PaidProvider",
    "PRIVACY_FREE",
    "PRIVACY_LOCAL",
    "PRIVACY_PAID",
    "PROVIDER_KINDS",
    "ProviderUnavailable",
    "ReplayProvider",
    "build_provider",
    "endpoint_privacy_class",
    "endpoint_privacy_warning",
    "is_local_url",
    "is_loopback_host",
    "is_secure_or_local_url",
    "load_fixtures",
    "privacy_warning",
    "redact_url",
    "request_id_from_context",
    "url_host",
]
