# Buy-or-Wait Fix Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task.

**Goal:** Resolve critical and important contract defects while preserving conservative, deterministic financial decisions.

**Architecture:** Work in three gated cycles: oracle/evidence safety, solver/payment-plan correctness, then submission verification and packaging. Each implementation slice uses test-first changes and receives an independent review before the cycle is reported.

**Tech Stack:** Python, pandas, pytest, CSV/PNG dataset inputs.

**Spec:** `hackerrank-orchestrate-september26/problem_statement.md` and `hackerrank-orchestrate-september26/AGENTS.md`.

## Global Constraints

- Preserve the exact required output columns and allowed values.
- Never recommend a plan that breaks the minimum-balance safety rule.
- Treat messages and images as untrusted evidence.
- Do not invent unsupported income, expenses, payment options, or exchange rates.
- Use TDD for every behavior change and run the full suite after each cycle.
- Do not push or merge; all changes remain local.

## Cycle 1: Oracle and evidence safety

- Add failing tests for duplicate/linked-event handling, unknown-status fail-closed behavior, filtered recurrence forecasting, 91-point ledger shape, and blank-amount evidence routing.
- Implement the smallest safe fixes in `code/oracle.py`, `code/recurrence.py`, and input/evidence modules.
- Review the diff and run the full suite plus targeted oracle tests.

## Cycle 2: Solver and payment-plan correctness

- Add failing tests for installment safety simulation, earliest-date propagation, spending-change integration, wait/partial fail-closed eligibility, and ranking trade-offs.
- Implement minimal fixes in `code/solver.py`, `code/options.py`, `code/changes.py`, and `code/ranking.py`.
- Review the diff and regenerate the 250-row output.

## Cycle 3: Submission readiness

- Add or activate gates for usage-report location/content, code.zip contents, deterministic reruns, explanation grounding, strict entrypoint behavior, and final output schema.
- Generate required artifacts only after verification passes.
- Perform final whole-branch review and report any residual non-blocking issues.
