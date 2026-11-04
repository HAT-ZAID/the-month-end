---
title: Textual mount-race flake in the UI tests
labels: test,flaky,p2
---

## Context

`tests/test_app_fact_form.py` and `tests/review/test_general_use.py` fail
intermittently when a posted event is dispatched before a sibling widget mounts.
The same tests pass in isolation and on rerun; the app is not at fault.

## Acceptance

The affected tests wait for the rendered effect (bounded pause loop) instead of a
fixed tick, and 20 consecutive runs stay green.
