---
title: Future works: durable memory, orchestrator state machine, LangGraph
labels: future-works,p3
---

## Context

Deferred from the remediation cut, as agreed:

- Pydantic contracts package (`code/contracts/`).
- A typed orchestrator state machine with plan/execute/observe/replan.
- Durable, account-scoped memory (SQLite, TTL, user confirmation).
- Pagination and field minimisation for tool outputs (today: bounded truncation).
- Module restructure into `contracts/`, `orchestration/`, `memory/`,
  `evidence/`, `scenarios/`.
- A LangGraph prototype, only after the plain-Python state machine is measured.
- Full adversarial benchmark and telemetry (p50/p95 latency, egress accounting).

## Acceptance

Each item becomes its own issue with a design when it is picked up.
