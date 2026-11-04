"""Which models this machine can actually run, and how to pick one.

The interface has to offer a choice, so the choice has to be discoverable.  Only
**local** endpoints are probed over the network: Ollama on loopback, decided by
the host's address and never by whether a URL happens to contain a local-looking
string.  A hosted provider is listed only when its credentials are already in the
environment, because this module must never make a paid call just to draw a
dropdown.  The credential and endpoint names come from :mod:`code.providers`,
so the menu and the builder agree on which variables configure which provider.

Privacy classes follow :mod:`code.providers`: ``local`` may see anything,
``paid`` may see user text but never secrets, ``free`` trains on what it is sent
and is labelled as such wherever it appears.
"""
from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
from urllib.parse import urlsplit

from code.providers import (
    DEFAULT_OLLAMA_BASE_URL,
    KILO_API_KEY_ENV,
    MODEL_ENV,
    OLLAMA_BASE_URL_ENV,
    OLLAMA_MODEL_ENV,
    PAID_API_KEY_ENV,
    PROVIDER_ENV,
    OpenRouterProvider,
    no_proxy_opener,
)

#: Loopback only. A model list is not worth a packet leaving the machine.
OLLAMA_TAGS = f"{DEFAULT_OLLAMA_BASE_URL}/api/tags"

#: The name this module read before the documented variable existed.  Kept so
#: an older checkout or shell profile keeps working; the documented name wins.
LEGACY_OLLAMA_URL_ENV = "OLLAMA_URL"

#: The transports a local endpoint may use. Anything else is refused before a
#: host is even considered.
LOCAL_SCHEMES = frozenset({"http", "https"})

#: The hosted providers this menu may offer, with the credential variables that
#: make each usable, in display order.  The names come from :mod:`code.providers`
#: -- the same ones ``build_provider`` reads -- so a menu entry cannot advertise
#: a provider the builder would refuse.
HOSTED_CREDENTIAL_ENVS = (
    ("openrouter", (OpenRouterProvider.api_key_env, *OpenRouterProvider.api_key_fallbacks)),
    ("kilo", (KILO_API_KEY_ENV,)),
    ("paid", (PAID_API_KEY_ENV,)),
)


def is_loopback(url) -> bool:
    """True when ``url`` is a plain HTTP(S) endpoint served by this machine.

    The host is compared as an address, never as text.  A substring test accepts
    names such as ``127.0.0.1.nip.io`` or ``ollama-127.0.0.1.example``, which are
    ordinary remote hosts: probing one would send a packet off this machine, and
    treating it as local would silence the free-tier warning.

    Userinfo is refused as well: ``http://user:pass@127.0.0.1:11434`` carries
    credentials, so it is not the unremarkable local endpoint this decides for.
    Only ``http`` and ``https`` name a host to probe at all.
    """
    try:
        parts = urlsplit(str(url or ""))
        has_userinfo = parts.username is not None or parts.password is not None
        host = (parts.hostname or "").strip().lower()
    except ValueError:
        return False
    if has_userinfo:
        return False
    if (parts.scheme or "").lower() not in LOCAL_SCHEMES:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


NO_MODEL = "none"

#: The transport for discovery probes.  Named so a test can replace it without
#: opening a socket.  ``urllib`` reads ``HTTP_PROXY``/``HTTPS_PROXY`` by
#: default; discovery is loopback-only, and every provider client is built with
#: ``trust_env=False``, so the menu must not be the one request that leaves
#: through a proxy the user never configured for this product.
_PROBE_OPENER = no_proxy_opener()


