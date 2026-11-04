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

import os
import urllib.request

import httpx
import pytest

from code import models, providers
from code.providers import (
    KILO_API_KEY_ENV,
    KILO_BASE_URL_ENV,
    OLLAMA_BASE_URL_ENV,
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


# ---------------------------------------------------------------------------
# Model discovery: the menu is loopback-only, so it must not leave via a proxy
#
# ``urllib`` reads ``HTTP_PROXY``/``HTTPS_PROXY`` by default.  A probe to
# Ollama is supposed to stay on this machine, and every provider client is now
# built with ``trust_env=False``, so discovery has to use an opener whose proxy
# table is empty rather than the module-level ``urlopen``.
# ---------------------------------------------------------------------------


class _JsonResponse:
    """The slice of ``http.client.HTTPResponse`` the probes read."""

    status = 200

    def read(self) -> bytes:
        return b'{"models": [{"name": "tiny:latest"}]}'

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RecordingOpener:
    """Stands in for the opener so no socket is ever opened."""

    def __init__(self):
        self.opened: list[str] = []

    def open(self, url, timeout=None):
        self.opened.append(url)
        return _JsonResponse()


def _proxy_handlers(opener) -> list:
    return [
        handler
        for handler in getattr(opener, "handlers", ())
        if isinstance(handler, urllib.request.ProxyHandler)
    ]


def test_the_discovery_openers_ignore_the_proxy_environment():
    # The autouse fixture proves the environment really is proxied here.
    assert os.environ["HTTP_PROXY"] == PROXY_URL
    assert os.environ["HTTPS_PROXY"] == PROXY_URL
    assert urllib.request.getproxies(), "the environment must be proxied for this test"

    # The empty table is the policy: it installs no scheme methods, so
    # ``build_opener`` drops the default, environment-reading ProxyHandler.
    assert isinstance(providers._PROBE_PROXY, urllib.request.ProxyHandler)
    assert providers._PROBE_PROXY.proxies == {}

    # With a default opener the populated ProxyHandler would be registered for
    # http/https once these variables are set, so an empty list is the proof
    # that neither probe has any proxy route at all.
    for name, opener in (
        ("models", models._PROBE_OPENER),
        ("providers", providers._PROBE_OPENER),
    ):
        assert _proxy_handlers(opener) == [], f"{name}: discovery must bypass proxies"


def test_both_discovery_probes_use_that_opener(monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(models, "_PROBE_OPENER", opener)
    monkeypatch.setattr(providers, "_PROBE_OPENER", opener)

    local = "http" "://" "127.0.0.1:11434"
    assert models.ollama_models(env={OLLAMA_BASE_URL_ENV: local}) == ["tiny:latest"]
    assert providers._ollama_tags(local, 0.5) == ["tiny:latest"]

    assert opener.opened == [f"{local}/api/tags", f"{local}/api/tags"]

