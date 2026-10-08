# SDD ledger — plan: docs/superpowers/plans/2026-09-13-buy-or-wait-compliance-fixes.md

## Preflight

- Authority: `problem_statement.md`; operational checklist: `AGENTS.md`.
- User approval: received with `go`; current model is preserved for all agents and reviewers.
- Existing worktree is dirty and user-owned; no reset, checkout, or unrelated cleanup will be performed.
- Deadline ruling: prioritize Tasks 1–3 and Task 5; Task 4 remains optional unless the critical path finishes with time available.
- Ruling: parallel implementation is allowed for Tasks 1–3 because the approved plan gives disjoint write scopes and the user explicitly requested multiple agents; the controller will reconcile shared interfaces before packaging. Cost if wrong: merge conflicts or integration regressions requiring a fix round.

### Plan conflict/interface scan

| Pair/task | Shared file or interface | Finding | Ruling |
|---|---|---|---|
| Task 1 ↔ Task 2 | `code/main.py`, final run | OCR resolution and explanation usage both flow through the final runner; neither should alter the other's decision boundary. | Keep OCR facts deterministic and explanations output-only; controller verifies the combined run. |
| Task 1 ↔ Task 3 | reconciled event dictionaries/oracle input | OCR may populate previously blank event amounts consumed by the solver/oracle. | Task 1 may only add validated evidence facts; Task 3 must preserve fail-closed behavior for unresolved amounts. |
| Task 2 ↔ Task 5 | `code/main.py`, usage reports | Provider accounting must describe the exact run that generated `output.csv` and archive reports. | Task 2 owns accounting interfaces; controller owns the final run and report/archive consistency. |
| Task 3 ↔ Task 4 | solver/ranking semantics | Task 3 changes the candidate pool; Task 4 would consolidate ranking later. | Task 3 must use the existing shared ranking contract and avoid sample-only changes; Task 4 is deferred until critical behavior is green. |
| Task 1 | `code/ocr.py`, `code/evidence.py`, OCR tests | Requirements agree: preprocess, classify by document family, validate ambiguity/currency/ownership, and fail closed. | Proceed with TDD and no event-ID hardcoding. |
| Task 2 | `code/explanations.py`, `code/usage.py`, usage tests | Requirements agree: preserve provider usage, cap calls, bound failures, and report costs without secrets. | Proceed with TDD; no live call is allowed to exceed the approved ceiling. |
| Task 3 | `code/changes.py`, `code/solver.py`, change tests | Requirements agree: validated change actions become ranked safe plans without corrupting earliest-date independence. | Proceed with TDD and preserve existing oracle safety checks. |
| Task 4 | `code/ranking.py`, `code/solver.py`, `code/options.py`, sample tests | Requirements are internally consistent but depend on Task 3's candidate pool. | Defer unless critical tasks and release verification complete. |
| Task 5 | entrypoint, artifacts, tests | Requirements agree: regenerate, validate, package, and record exact final usage. | Controller-only after implementation/reviews. |

## Execution

- [ ] Task 1: OCR/evidence recovery
- [ ] Task 2: provider usage/cost accounting
- [ ] Task 3: spending-change integration
- [ ] Task 4: sample/ranking optimization (deferred)
- [ ] Task 5: final release verification

### Critical implementation wave

- Task 1 implementer: Popper, agent `01a09a21-c70a-79b2-a953-e01b39da0106`; base `1f36f17f10ab666a2c2ca6efd8d41a696bafa9ca`; report `task-1-report.md`.
- Task 2 implementer: Hegel, agent `01a09a21-c6ac-7903-8bbd-48e03de2ace3`; base `1f36f17f10ab666a2c2ca6efd8d41a696bafa9ca`; report `task-2-report.md`.
- Task 3 implementer: Epicurus, agent `01a09a21-c754-7e83-b1cc-1c7b2de89514`; base `1f36f17f10ab666a2c2ca6efd8d41a696bafa9ca`; report `task-3-report.md`.
- All implementers were dispatched without a model override so the configured model remains unchanged. Each was instructed to use RED → GREEN TDD, commit, self-review, and avoid subagents/reviewers.

### Pause point

- Task 2 implementation committed as `431f53b`; report received. Independent review is still required.
- Task 3 implementation committed as `45a766b`; report received. Independent review is still required.
- Task 1 agent was shut down by explicit user instruction before commit/report completion.
- The two planned independent reviewers and Task 5 release verification were not started. Work is paused; do not claim plan completion or submission readiness.
