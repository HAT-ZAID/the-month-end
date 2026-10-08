# Task 3 Report: Permitted Spending Changes as First-Class Plans

## Status

Implemented and committed as Task 3. The shared `code/ranking.py` function was left untouched because this task keeps the solver's existing inline specification-order key authoritative; changed options now enter that same ranking pool.

## RED evidence

Added RED coverage before the production changes for:

- one-time events without recurrence evidence;
- cross-user events;
- changed-ledger partial-payment completion;
- changed-ledger installment safety;
- changed-ledger wait completion;
- no-change preference over an unnecessary change.

Command:

```text
python -m pytest tests/test_changes.py::test_one_time_event_without_recurrence_evidence_is_rejected tests/test_changes.py::test_cross_user_event_is_rejected_even_with_matching_recurrence_evidence tests/test_solver_changes_integration.py -q
```

Expected RED output:

```text
FFFFF.                                                                   [100%]
5 failed, 1 passed in 0.27s
```

The failures were the intended pre-fix behavior: `search_changes` accepted one-time/cross-user events, and the solver returned `not_recommended` for change-resolvable partial/installment/wait fixtures.

## GREEN evidence

Focused command:

```text
python -m pytest tests/test_changes.py tests/test_solver.py tests/test_solver_changes_integration.py -q
```

Output:

```text
47 passed in 0.18s
```

Output invariant command:

```text
python -m pytest tests/test_invariants.py -q -k "not all_built_rows_pass_every_gate"
```

Output:

```text
11 passed, 1 deselected in 0.11s
```

Full command:

```text
python -m pytest -q
```

Output:

```text
2 failed, 274 passed, 1 skipped in 39.44s
```

The two failures are outside the Task 3 implementation: the existing OCR/image expectation failure in `tests/test_hybrid_evidence.py`, and the built-output invariant failure against the pre-existing `output.csv` artifact. The latter reports stale `not_affordable` rows with non-empty earliest dates; the Task 3 solver fallback itself remains empty-earliest and the focused invariant checks pass.

## Change-plan integration evidence

The Task 3 section of `docs/superpowers/plans/2026-09-13-buy-or-wait-compliance-fixes.md` requires validated `search_changes` actions, common solver option fields, recurring/flexible/user/permission/window/protection filtering, changed partial/installment/wait variants, common ranking, and independent no-change earliest-date preservation. The SDD progress ledger at `.superpowers/sdd/2026-09-13-buy-or-wait-compliance-fixes/progress.md` assigns Task 3 to `code/changes.py`, `code/solver.py`, and change tests, and explicitly requires preserving the existing ranking contract.

Implemented evidence:

- `search_changes` now requires same-user expense events with recurrence evidence, debit cash states, flexible actions, permitted categories, unprotected categories, and request-date + 90-day-window dates; it retains the one-to-three-change cap and stop/reduce exclusivity.
- Changed solver variants cover full payment, partial payment, installments, and wait; each carries `changes`, `completion_date`, `total_cost`, `start_date`, `legs`, and `option_id`.
- Changed variants are ranked with unchanged variants using deadline, no changes, total cost, start date, legs, and option ID.
- `earliest_date_for_full_payment` is sourced from the original no-change oracle for viable recommendations; the `not_affordable` fallback remains empty per the output invariant.

## Changed files

- `code/changes.py`
- `code/solver.py`
- `tests/test_changes.py`
- `tests/test_solver.py`
- `tests/test_solver_changes_integration.py`
- `.superpowers/sdd/2026-09-13-buy-or-wait-compliance-fixes/task-3-report.md`

`code/ranking.py`, `output.csv`, and unrelated user changes were not modified by this task.

## Self-review

- `search_changes` preserves the `list[str]` interface and emits only `stop:<event_id>` or `reduce_to:<event_id>:<amount>` strings.
- Candidate actions are fail-closed for ownership, event type, recurrence evidence, flexible status, debit direction, permitted category, protected category, and date window.
- The solver does not return early after a spending change; it adds changed plans to the common pool and ranks them.
- Changed installment plans use the changed ledger for strict per-leg safety.
- The original no-change safe amount and earliest date are not overwritten by changed oracle results.
- Focused tests and syntax compilation pass; no model configuration, subagent, reviewer, or secret was used.

## Concerns

- Full pytest is not entirely green because of the pre-existing OCR fixture failure and stale root `output.csv` invariant failure. Regenerating release artifacts was intentionally not done because `output.csv` is outside the Task 3 write scope and is user-owned work.
- No changes were made to `code/ranking.py`; a later ranking-consolidation task may make that shared helper authoritative.
