---
title: Model-list probes still honour HTTP(S)_PROXY
labels: security,p2
---

## Context

Provider HTTP clients now use `trust_env=False` (commit `164ad64`), so prompts
and API keys cannot be sent to a proxy.

`code/models.py` and `_ollama_tags()` still use `urllib`, which honours proxy
environment variables. No prompt, key or user text travels there, but the
behaviour is inconsistent.

## Acceptance

Model discovery uses the same no-proxy policy, or documents why not.
