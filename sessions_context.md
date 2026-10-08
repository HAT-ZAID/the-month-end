# sessions_context.md — Buy or Wait? systematic-debugging session

## Objective
Reduce `python -m code.verify_samples` mismatches (started 78) and produce correct `output.csv` for 250 evaluation requests.

## 4-agent investigation (read-only, no writes)
Dispatched 4 parallel `general` subagents under systematic-debugging Phase 1/2:

1. **Oracle ledger** — traced `solve_safe_amount()` for request_02 (2025-08-05, user_02) and request_03 (2019-09-03, user_03). Found forecast collision guard `if not any(e["date"]==next_date)` drops 8/12 forecasts nondeterministically via `set` ordering; safe 17414389 vs 21431067 across runs; earliest None vs installment date confusion.
2. **Solver pipeline** — mapped solver.py A/B/C/D → rank_options → best. Confirmed ranking key mismatch (solver passes `cost/num_payments/option_id`, ranking expects `total_cost/completion_date/changes/legs/start_date/id` → always tie, picks opt_full_now). Confirmed `search_changes` imported but never called → spending_changes always none. Confirmed options.py ignores `payment_methods_user_will_consider`, uses single-leg amount as cost.
3. **Pattern working vs broken** — request_01/09/12 pass at oracle level (01 has scheduled salary in-window, 09/12 have 0 events + buffer). Broken split into `0 vs positive` (missing income) and `too high` (under-reserved weekly groceries skipped by avg<14, FX_UNKNOWN→0 silently, pending/failed/cancelled unfiltered).
4. **Evidence/forecast** — message_02 salary confirmation lost (parser regex needs `IDR <number>`, solver `pass`); event_253 blank salary + image_01 (4365000) unwired; pending debit correctly reserved by accident; anchor bug `next_date=req_date` injects forecast on day0; collision makes request_03 flip 0 vs 1909048 across processes.

## Hypotheses → fixes (one variable at a time)
- **H1 oracle collision + determinism** — TEST: temp `test_oracle_collision.py` FAIL (drop 853k vs expected both-cats). FIX: add `category` to processed_events, `sorted()` cats, same-category-only guard. VERIFIED deterministic, both cats kept. Mismatches 78→80 (more conservative, expected).
- **H2 ranking contract** — FIX: options.py adds `total_payable` + `completion_date`; solver.py adds `id/total_cost/completion_date/changes/legs/start_date`. `test_ranking.py` 6 passed. Mismatches unchanged 80 (many safe=0 → no viable options).
- **H3 ledger: status filter + income forecast + anchoring** — FIX: skip failed/cancelled/unrealized/non_cash, skip pending credits; forecast recurring `income` cats; roll-forward `while next_date < req_date` instead of anchoring to req_date. Request_02 safe 0→46M→24M (expected 17M), earliest None→08-05→08-14 (expected 09-15). Mismatches 80→93→92.

## Current state (completion pass)
- `pytest tests/`: **124 passed, 2 skipped, 0 failed**.
- `tools/verify_samples`: 71 sample exact-match diffs remain (gold style), but every built-row validity gate passes.
- `output.csv`: regenerated 250 rows via `python -m code.main` (LF endings).
- Dev verification moved out of `code/` → `tools/` so submission scan is clean; `code/main.py` runs as both `-m` and script.

## Next steps (needs human decision)
1. Decide scope: chase sample-exact (<10 mismatches) vs spec-correct + document gaps?
2. If chase: implement eligibility filter + 6-rank order (complete-by-deadline, no-changes, minimize total, start earlier, fewer legs, lowest option_id) + wire `search_changes` + weekly forecast policy.
3. Re-run `verify_samples`, then `python -m code.main` for 250-row `output.csv`.

## Files touched
- `code/oracle.py` (collision, determinism, status filter, income forecast, anchoring)
- `code/solver.py` (ranking keys)
- `code/options.py` (total_payable, completion_date)
- Temp: `C:\Users\zaidk\AppData\Local\Temp\opencode\test_oracle_collision.py`, `issue.txt`

## Latest execution handoff — stopped by user instruction

Date: 2026-09-13. The approved critical implementation wave was started with the configured model unchanged. The user then instructed that work stop with approximately 5% of the task remaining; no further implementation, review dispatch, packaging, or artifact regeneration was performed after that instruction.

### Completed implementation commits

- Task 2 provider usage/accounting: commit `431f53b`.
  - Focused LLM tests: `13 passed`.
  - LLM plus submission checks: `23 passed, 1 deselected`.
  - Bounded run used one provider call, which failed fast; `249` calls were rejected by the cap, and all `250` explanations fell back deterministically.
  - Reports were updated at `evaluation/usage_report.md` and `tools/evaluation/usage_report.md`.
  - No successful live provider response was obtained, so nonzero live token/cost evidence is still unavailable.

- Task 3 spending-change integration: commit `45a766b`.
  - Focused change/solver integration tests: `47 passed`.
  - Invariant subset: `11 passed, 1 deselected`.
  - Changed plans now enter the common ranking pool and preserve the original no-change earliest-date result.

### Stopped work

- Task 1 OCR/evidence recovery was still active and was explicitly shut down before it produced a commit or report. The unresolved OCR/image issue remains the primary critical gap.
- The two planned independent review agents were not launched.
- Task 5 final release verification was not run. `output.csv` and `code.zip` should be treated as not freshly verified against commits `431f53b` and `45a766b`.
- Task 4 sample-exact optimization remains deferred.

### Known verification state

- Task 2 agent reported full-suite result: `267 passed, 1 skipped, 9 failed`, with failures attributed to the still-pending OCR/evidence and spending/output worktree state.
- Task 3 agent reported full-suite result: `274 passed, 1 skipped, 2 failed`; remaining failures were the OCR/image expectation and stale `output.csv` invariant.
- These results are implementation-agent evidence, not final release approval, because the required independent reviews and final artifact run were intentionally skipped.

### Safe continuation point

1. Read `AGENTS.md`, this handoff, and `.superpowers/sdd/2026-09-13-buy-or-wait-compliance-fixes/progress.md`.
2. Inspect commits `431f53b` and `45a766b` and the two task reports before changing code.
3. Run the two independent reviews requested in `plan_a.md` against the combined implementation.
4. If reviews are clean or fixes are completed, run the final full suite, regenerate `output.csv`, rebuild `code.zip`, and update both `plan_a.md` and the usage reports with exact final-run evidence.
5. Do not claim submission readiness until OCR resolution, review results, output invariants, archive contents, and usage-report consistency are all verified.


