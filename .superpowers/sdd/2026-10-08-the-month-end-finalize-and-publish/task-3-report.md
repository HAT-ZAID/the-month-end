# Task 3 report: Recompute the plan from the adjusted cashflow

**Status:** COMPLETE — committed on `sol/p1-inputs-oracle` (staged files only: `code/solver.py`, `tests/test_solver.py`).

## Step 1 — Evidence before edit (state at HEAD `ceefe2b`)

- **Guard for `search_changes`:** `code/solver.py:412` — `if (not viable_options) or _all_miss:`. `_all_miss` is set at 410-411 only when a `desired_completion_date` is present and every viable option's `completion_date` fails `_meets`. `search_changes` runs at 414 only under that guard.
- **Field provenance at HEAD:**
  - From `res2` (post-change): `res2["safe_amount"]`, `res2["earliest_date"]`, `res2["ledger"]` feed `_append_changed_options` (440-441). If an appended changed option exists **and wins ranking**, `affordability_status`, `recommended_payment_method`, `payment_plan`, `spending_changes_needed` come from it via `best` (472-476).
  - Never from `res2`: `amount_safe_to_pay = str(safe_amt)` (471, from `res_now`) and `earliest_date_for_full_payment = earliest` (475, from `res_now`) — these are correct per spec (see conflict note below).
- **Why the HEAD recompute could not change any outcome:** it only rewrote `amount` on a copy of the events (418-429). Out-of-window rows (settlement before `request_date`) never enter the daily ledger — `code/oracle.py:278` window filter — so the rewrite cannot alter balances. Those history rows *do* feed the recurring forecast through the category average (`oracle.py:333-361`), where zeroing one targeted row only shrinks the average (3×100 → (0+100+100)/3 ≈ 66.67/month) instead of removing the forecast. Result: `res2["safe_amount"]` stayed below the requested amount, `_append_changed_options` appended nothing (351 requires `changed_safe >= req`), `viable_options` stayed empty, and the row hit the pre-existing fallback return hardcoding `spending_changes_needed: "none"` (444-453) — discarding the `spending_needed` string that had been computed at 416. Nuance vs the brief's wording: the rewrite is a literal no-op only for the ledger; for the forecast it is a partial shrink — the defect conclusion (res2 ≈ pre-change → no viable option → `"none"`) holds.

## Working-tree state found

A prior interrupted attempt had already staged (uncommitted): the suppression pass-through in `solver.py` and the brief's test in `test_solver.py`. I verified both line-by-line against spec/gold/`search_changes`, added the earliest-date assertion, and committed.

## What changed

- **`code/solver.py` (recompute block, 417-441):** the change branch now collects `suppressed_cats` from the returned actions and calls `solve_safe_amount(..., suppressed_categories=suppressed_cats)`. Suppression zeroes the category's forward forecast (`oracle.py:335-338`) — the only mechanism that removes history-only subscription targets.
  - **Deviation, deliberate:** the amount rewrite on the copy was *kept* alongside suppression. `search_changes` validates each combo with both mechanisms ("two mechanisms act on disjoint date sets", `changes.py:111-138`): copy-edit removes in-window rows from the ledger, suppression removes the history-derived forecast. Dropping the rewrite would make the solver's recompute stricter than the validation that produced `changes`, silently returning to `"none"` for in-window targets. The recompute is now an exact mirror of `changes.py:114-139`.
  - Downstream: status/method/plan/`spending_changes_needed` come from the res2-derived option that wins ranking; `amount_safe_to_pay` and `earliest_date_for_full_payment` stay from `res_now` (pre-change) per spec + gold.
- **`tests/test_solver.py`:** added `test_spending_change_unlocks_full_payment_status` (brief Step 2 name/docstring verbatim). Fixture: balance 4600, floor 4000, request 600 on 2026-04-01, three settled `stoppable` streaming subscriptions (Jan-Mar). Asserts: changes ≠ `none`, `affordable_with_plan`, `full_payment`, `payment_plan == "2026-04-01:600"`, `amount_safe_to_pay ==` pre-change safe (oracle-computed), and `earliest_date_for_full_payment` empty (pre-change full payment never safe).

## Brief conflict — resolved against brief lines 11/26

Brief says `amount_safe_to_pay` must come from the recomputed result / be *strictly greater than* the pre-change safe amount. Contradicted by, and resolved in favor of, the spec and gold:

- `problem_statement.md` §90-Day: `amount_safe_to_pay` = "the most the user can pay today **before optional spending changes**"; `earliest_date_for_full_payment` = first date full passes "**without** optional spending changes". Same in AGENTS.md §6.2 and the dispatcher's Constraints.
- Gold (sample_requests.csv): req_06 `603.3 < 620.40` (its own plan leg), req_11 `12510645 < 13110000`, req_21 `1543.35 < 1574.40` — all with changes attached. Post-change safe must be ≥ requested (search_changes requires it), so a post-change amount would cap at requested_amount; gold shows less ⇒ pre-change amount.
- Gold earliest ≠ request_date on all three change rows while the plan pays full on request_date; post-change earliest would equal request_date (`search_changes` safe ≥ requested ⟹ oracle earliest = request_date at `oracle.py:448-453`) ⇒ gold earliest is pre-change.
- `tests/test_gold.py::test_gold_safe_below_requested_with_changes` already pins this signature ("safe<requested with a spending change attached").

Status/method/plan **do** come from the recomputed result (brief line 11 satisfied for those).

## Steps not run (per dispatcher)

- pytest (brief Steps 3, 5) — not run; verification handled elsewhere.
- `output.csv` regeneration (brief Step 6) — not run; outside this task's job list, and Task 8 regenerates output. Consequence: no status-distribution shift note recorded for Task 8's reviewer from this task.

## Verification by manual trace (no tests run)

`detect_recurrence` → `("u1","streaming","subscription")` (3 settled obs, CV=0 ≤ 0.5). Pre-change oracle: median interval (31,28)→29d, 4 forecast debits in [04-01, 06-30], min balance 4200 → safe = 200 < 600, earliest = None → `viable_options` empty → guard fires. `search_changes` returns `["stop:sub0"]` (fixture identical to the proven Task 2 test `test_search_changes_stops_a_subscription_that_only_exists_in_history`). Recompute with `suppressed_categories={"streaming"}` → forecast avg 0 → safe = 600, earliest = 2026-04-01 → `opt_full_changed` appended (351-360) → ranked sole option → returns `affordable_with_plan` / `full_payment` / plan `2026-04-01:600` / amount `200` (pre) / earliest `None` / changes `stop:sub0`.

## Concerns

- Brief Step 2's "strictly greater than pre-change" assertion was implemented as **equality** with pre-change (spec/gold-backed, see conflict section).
- Broad `except Exception: pass` around the change branch (442-443) can still silently emit `"none"` if the recompute raises; pre-existing, untouched (narrow patch).
- Ranking deliberately prefers no-change options on ties (`spec_key` changes count second); rows where changed options also miss the deadline will still emit `"none"` — spec-consistent, but means the 250-row change count will not equal the number of `search_changes` hits.
- No gold-sample event IDs in either committed file (grep verified: `event_476`, `event_989`, `event_1815`, `event_1816` absent from `code/solver.py` and `tests/test_solver.py`).
- `log.txt` not appended (explicit dispatcher instruction overrides AGENTS.md §5).
