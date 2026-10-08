# Task 3: Make permitted spending changes first-class safe plans

## Requirements

Modify `code/changes.py` and `code/solver.py`; modify `code/ranking.py` only if the shared ranking function becomes authoritative. Add/modify `tests/test_changes.py`, `tests/test_solver.py`, and add `tests/test_solver_changes_integration.py`.

Preserve `search_changes(...) -> list[str]`, returning only validated `stop:<event_id>` and `reduce_to:<event_id>:<amount>` actions. Solver option records must carry `changes`, `completion_date`, `total_cost`, `start_date`, `legs`, and `option_id` so changed plans use the same ranking tuple as other plans.

Use TDD. Add RED tests for: a permitted flexible recurring expense making full payment safe; no-change preferred over changed; protected-category rejection; user-permission rejection; one-time-event rejection; cross-user rejection; stop/reduce exclusivity; and the three-change cap. Verify the focused tests fail because the current solver returns `spending_changes_needed=none` or `not_affordable` for change-resolvable fixtures.

Filter candidates to the same user, recurring/flexible events within the forecast window, permitted by the relevant profile category, and not protected. Evaluate spending-change variants before final fallback, including partial payment/installments/wait where the changed ledger makes those plans safe. Add changed plans to the common ranking pool instead of returning early. Preserve the specification order: complete by deadline, no changes, lowest total paid, earlier start, fewer payments, lowest option ID. Preserve the original no-change `earliest_date_for_full_payment`; spending changes must not corrupt that independent field.

Run focused integration tests, full pytest, and output invariant checks. Commit the task.

Global constraints: preserve exact output columns and amount bounds; only validated deterministic facts affect decisions; protected/fixed/cross-user events cannot be changed; no more than three changes; do not invent facts or options; keep `log.txt` append-only.

## Acceptance

The solver emits valid spending changes when required and permitted, never changes protected/non-recurring/cross-user events, never uses more than three changes, and ranks changed versus unchanged plans according to `problem_statement.md`.