def _get_json(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with _PROBE_OPENER.open(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _tags_url(base: str) -> str:
    """The ``/api/tags`` endpoint for a configured Ollama base.

    The documented variable names the server root, but a user may paste the API
    root (``/api``) or the tags path itself.  Joining blindly would ask for
    ``/api/api/tags`` or ``/api/tags/api/tags`` and discover nothing.
    """
    base = str(base or "").strip().rstrip("/")
    if base.endswith("/api/tags"):
        return base
    if base.endswith("/api"):
        return f"{base}/tags"
    return f"{base}/api/tags"


def _configured_ollama_base(env) -> str:
    """The documented base URL, else the legacy name, else the provider default."""
    for name in (OLLAMA_BASE_URL_ENV, LEGACY_OLLAMA_URL_ENV):
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return DEFAULT_OLLAMA_BASE_URL


def ollama_models(env=None) -> list[str]:
    """Model names served by a local Ollama, newest first, or an empty list."""
    env = os.environ if env is None else env
    if str(env.get("BUY_WAIT_MODEL_DISCOVERY") or "1").strip() in ("0", "false", "no"):
        return []
    base = _configured_ollama_base(env)
    if not is_loopback(base):
        return []  # never probe a remote host for a menu
    payload = _get_json(_tags_url(base))
    if not payload:
        return []
    names = []
    for item in payload.get("models") or []:
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def hosted_configured(env=None) -> list[str]:
    """Hosted providers whose credentials are already present."""
    env = os.environ if env is None else env
    found = []
    for kind, names in HOSTED_CREDENTIAL_ENVS:
        if any(str(env.get(name) or "").strip() for name in names):
            found.append(kind)
    return found


def options(env=None) -> list[dict]:
    """Every selectable model, as ``{"id", "label", "provider", "model", "privacy"}``.

    The first entry is always the honest "no model" choice, so a caller can fall
    back to a screen that states it has no model instead of guessing.
    """
    env = os.environ if env is None else env
    hosted = set(hosted_configured(env))
    items = [{
        "id": NO_MODEL,
        "label": "No model — run the kernel directly",
        "provider": "",
        "model": "",
        "privacy": "",
    }]
    for name in ollama_models(env):
        items.append({
            "id": f"ollama:{name}",
            "label": f"Local · {name}",
            "provider": "ollama",
            "model": name,
            "privacy": "local",
        })
    # The free tier trains on what it is sent; say so in the label itself.
    if "openrouter" in hosted:
        items.append({
            "id": "openrouter:default",
            "label": "OpenRouter · free tier trains on your text",
            "provider": "openrouter",
            "model": "",
            "privacy": "free",
        })
    if "kilo" in hosted:
        # KiloProvider is a free tier too (PRIVACY_FREE), so the label has to
        # say what happens to the text rather than imply a no-training class.
        items.append({
            "id": "kilo:default",
            "label": "Kilo · free tier trains on your text",
            "provider": "kilo",
            "model": "",
            "privacy": "free",
        })
    # The paid class does not train on what it receives: no warning is owed, and
    # the label says which class the choice belongs to.
    if "paid" in hosted:
        items.append({
            "id": "paid:default",
            "label": "Paid endpoint · no training terms",
            "provider": "paid",
            "model": "",
            "privacy": "paid",
        })
    return items


def provider_for(choice: str, env=None):
    """Build the provider for a selected id, or ``None`` for the no-model entry.

    The environment is copied, never mutated: selecting a model in a screen must
    not change how the batch pipeline behaves in the same process.
    """
    from code.providers import build_provider

    env = dict(os.environ if env is None else env)
    choice = str(choice or NO_MODEL)
    if choice == NO_MODEL or ":" not in choice:
        return None
    provider, model = choice.split(":", 1)
    env[PROVIDER_ENV] = provider
    if provider == "ollama" and not str(env.get(OLLAMA_BASE_URL_ENV) or "").strip():
        # The menu probes the documented base, else the legacy ``OLLAMA_URL``,
        # else the provider default; the builder reads only the documented
        # variable.  Copy the resolved base across so the turn and the probe
        # target the same host instead of silently falling back to localhost.
        env[OLLAMA_BASE_URL_ENV] = _configured_ollama_base(env)
    if model and model != "default":
        env[MODEL_ENV] = model
        # Ollama reads its own model variable; set both so the choice is not
        # silently replaced by the provider default.
        env[OLLAMA_MODEL_ENV] = model
    built = build_provider(env)
    built = built[0] if isinstance(built, tuple) else built
    if built is not None and model and model != "default":
        if hasattr(built, "model"):
            built.model = model
    return built
