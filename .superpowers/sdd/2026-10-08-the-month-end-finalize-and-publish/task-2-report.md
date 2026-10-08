# Task 2 Report — Make `search_changes` evaluate real forecast impact

**Status: BLOCKED** — no commit made (hard stop tripped: all three gold change cases still emit `none`; suite also not green because two defects in the brief make its acceptance criteria unreachable as written).

**Branch:** `sol/p1-inputs-oracle` (work left uncommitted for inspection)

---

## 1. What I implemented (brief Steps 1–5, verbatim)

- **Step 1:** Added `test_search_changes_stops_a_subscription_that_only_exists_in_history` to `tests/test_changes.py` verbatim from the brief.
- **Step 3:** `code/changes.py:86` — `event_type not in ("expense", "subscription")` (subscriptions now eligible candidates).
- **Step 4:** Removed the `req_d <= ed <= win_end` settlement-date rejection and the now-dead `req_d`/`win_end` parsing + `date`/`timedelta` import that fed it. Every other guard kept: user match, recurrence flag present, `direction == "debit"`, `status ∈ {settled, scheduled, pending}`, flexibility set, category not protected, category in `stop_ok`/`reduce_ok` per action, stop/reduce mutual exclusion per event, ≤3 actions, `reduce_to` only when `minimum_allowed_amount < amount` (`get_candidates` untouched).
- **Step 5:** Replaced the amount-mutation block with category suppression per the brief's code block: map each combo's event ids to lowercased categories, `suppressed.discard("")`, call `solve_safe_amount(..., suppressed_categories=suppressed)` on the **original** events, keep the savings calculation and "fewest changes, then max savings" preference.
- Public signature `search_changes(user_id, request_date, requested_amount, profile, events, fx_rows, recurrence_flags)` unchanged; no gold-sample event id appears anywhere in `code/changes.py`.

## 2. TDD evidence

### RED (verified before any production change)

```
> python -m pytest tests/test_changes.py::test_search_changes_stops_a_subscription_that_only_exists_in_history -v
...
E       AssertionError: expected a stop action for a stoppable recurring subscription
E       assert []
FAILED tests/test_changes.py::test_search_changes_stops_a_subscription_that_only_exists_in_history
1 failed in 0.25s
```

Expected reason confirmed: `result == []` because subscriptions were rejected (old line 92) and every event's settlement date predated `request_date` (old lines 108-115).

### GREEN — NOT REACHED

```
> python -m pytest tests/test_changes.py -q
7 failed, 7 passed in 0.23s
FAILED tests/test_changes.py::test_single_stop_makes_safe
FAILED tests/test_changes.py::test_reduction_makes_safe
FAILED tests/test_changes.py::test_stop_reduce_same_event_never_combined
FAILED tests/test_changes.py::test_two_change_only_solvable_returns_two
FAILED tests/test_changes.py::test_larger_savings_wins_among_single_changes
FAILED tests/test_changes.py::test_search_changes_requires_recurrence_evidence
FAILED tests/test_changes.py::test_search_changes_stops_a_subscription_that_only_exists_in_history   (the brief's new test)

> python -m pytest tests/ -q
7 failed, 277 passed, 1 skipped in 116.27s
```

