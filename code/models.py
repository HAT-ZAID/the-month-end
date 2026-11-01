"""Which models this machine can actually run, and how to pick one.

The interface has to offer a choice, so the choice has to be discoverable.  Only
**local** endpoints are probed over the network: Ollama on loopback.  A hosted
provider is listed only when its credentials are already in the environment,
because this module must never make a paid call just to draw a dropdown.

Privacy classes follow :mod:`code.providers`: ``local`` may see anything,
``paid`` may see user text but never secrets, ``free`` trains on what it is sent
and is labelled as such wherever it appears.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

#: Loopback only. A model list is not worth a packet leaving the machine.
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"

NO_MODEL = "none"


def _get_json(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def ollama_models(env=None) -> list[str]:
    """Model names served by a local Ollama, newest first, or an empty list."""
    env = env or os.environ
    if str(env.get("BUY_WAIT_MODEL_DISCOVERY") or "1").strip() in ("0", "false", "no"):
        return []
    base = str(env.get("OLLAMA_URL") or "http://127.0.0.1:11434")
    base = base.rstrip("/")
    if "127.0.0.1" not in base and "localhost" not in base:
        return []  # never probe a remote host for a menu
    payload = _get_json(f"{base}/api/tags")
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
    env = env or os.environ
    found = []
    if str(env.get("KILO_API_KEY") or "").strip():
        found.append("kilo")
    if str(env.get("OPENROUTER_KEY") or env.get("OPENROUTER_API_KEY") or "").strip():
        found.append("openrouter")
    return found


def options(env=None) -> list[dict]:
    """Every selectable model, as ``{"id", "label", "provider", "model", "privacy"}``.

    The first entry is always the honest "no model" choice, so a caller can fall
    back to a screen that states it has no model instead of guessing.
    """
    env = env or os.environ
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
    if "openrouter" in hosted_configured(env):
        items.append({
            "id": "openrouter:default",
            "label": "OpenRouter · free tier trains on your text",
            "provider": "openrouter",
            "model": "",
            "privacy": "free",
        })
    if "kilo" in hosted_configured(env):
        items.append({
            "id": "kilo:default",
            "label": "Kilo · paid endpoint",
            "provider": "kilo",
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

    env = dict(env or os.environ)
    choice = str(choice or NO_MODEL)
    if choice == NO_MODEL or ":" not in choice:
        return None
    provider, model = choice.split(":", 1)
    env["BUY_WAIT_LLM_PROVIDER"] = provider
    if model and model != "default":
        env["BUY_WAIT_LLM_MODEL"] = model
        # Ollama reads its own model variable; set both so the choice is not
        # silently replaced by the provider default.
        env["BUY_WAIT_OLLAMA_MODEL"] = model
    built = build_provider(env)
    built = built[0] if isinstance(built, tuple) else built
    if built is not None and model and model != "default":
        if hasattr(built, "model"):
            built.model = model
    return built
