"""Focused tests for the Kilo / Nemotron chat provider (Task 7).

No network call is made: ``langchain_openai.ChatOpenAI`` is replaced by a
recording fake, so these tests assert *construction arguments* only.  The Kilo
provider is the final remote deployment path; the default provider is still
``none``, which keeps the offline pipeline keyless.
"""
from __future__ import annotations

import pytest

from code.providers import (
    DEFAULT_KILO_MODEL,
    DEFAULT_MAX_CALLS,
    KILO_API_KEY_ENV,
    KILO_BASE_URL_ENV,
    KILO_MODEL_ENV,
    PROVIDER_ENV,
    PROVIDER_KINDS,
    KiloProvider,
    ProviderUnavailable,
    build_provider,
)

KILO_URL = "https://kilo.example.invalid/v1"
KILO_KEY = "kilo-placeholder-key-for-tests"


class RecordingChatOpenAI:
    """Captures the constructor arguments instead of contacting a server."""

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)

    def bind_tools(self, tools, **kwargs):  # pragma: no cover - shape only
        return self


@pytest.fixture
def created_models(monkeypatch):
    import langchain_openai

    created: list[RecordingChatOpenAI] = []

    def factory(**kwargs):
        model = RecordingChatOpenAI(**kwargs)
        created.append(model)
        return model

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", factory)
    return created


def _env(**overrides) -> dict:
    env = {KILO_BASE_URL_ENV: KILO_URL, KILO_API_KEY_ENV: KILO_KEY}
    env.update(overrides)
    return {key: value for key, value in env.items() if value is not None}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_kilo_builds_openai_compatible_model_with_default_model(created_models):
    provider = KiloProvider(env=_env())

    model = provider.chat_model()

    assert model is created_models[0]
    assert model.kwargs["base_url"] == KILO_URL
    assert model.kwargs["api_key"] == KILO_KEY
    assert model.kwargs["model"] == DEFAULT_KILO_MODEL == "nemotron-3-ultra"
    assert model.kwargs["temperature"] == 0
    assert provider.kind == "kilo" and provider.name == "kilo"


def test_kilo_model_env_overrides_the_default(created_models):
    provider = KiloProvider(env=_env(**{KILO_MODEL_ENV: "nemotron-3-ultra-mini"}))

    assert provider.model == "nemotron-3-ultra-mini"
    assert provider.chat_model().kwargs["model"] == "nemotron-3-ultra-mini"


def test_kilo_blank_model_env_still_uses_the_default():
    provider = KiloProvider(env=_env(**{KILO_MODEL_ENV: "   "}))
    assert provider.model == DEFAULT_KILO_MODEL


# ---------------------------------------------------------------------------
# Missing configuration: fail closed with a clear, secret-free message
# ---------------------------------------------------------------------------


def test_kilo_missing_base_url_raises_provider_unavailable():
    provider = KiloProvider(env=_env(**{KILO_BASE_URL_ENV: None}))

    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.chat_model()

    assert KILO_BASE_URL_ENV in str(excinfo.value)
    assert KILO_KEY not in str(excinfo.value)


def test_kilo_missing_api_key_raises_provider_unavailable():
    provider = KiloProvider(env=_env(**{KILO_API_KEY_ENV: None}))

    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.chat_model()

    assert KILO_API_KEY_ENV in str(excinfo.value)


def test_kilo_never_falls_back_to_the_openrouter_key(created_models):
    provider = KiloProvider(env=_env(**{KILO_API_KEY_ENV: None, "OPENROUTER_KEY": "router-key"}))
    with pytest.raises(ProviderUnavailable):
        provider.chat_model()

    generic = KiloProvider(
        env=_env(**{KILO_API_KEY_ENV: None, "BUY_WAIT_LLM_API_KEY": "generic-key"})
    )
    with pytest.raises(ProviderUnavailable):
        generic.chat_model()
    assert created_models == []


# ---------------------------------------------------------------------------
# Factory wiring and budget
# ---------------------------------------------------------------------------


def test_kilo_is_a_known_provider_kind():
    assert "kilo" in PROVIDER_KINDS
    assert "none" in PROVIDER_KINDS


def test_build_provider_selects_kilo_when_configured():
    provider, reason = build_provider({PROVIDER_ENV: "kilo", **_env()})

    assert reason is None
    assert isinstance(provider, KiloProvider)
    assert provider.model == DEFAULT_KILO_MODEL


def test_build_provider_reports_missing_kilo_configuration_without_raising():
    provider, reason = build_provider({PROVIDER_ENV: "kilo", KILO_API_KEY_ENV: KILO_KEY})
    assert provider is None and KILO_BASE_URL_ENV in reason

    provider, reason = build_provider({PROVIDER_ENV: "kilo", KILO_BASE_URL_ENV: KILO_URL})
    assert provider is None and KILO_API_KEY_ENV in reason


def test_default_provider_stays_offline_and_keyless(created_models):
    provider, reason = build_provider({})

    assert provider is None
    assert PROVIDER_ENV in reason
    # The offline path neither selects Kilo nor builds any chat model.
    assert "kilo" not in reason
    assert created_models == []


def test_kilo_keeps_the_run_wide_call_cap():
    assert KiloProvider(env=_env()).max_calls == DEFAULT_MAX_CALLS == 50

    capped = KiloProvider(env=_env(**{"BUY_WAIT_LLM_MAX_CALLS": "7"}))
    assert capped.max_calls == 7
    assert capped.reserve_call() is True
    assert capped.remaining_calls == 6


def test_kilo_describe_never_contains_the_secret():
    payload = KiloProvider(env=_env()).describe()
    assert KILO_KEY not in repr(payload)
    assert "api_key" not in payload
