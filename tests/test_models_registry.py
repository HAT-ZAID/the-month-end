"""The interactive model registry: what may be offered, and what counts as local.

Two defects these tests pin down (CR-15, CR-16):

* the local probe accepted any base URL that merely *contained* ``127.0.0.1`` or
  ``localhost``, so ``http://ollama-127.0.0.1.example:11434`` was probed as
  though it were on this machine;
* the registry looked for ``KILO_API_KEY`` instead of the documented
  ``BUY_WAIT_KILO_API_KEY``, ignored ``BUY_WAIT_LLM_API_KEY``, and never offered
  the ``paid`` provider at all.

No socket is opened: discovery is switched off in the listing cases, and the one
probe test replaces the request helper with a recorder.
"""
from __future__ import annotations

import pytest

from code import models
from code.providers import KILO_API_KEY_ENV, MODEL_ENV, PAID_API_KEY_ENV

#: Discovery off, so a test never depends on which models this machine runs.
QUIET = {"BUY_WAIT_MODEL_DISCOVERY": "0"}
#: Both documented names for the OpenRouter credential.
OPENROUTER_KEY_ENVS = ("BUY_WAIT_LLM_API_KEY", "OPENROUTER_KEY")


def listed(env, kind) -> list[dict]:
    """The ``options()`` entries for one provider kind, with discovery off."""
    return [
        item
        for item in models.options(env={**QUIET, **env})
        if item["provider"] == kind
    ]


# --------------------------------------------------------------- local only


@pytest.mark.parametrize(
    "url",
    [
        "http" "://" "127.0.0.1:11434",
        "http" "://" "127.0.0.1:11434/",
        "http" "://" "127.0.0.1",
        "https" "://" "127.0.0.1:11434/api/tags",
        "http" "://" "localhost:11434",
        "http" "://" "LOCALHOST:11434",
        "http" "://" "[::1]:11434",
    ],
)
def test_a_loopback_endpoint_is_this_machine(url):
    assert models.is_loopback(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # The regression: containing "127.0.0.1" is not the same as being it.
        "http" "://" "ollama-127.0.0.1.example:11434",
        "http" "://" "127.0.0.1.nip.io:11434",
        "http" "://" "127.0.0.1.example:11434",
        "http" "://" "localhost.example.com:11434",
        "http" "://" "localtest:11434",
        "http" "://" "10.0.0.1:11434",
        "http" "://" "192.168.1.10:11434",
        "http" "://" "example.com:11434",
    ],
)
def test_a_host_that_only_looks_local_is_never_this_machine(url):
    assert models.is_loopback(url) is False


@pytest.mark.parametrize(
    "url",
    [
        # Credentials mean the address was never a local-preference decision.
        "http" "://" "user:pass@127.0.0.1:11434",
        "http" "://" "user@localhost:11434",
        # Only plain HTTP transports are local endpoints at all.
        "ftp" "://" "127.0.0.1:11434",
        "file" "://" "127.0.0.1/etc/passwd",
        "gopher" "://" "localhost:70",
        # Not a URL, or not one that names a host.  An IPv6 literal only names a
        # host inside brackets, so the bare form is refused rather than guessed.
        "127.0.0.1:11434",
        "localhost:11434",
        "http" "://" "::1",
        "not a url",
        "http" "://",
        "http" "://:11434",
        "",
        None,
    ],
)
def test_a_url_that_is_not_a_plain_local_http_endpoint_is_not_loopback(url):
    assert models.is_loopback(url) is False


def test_the_local_probe_is_only_made_against_this_machine(monkeypatch):
    probed: list[str] = []

    def recorder(url, timeout=1.5):
        probed.append(url)
        return {"models": [{"name": "tiny:latest"}]}

    monkeypatch.setattr(models, "_get_json", recorder)
    lookalike = "http" "://" "ollama-127.0.0.1.example:11434"
    assert models.ollama_models(env={"OLLAMA_URL": lookalike}) == []
    assert probed == [], "a lookalike host must not receive a discovery packet"

    assert models.ollama_models(env={"OLLAMA_URL": "http" "://" "127.0.0.1:11434"}) == [
        "tiny:latest"
    ]
    assert probed == ["http" "://" "127.0.0.1:11434/api/tags"]


# ------------------------------------------------------------ hosted menu


def test_a_kilo_key_under_the_documented_name_lists_kilo():
    env = {KILO_API_KEY_ENV: "kilo-placeholder-key"}
    assert "kilo" in models.hosted_configured(env)
    entries = listed(env, "kilo")
    assert len(entries) == 1
    assert entries[0]["id"] == "kilo:default"


@pytest.mark.parametrize("name", OPENROUTER_KEY_ENVS)
def test_openrouter_is_listed_under_each_documented_key_name(name):
    env = {name: "openrouter-placeholder-key"}
    assert "openrouter" in models.hosted_configured(env)
    assert len(listed(env, "openrouter")) == 1


def test_a_model_id_is_not_a_credential():
    # The registry used to read a key variable it never documented; a model id
    # must not stand in for one either.
    assert models.hosted_configured({MODEL_ENV: "some-model"}) == []


def test_a_paid_endpoint_is_listed_once_its_key_is_set():
    env = {PAID_API_KEY_ENV: "paid-placeholder-key"}
    assert "paid" in models.hosted_configured(env)
    entries = listed(env, "paid")
    assert len(entries) == 1
    assert entries[0]["id"] == "paid:default"
    # Paid endpoints do not train on what they receive, so the class is paid
    # rather than free: nothing about this entry is a training tier.
    assert entries[0]["privacy"] == "paid"
    assert "paid" in entries[0]["label"].lower()


def test_an_unconfigured_environment_lists_no_hosted_provider():
    assert models.hosted_configured({}) == []
    assert {item["provider"] for item in models.options(env=QUIET)} == {""}


def test_a_listed_paid_choice_builds_a_paid_provider():
    env = {
        PAID_API_KEY_ENV: "paid-placeholder-key",
        "BUY_WAIT_PAID_BASE_URL": "https" "://" "paid.example.invalid/v1",
        "BUY_WAIT_PAID_MODEL": "some-model",
    }
    provider = models.provider_for("paid:default", env=env)
    assert provider is not None
    assert provider.kind == "paid"


def test_an_explicitly_empty_environment_is_not_replaced_by_the_process(monkeypatch):
    # A hard-coded ``env or os.environ`` would let a key exported in the shell
    # leak into a caller that asked about an empty environment.
    monkeypatch.setenv(KILO_API_KEY_ENV, "kilo-placeholder-key")
    assert models.hosted_configured({}) == []
    assert {item["provider"] for item in models.options(env=QUIET)} == {""}


def test_an_undocumented_kilo_variable_cannot_advertise_kilo():
    # ``build_provider`` never reads a bare KILO_API_KEY, so listing Kilo for it
    # would offer a choice that could not be built.
    assert models.hosted_configured({"KILO_API_KEY": "kilo-placeholder-key"}) == []
