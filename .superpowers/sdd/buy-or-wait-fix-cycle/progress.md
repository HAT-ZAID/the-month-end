# SDD ledger — plan: docs/superpowers/plans/2026-09-13-buy-or-wait-fix-cycle.md

## Baseline
- Branch: `sol/p1-inputs-oracle`
- Starting commit: `e6956547683889e76c21b03c828a3b4a2ea7c696`
- Baseline: `203 passed, 2 skipped`

## Planned cycle estimate
- Cycle 1: oracle and evidence safety
- Cycle 2: solver and payment-plan correctness
- Cycle 3: submission readiness

## Pre-flight ruling
Ruling: treat existing uncommitted work as user-owned baseline and modify only files required by the approved plan — because the worktree is already dirty — cost if wrong: unrelated pre-existing changes may complicate review attribution.

## Task scan
| Task | Shared files/interfaces | Finding | Ruling |
|---|---|---|---|
| Cycle 1 oracle | `oracle.py`, recurrence/evidence inputs | Must preserve the existing 91-point API while hardening unsafe fallbacks | Keep public return shape stable; add tests before implementation |
| Cycle 2 solver | `solver.py`, `options.py`, `ranking.py`, `changes.py` | Solver must consume oracle safety and preserve output contract | Defer until Cycle 1 completes |
| Cycle 3 submission | `main.py`, `evaluation/`, root artifacts | Packaging gates depend on final solver behavior | Defer until Cycles 1–2 complete |

## Status
- Cycle 1: complete (commits 09bd55e..78aa632, review clean)

## Cycle 1 verification
- Scoped recurrence review: approved after fix round 1.
- Scoped oracle review: approved after fix round 1.
- Controller full suite: 220 passed, 2 skipped.

## Remaining issues after Cycle 1
- Installment safety simulation and earliest-date propagation remain open.
- Spending-change search/wiring remains incomplete.
- Blank-amount image/OCR evidence remains fail-closed and unwired.
- Generic explanations and submission packaging/usage report remain open.
- Oracle forecast cadence/amount filtering and pending-debit timing still need a dedicated review.
- Salary-raise message amendments remain unresolved.
