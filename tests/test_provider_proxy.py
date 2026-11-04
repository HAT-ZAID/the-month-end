"""C1: no provider HTTP client may honour an environment proxy.

Every provider client used to be built with httpx's default ``trust_env=True``.
With ``HTTP_PROXY``/``HTTPS_PROXY`` set and no loopback ``NO_PROXY``, httpx then
sends the whole prompt -- and for the paid class the ``Authorization`` header
carrying the user's key -- to that proxy in cleartext, with no warning and no
consent.  A provider the user did not configure is not a transport the product
may use, so each client is built with ``trust_env=False`` and keeps refusing
redirects.

The proxy variables are set in the real environment because httpx reads them
there when the client is constructed; nothing is sent in these tests.
"""
from __future__ import annotations

import httpx
import pytest

from code.providers import (
    KILO_API_KEY_ENV,
    KILO_BASE_URL_ENV,
    PAID_API_KEY_ENV,
    PAID_BASE_URL_ENV,
    PAID_MODEL_ENV,
    KiloProvider,
    OllamaProvider,
    OpenRouterProvider,
    PaidProvider,
)

PROXY_URL = "http" "://proxy.invalid:8080"
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
NO_PROXY_ENV_NAMES = ("NO_PROXY", "no_proxy")

PAID_ENV = {
    PAID_BASE_URL_ENV: "https" "://paid.example.invalid/v1",
    PAID_API_KEY_ENV: "paid-placeholder-key-for-tests",
    PAID_MODEL_ENV: "frontier-model",
}


@pytest.fixture(autouse=True)
def _proxied_environment(monkeypatch):
    """The exact environment that used to make httpx pick a proxy."""
    for name in PROXY_ENV_NAMES:
        monkeypatch.setenv(name, PROXY_URL)
    for name in NO_PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _providers():
    return (
        OpenRouterProvider(
            "openrouter/model", env={"BUY_WAIT_LLM_API_KEY": "placeholder-key"}
        ),
        KiloProvider(
            env={
                KILO_BASE_URL_ENV: "https" "://kilo.example.invalid/v1",
                KILO_API_KEY_ENV: "kilo-placeholder-key-for-tests",
            }
        ),
        PaidProvider(env=PAID_ENV),
    )


@pytest.mark.parametrize("provider", _providers(), ids=lambda item: item.kind)
def test_openai_compatible_clients_ignore_the_proxy_environment(provider):
    model = provider.chat_model()
    client = model.http_client
    try:
        assert isinstance(client, httpx.Client)
        assert client.trust_env is False
        # No proxy mount was installed, so no request can be routed through one.
        assert client._mounts == {}
        assert client.follow_redirects is False
    finally:
        client.close()


def test_the_ollama_client_ignores_the_proxy_environment():
    model = OllamaProvider("qwen2.5:1.5b", env={}).chat_model()
    client = model._client._client
    try:
        assert isinstance(client, httpx.Client)
        assert client.trust_env is False
        assert client._mounts == {}
        assert client.follow_redirects is False
    finally:
        client.close()