The 7 failures are exactly the two brief defects described in §6 (6 pre-existing tests + the brief's new test). Baseline before my change: 283 passed / 1 skipped, so nothing outside `test_changes.py` moved.

## 3. Step 7 hard stop — `python -m tools.verify_samples`

Exact output for the three gold change cases (invoked as `python -m tools.verify_samples`, per ruling T4-a):

```
Audit completed. Found 76 mismatches.
[request_06] spending_changes_needed: expected stop:event_476, got none
[request_11] spending_changes_needed: expected reduce_to:event_989:665950, got none
[request_21] spending_changes_needed: expected stop:event_1815|reduce_to:event_1816:23.50, got none
```

**Precise statement: none fired.** All three emit `none`. STOP taken; nothing committed.

### Diagnosis (what I observed)

1. **The change-search mechanism itself does fire.** Called directly with identical inputs, `search_changes` returns non-empty for all three requests:
   - `request_06 → ['stop:event_444']`
   - `request_11 → ['reduce_to:event_984:665950']`
   - `request_21 → ['reduce_to:event_1793:49.6']`
   (same permitted categories as gold, different same-category rows; exact gold row selection is the sample-exactness class ruling T2-a defers.)
2. **Why the solver never reaches it for these three:** `code/solver.py:412` only calls `search_changes` when there is no viable option (or all miss the deadline). Our oracle reports all three `affordable_now` with `amount_safe_to_pay` **clamped at the requested amount** (620.4 / 13,110,000 / 1,574.4), while gold's pre-change safe is **below** the request (603.3 / 12,510,645 / 1,543.35 → gold `affordable_with_plan` + changes). That gap is exactly the forecast-semantics class ruling T2-a deferred to the exactness task (we forecast fewer debits than gold); it lives in `code/oracle.py`/forecasting, not in `changes.py`, and is not fixable from Task 2's file scope.
3. **250-row artifact (read-only in-memory run, `output.csv` NOT written):** `search_changes` was called 79 times and returned a plan 6 times (`user_78, user_99, user_117, user_133, user_135, user_251`), yet final `spending_changes_needed != 'none'` rows = **0**. Reason: after a plan is found, `code/solver.py:418-429` recomputes by rewriting event amounts — an edit the oracle ignores for out-of-window history — so `res2` equals the pre-change result, no changed option becomes viable, and the row falls through to `"none"`. Switching that recompute to suppression is **Task 3 Step 4**; therefore Task 2 alone cannot move the 0/250 judge complaint.

## 4. Two defects in the brief (decisions needed)

**Defect A — the new test's profile numbers make it impossible to pass (any implementation).**
`current_available_balance: "1500"` < `minimum_balance_to_keep: "4000"`. The oracle computes `safe_amount = max(0, min(requested, min(ledger) - floor))` (`oracle.py:443-444`); with no income events, `min(ledger) ≤ 1500` under any suppression, so `safe_amount ≡ 0 < 600` — `assert result` can never hold. Empirically confirmed with the finished Steps 3–5: baseline safe `0` (min ledger 1100), suppressed safe `0` (min ledger 1500), `search_changes → []`.
*Options (need a ruling, cf. T1-a precedent):*
- (A1) `current_available_balance: "4600"`, floor kept at 4000 → post-suppression availability 600 = requested → green; without suppression 200 → red (test strictly proves the mechanism).
- (A2) `minimum_balance_to_keep: "800"`, balance kept at 1500 → availability 700 → green; without suppression 300 → red.
I did not pick one: changing the brief's test data is a guess about intent, and the instructions say to ask rather than guess.

**Defect B — brief Step 5 "replace mutation with suppression" breaks 6 pre-existing tests.**
Task 1's suppression deliberately only zeroes **forecast** deltas; Task 1's brief states "Historical amounts stay in the ledger; only future forecast occurrences vanish," and in-window actual rows are processed with no suppression awareness (`oracle.py:266-316` vs `335-339`). The 6 failing tests all have flexible events dated **on** `request_date` (in-window actuals): suppression is a no-op for them, so their combos never reach safe and `search_changes` returns `[]`. Pure suppression cannot satisfy them category-wide either (zeroing a whole category in-window would collapse `test_two_change_only_solvable`/`test_stop_reduce_same_event` from 2 actions to 1).
*Options:*
- (B1) **Recommended:** belt-and-braces evaluation — keep the event-level amount edit on the *copy* for in-window rows **and** pass `suppressed_categories` for forward recurrence. This keeps every guard and every existing expectation, and changes nothing on the gold path (all four gold target events — `event_476`, `event_989`, `event_1815`, `event_1816` — settle **before** their request dates, so the mutation is a no-op there; verified from `financial_events.csv`). Unapplied pending ruling (Task 6's "settled history is never rewritten" claim is about persisted data; the copy edit is simulation only — flag if the controller disagrees).
- (B2) suppression-only as written + edit the 6 tests. No brief authorizes editing them, and Task 1's brief explicitly warns against editing tests to force green.

## 5. Files changed (uncommitted)

- `code/changes.py` — Steps 3–5 (subscription eligibility, date-filter removal + dead-code cleanup, suppression-based combo evaluation).
- `tests/test_changes.py` — brief's new test added verbatim.
- `log.txt` — AGENTS.md session/turn entries appended (gitignored; not staged).
- Untouched: `code/oracle.py`, `code/solver.py`, `dataset/`, `output.csv` (Task 3/8 own regeneration).
- Pre-existing unrelated modifications in the working tree (README.md, code/amounts.py, etc.) were present before this task and were not staged or touched.

## 6. Self-review findings

- **Completeness:** all brief Steps 1–5 executed; Step 6 attempted (fails, §2); Step 7 hard stop executed and honored (§3); no commit per the stop rule.
- **Discipline:** no gold-id special-casing (grep confirms only a pre-existing docstring mention in `code/amounts.py`, untouched); `reduce_to` emission rule unchanged; no filters weakened; YAGNI — no speculative dedupe of identical suppression sets.
- **Quality notes (deferred):** `cat_by_eid` is rebuilt inside the combo loop (kept verbatim from the brief; cost is dominated by the oracle call); the redundant `pending → debit` re-check at `changes.py:92` predates this task and was left alone.
- **Verification:** RED captured before production edits; full suite run; verifier run as `python -m tools.verify_samples`; 250-row scan run read-only without writing `output.csv`.

## 7. Issues / concerns — what I need to proceed

1. **Ruling on Defect A** (test numbers): approve A1, approve A2, or supply numbers. Until then the suite cannot be green and nothing can be committed.
2. **Ruling on Defect B** (evaluation strategy): approve B1 (recommended), or authorize editing the 6 existing tests under B2.
3. **Hard-stop disposition:** all three gold cases emit `none` at the solver level due to the deferred forecast gap (§3.2), which no `changes.py` change can close. T2-a offers "an explicit recorded explanation of the remaining forecast gap" as an alternative gate; the controller's dispatch instruction says stop and report. Confirm which governs, and note that nonzero `output.csv` emission additionally requires Task 3's recompute switch (§3.3).

If the rulings come back, the remaining work is small: adjust the one test's numbers, apply the approved evaluation strategy, re-run `tests/test_changes.py` → full suite → `python -m tools.verify_samples`, then commit.

---

# Task 2 Fix Report — rulings T2-b / T2-c / T2-d applied

**Status: DONE** | **Commit: `ceefe2b` — feat: evaluate spending changes via category suppression** (staged files: only `code/changes.py` + `tests/test_changes.py`; unrelated dirty files left untouched).

## What changed after the rulings

- **T2-b (A1):** new test's `current_available_balance` changed `"1500" -> "4600"`, floor kept at `"4000"`. Post-suppression the ledger is flat at 4600, so `safe = min(600, 4600-4000) = 600 >= 600` (passes with the fix); without suppression the forecast debits drop the minimum ledger to 4200, so `safe = 200 < 600` (fails without it). The test strictly discriminates.
- **T2-c (B1):** `code/changes.py` combo evaluation now runs BOTH mechanisms: (1) the event-level amount edit on `modified_events` (in-window rows of targeted events simulated as cancelled), and (2) `suppressed_categories=suppressed` passed to `solve_safe_amount` (forward recurrence dropped). The 6 existing tests were not edited. Comments explain the disjoint date sets without internal ruling IDs (one comment initially cited `T2-c / B1`; self-review caught and removed that jargon before commit).
- **T2-d:** no change needed in `changes.py`; the recorded forecast-gap explanation in this report's §3 stands as the alternative gate. `output.csv` nonzero emission is Task 3 Step 6's binding gate.

## RED re-verification with the final (A1) test numbers

Saved the working `code/changes.py`, restored HEAD's original via `git checkout -- code/changes.py`, ran:

`
> python -m pytest tests/test_changes.py::test_search_changes_stops_a_subscription_that_only_exists_in_history -v
E       AssertionError: expected a stop action for a stoppable recurring subscription
E       assert []
1 failed in 0.20s
`

Failure reason unchanged and expected (candidate filters reject subscriptions and all pre-dated rows); then restored the fix. The RED is therefore proven for the committed test, not just the original numbers.

## GREEN — covering tests run

`
> python -m pytest tests/test_changes.py -q
14 passed in 0.05s
`

All 6 previously-failing pre-existing tests pass (B1 preserved their in-window expectations) plus the new A1 test.

`
> python -m pytest tests/ -q
284 passed, 1 skipped in 119.50s (0:01:59)
`

Pristine output: no warnings, no errors. 284 = 283 baseline + 1 new test (the 1 skip is pre-existing).

## Hard-stop record (unchanged, as predicted by T2-d)

`
> python -m tools.verify_samples
Audit completed. Found 76 mismatches.
[request_06] spending_changes_needed: expected stop:event_476, got none
[request_11] spending_changes_needed: expected reduce_to:event_989:665950, got none
[request_21] spending_changes_needed: expected stop:event_1815|reduce_to:event_1816:23.50, got none
`

76 mismatches, identical to the pre-fix count: the three gold cases still read `none` **at solver level**, which is the recorded explanation (oracle clamps `amount_safe_to_pay` at the request -> `affordable_now` -> `solver.py:412` never calls `search_changes`; called directly it returns plans for all three; 250-row emission needs Task 3's recompute switch). Not a Task 2 failure per rulings T2-a/T2-d.

## Self-review of the committed diff

- Signature unchanged; `get_candidates` untouched (`reduce_to` still emitted only when `minimum_allowed_amount < amount`); every guard kept (user, recurrence flag, debit, status, flexibility, protected, willingness, mutual exclusion, <=3 actions).
- No gold-sample event id appears in production code (grep of `code/` confirms only a pre-existing docstring mention in `code/amounts.py`, untouched).
- Dead date-window parsing removed with its import; no stale references (suite green proves it).
- Comments self-contained; no process/ruling jargon left in code; verbatim-brief code only where the brief demanded it.
- Residual minor: the new test re-imports `Decimal`/`search_changes` locally though the file imports them at top — kept because the brief's block was to be used verbatim; harmless.
