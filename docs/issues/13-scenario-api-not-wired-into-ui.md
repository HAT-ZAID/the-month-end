---
title: The scenario API is not wired into chat or the app
labels: feature,p2
---

## Context

`code/scenarios.py` provides `simulate_scenario` and `compare_scenarios`
(deep-copy, kernel reuse, never persists). `tools/app.py` and `code/chat.py` still
use the older `_decide_hypothetically`, so users do not yet get guarded
scenarios through the product surface.

## Acceptance

The app and chat call the scenario API; unsafe hypotheses are refused with the
guards; a UI test covers a delay scenario.
