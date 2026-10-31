"""Focused tests for the Kilo / Nemotron chat provider (Task 7).

No network call is made: ``langchain_openai.ChatOpenAI`` is replaced by a
recording fake, so these tests assert *construction arguments* only.  The Kilo
provider is the final remote deployment path; the default provider is still
``none``, which keeps the offline pipeline keyless.
"""
from __future__ import annotations

import pytest

from code.providers import (
    ACCEPT_FREE_TIER_ENV,
    DEFAULT_KILO_MODEL,
    DEFAULT_MAX_CALLS,
    FREE_TIER_WARNING,
    KILO_API_KEY_ENV,
    KILO_BASE_URL_ENV,
    KILO_MODEL_ENV,
    OLLAMA_BASE_URL_ENV,
    OPENROUTER_BASE_URL,
    PAID_API_KEY_ENV,
    PAID_BASE_URL_ENV,
    PAID_MODEL_ENV,
    PRIVACY_FREE,
    PRIVACY_LOCAL,
    PRIVACY_PAID,
    PROVIDER_ENV,
    PROVIDER_KINDS,
    ChatProvider,
    KiloProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    PaidProvider,
    ProviderUnavailable,
    build_provider,
    is_local_url,
    is_loopback_host,
    is_secure_or_local_url,
    privacy_warning,
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


# ---------------------------------------------------------------------------
# Privacy classes: one declaration per provider, and a warning that fires
# ---------------------------------------------------------------------------


def test_privacy_class_is_declared_per_provider():
    assert OllamaProvider(env=_env()).privacy == PRIVACY_LOCAL
    assert OpenRouterProvider("some/model", env=_env()).privacy == PRIVACY_FREE
    assert KiloProvider(env=_env()).privacy == PRIVACY_FREE


def test_an_unclassified_provider_defaults_to_the_free_class():
    """Silence must never be the default: an unknown endpoint warns."""
    assert ChatProvider.privacy == PRIVACY_FREE
    assert privacy_warning(ChatProvider(), env={}) == FREE_TIER_WARNING


def test_describe_reports_the_privacy_class_and_no_key():
    payload = KiloProvider(env=_env()).describe()
    assert payload["privacy"] == PRIVACY_FREE
    assert KILO_KEY not in repr(payload)


def test_privacy_warning_is_empty_for_local_and_paid_providers():
    assert privacy_warning(OllamaProvider(env=_env()), env={}) == ""
    paid = ChatProvider()
    paid.privacy = PRIVACY_PAID
    assert privacy_warning(paid, env={}) == ""


def test_privacy_warning_is_silenced_only_by_an_explicit_acceptance():
    provider = KiloProvider(env=_env())
    assert privacy_warning(provider, env={}) == FREE_TIER_WARNING
    for accepted in ("1", "true", "yes", "on", " TRUE "):
        assert privacy_warning(provider, env={ACCEPT_FREE_TIER_ENV: accepted}) == ""
    assert privacy_warning(provider, env={ACCEPT_FREE_TIER_ENV: "0"}) == FREE_TIER_WARNING


def test_free_tier_warning_says_the_provider_may_train_on_the_data():
    lowered = FREE_TIER_WARNING.lower()
    assert "train" in lowered
    assert "sensitive" in lowered


def test_no_provider_is_constructible_without_declaring_privacy():
    for kind in (OllamaProvider, OpenRouterProvider, KiloProvider):
        assert issubclass(kind, ChatProvider)
        assert kind.privacy in (PRIVACY_LOCAL, PRIVACY_PAID, PRIVACY_FREE)


# ---------------------------------------------------------------------------
# The two OpenAI-compatible providers now share one implementation
# ---------------------------------------------------------------------------


def test_openai_compatible_presets_only_declare_their_endpoint():
    assert issubclass(OpenRouterProvider, OpenAICompatibleProvider)
    assert issubclass(KiloProvider, OpenAICompatibleProvider)
    # The env var names are the public contract; they must survive the merge.
    assert OpenRouterProvider.model_env_name() == "BUY_WAIT_LLM_MODEL"
    assert KiloProvider.model_env_name() == "BUY_WAIT_KILO_MODEL"
    assert OpenRouterProvider()
    assert OpenRouterProvider(env={}).base_url == OPENROUTER_BASE_URL
    assert KiloProvider(env={}).base_url == ""
    assert KiloProvider(env={}).has_credentials() is False


def test_kilo_does_not_fall_back_to_the_openrouter_key():
    provider = KiloProvider(env={KILO_BASE_URL_ENV: KILO_URL, "OPENROUTER_KEY": "router-key"})
    assert provider._api_key == ""
    assert provider.has_credentials() is False


# ---------------------------------------------------------------------------
# Transport and endpoint class both have to be honest
# ---------------------------------------------------------------------------


def test_secure_or_local_url_accepts_https_and_loopback_only():
    assert is_secure_or_local_url("https" "://kilo.example.invalid/v1") is True
    assert is_secure_or_local_url("http" "://localhost:11434") is True
    assert is_secure_or_local_url("http" "://127.0.0.1:11434") is True
    assert is_secure_or_local_url("http" "://[::1]:11434") is True
    assert is_secure_or_local_url("http" "://kilo.example.invalid/v1") is False
    assert is_secure_or_local_url("ftp" "://kilo.example.invalid/v1") is False
    assert is_secure_or_local_url("") is False
    assert is_secure_or_local_url(None) is False


def test_a_plaintext_remote_base_url_is_refused_before_a_key_is_sent():
    provider = KiloProvider(
        env={
            KILO_BASE_URL_ENV: "http" "://kilo.example.invalid/v1",
            KILO_API_KEY_ENV: KILO_KEY,
        }
    )
    with pytest.raises(ProviderUnavailable) as caught:
        provider.chat_model()
    assert "https" in str(caught.value)
    assert KILO_KEY not in str(caught.value)


def test_a_remote_ollama_host_is_not_classified_local():
    provider = OllamaProvider(
        env={OLLAMA_BASE_URL_ENV: "http" "://gpu-box.lan:11434"}
    )
    assert provider.privacy == PRIVACY_FREE
    assert privacy_warning(provider, env={}) == FREE_TIER_WARNING


def test_only_a_loopback_address_counts_as_this_machine():
    for url in (
        "http" "://127.0.0.1:11434",
        "http" "://127.0.0.5:11434",
        "http" "://[::1]:11434",
        "http" "://localhost:11434",
    ):
        assert is_local_url(url) is True, url
        assert is_secure_or_local_url(url) is True, url


def test_a_name_that_merely_starts_with_127_is_still_remote():
    # A prefix test ("127.") would accept all of these; none is this machine, so
    # a plaintext endpoint would leak the key and a remote Ollama host would
    # lose its free-tier warning.
    for url in (
        "http" "://127.0.0.1.nip.io:11434",
        "http" "://127.gpu-srv.example:11434",
        "http" "://127.evil.example/v1",
    ):
        assert is_local_url(url) is False, url
        assert is_secure_or_local_url(url) is False, url


def test_loopback_is_decided_by_address_not_by_prefix():
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("127.0.0.1.nip.io") is False
    assert is_loopback_host("127.0.0.1.invalid") is False
    assert is_loopback_host("") is False


def test_a_remote_dotted_127_host_still_gets_the_free_tier_warning():
    provider = OllamaProvider(
        env={OLLAMA_BASE_URL_ENV: "http" "://127.gpu-srv.example:11434"}
    )
    assert provider.privacy == PRIVACY_FREE
    assert privacy_warning(provider, env={}) == FREE_TIER_WARNING


def test_a_remote_plaintext_127_endpoint_cannot_smuggle_a_key():
    provider = KiloProvider(
        env={
            KILO_BASE_URL_ENV: "http" "://127.0.0.1.nip.io/v1",
            KILO_API_KEY_ENV: KILO_KEY,
        }
    )
    with pytest.raises(ProviderUnavailable) as caught:
        provider.chat_model()
    assert KILO_KEY not in str(caught.value)


def test_an_unrecognised_privacy_class_still_warns():
    provider = OllamaProvider(env=_env())
    provider.privacy = "public-ish"
    assert privacy_warning(provider, env={}) == FREE_TIER_WARNING


# ---------------------------------------------------------------------------
# The paid class: the safe path for real user data
# ---------------------------------------------------------------------------

PAID_URL = "https" "://paid.example.invalid/v1"
PAID_KEY = "paid-placeholder-key-for-tests"


def _paid_env(**overrides) -> dict:
    env = {
        PAID_BASE_URL_ENV: PAID_URL,
        PAID_API_KEY_ENV: PAID_KEY,
        PAID_MODEL_ENV: "frontier-model",
    }
    env.update(overrides)
    return {key: value for key, value in env.items() if value is not None}


def test_the_paid_provider_is_listed_and_declares_no_defaults():
    assert "paid" in PROVIDER_KINDS
    assert PaidProvider.default_model == ""
    assert PaidProvider.privacy == PRIVACY_PAID
    assert PaidProvider.base_url_env == PAID_BASE_URL_ENV
    assert PaidProvider.api_key_env == PAID_API_KEY_ENV


def test_a_paid_provider_is_built_from_the_users_own_endpoint():
    provider, reason = build_provider({PROVIDER_ENV: "paid", **_paid_env()})

    assert reason is None
    assert isinstance(provider, PaidProvider)
    assert provider.privacy == PRIVACY_PAID
    assert provider.model == "frontier-model"
    assert provider.base_url == PAID_URL


def test_a_paid_provider_never_warns_about_training():
    provider, _reason = build_provider({PROVIDER_ENV: "paid", **_paid_env()})

    assert privacy_warning(provider, env={}) == ""
    assert privacy_warning(provider, env={ACCEPT_FREE_TIER_ENV: "0"}) == ""


def test_a_paid_provider_sends_the_key_to_the_users_own_endpoint(created_models):
    provider, _reason = build_provider({PROVIDER_ENV: "paid", **_paid_env()})

    model = provider.chat_model()

    assert model.kwargs["base_url"] == PAID_URL
    assert model.kwargs["api_key"] == PAID_KEY
    assert model.kwargs["model"] == "frontier-model"
    assert model.kwargs["temperature"] == 0


@pytest.mark.parametrize(
    "missing, expected",
    [
        (PAID_MODEL_ENV, PAID_MODEL_ENV),
        (PAID_BASE_URL_ENV, PAID_BASE_URL_ENV),
        (PAID_API_KEY_ENV, PAID_API_KEY_ENV),
    ],
)
def test_an_incomplete_paid_configuration_is_refused_with_its_env_name(missing, expected):
    provider, reason = build_provider(
        {PROVIDER_ENV: "paid", **_paid_env(**{missing: None})}
    )

    assert provider is None
    assert expected in reason
    assert reason.endswith("paid provider")


def test_a_paid_provider_refuses_a_plaintext_remote_endpoint(created_models):
    provider, _reason = build_provider(
        {
            PROVIDER_ENV: "paid",
            **_paid_env(**{PAID_BASE_URL_ENV: "http" "://paid.example.invalid/v1"}),
        }
    )

    with pytest.raises(ProviderUnavailable) as caught:
        provider.chat_model()
    assert "https" in str(caught.value)
    assert PAID_KEY not in str(caught.value)
    assert created_models == []


def test_the_http_client_never_follows_a_redirect(created_models):
    """A 307 keeps the body, so a redirect could resend the key in cleartext."""
    provider, _reason = build_provider({PROVIDER_ENV: "paid", **_paid_env()})

    model = provider.chat_model()
    client = model.kwargs["http_client"]

    assert client.follow_redirects is False
    client.close()


def test_a_local_endpoint_also_gets_the_no_redirect_client(created_models):
    provider = KiloProvider(
        env={
            KILO_BASE_URL_ENV: "http" "://127.0.0.1:8080/v1",
            KILO_API_KEY_ENV: KILO_KEY,
        }
    )

    model = provider.chat_model()
    client = model.kwargs["http_client"]

    assert client.follow_redirects is False
    client.close()
