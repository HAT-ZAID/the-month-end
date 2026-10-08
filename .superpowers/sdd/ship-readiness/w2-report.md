# W2 Ship-Readiness Report — Solver Pipeline (R2 Criticals)

Date: 2026-09-13. Implementer: TDD RED→GREEN, no subagents, no commits.
Scope (YOUR FILES ONLY): `code/solver.py`, `code/changes.py`, `code/parser.py` (+ tests `test_solver.py`, `test_changes.py`). Untouched as required: `code/options.py`, `code/ranking.py`, `tests/test_options.py`, `tests/test_ranking.py` (no behavior change), and all FROZEN files (`code/oracle.py`, `recurrence/fx/amounts/reconcile/main.py`, `tests/test_oracle*.py`, `test_recurrence/reconcile*/ledger/amounts/cashstates.py`, `tests/test_submission.py`, `test_invariants.py`, `spec_checks.py`, `conftest.py`, `open_issues.md`, `output.csv`, `log.txt`).
Interface freeze: `code/oracle.solve_safe_amount(user_id, request_date_str, requested_amount_str, profile, events, fx_rows, recurrence_flags)` — 7-arg calls only, verified by grep. Salary-raise via EVENT INJECTION into a deepcopy, never mutating caller, never changing oracle.
Prior rulings honored: ranking.py completion-first order untouched; solver inline boolean-deadline `spec_key` kept; options duration gate untouched.

## Summary
- DONE. All 6 tasks implemented with TDD RED→GREEN.
- Full suite: 194 passed, 2 skipped, 0 failed (baseline 179 passed, 2 skipped; +15 new tests).
- No commits.

## Task 1 — Installment safety simulation (strict default / lenient)
Change (`code/solver.py`):
- `def solve_request(..., installment_safety="strict")` (backward-compatible keyword, default strict).
- Helpers `_parse_plan_legs()` + `_installment_safe(ledger, req_date, payment_plan, profile)`: rebuild post-leg balances as `ledger[i] - cumulative_legs_up_to_day_i >= minimum_balance_to_keep` for all 91 days. Empty/missing ledger (stubbed unit tests) treated as safe; legs outside window ignored; unparseable inputs fail-open to safe.
- Strict: deadline (`_meets`) + `_installment_safe`; Lenient: deadline only (legacy).
- Deadline filter kept in both modes.

Tests (`tests/test_solver.py`):
- `test_installment_strict_rejects_late_overdraft` (ledger 10000 flat, min 2000, 1×9000 leg → 1000 < 2000, strict rejects).
- `test_installment_lenient_accepts_late_overdraft` (same fixture, lenient accepts).
- `test_installment_deadline_filter_both_modes` (completion 2026-04-01 vs deadline 2026-02-01, both modes reject).

RED evidence:
- Command: `python -m pytest tests/test_solver.py::test_installment_earliest_is_oracle_not_start tests/test_solver.py::test_spending_fallback_earliest_is_original -q`
- Output (excerpt): `TypeError: solve_request() got an unexpected keyword argument 'installment_safety'` (param missing pre-fix). Old logic had deadline-only, no ledger check (would accept overdraft).

GREEN evidence:
- Command: `python -m pytest tests/test_solver.py -q`
- Output: `14 passed in 0.08s` (after Task1+2 fixes), later `194 passed, 2 skipped`.

## Task 2 — Earliest propagation (oracle earliest, except affordable_now)
Change (`code/solver.py`):
- Installments: `"earliest_date": earliest` (oracle), not `opt["first_payment_date"]`.
- Spending fallback success: `"earliest_date_for_full_payment": earliest` (original oracle earliest without changes), not `req_date`.
- Affordable_now still `req_date`; not_affordable still `None` (spec `check_status_method_consistency` requires empty earliest for not_affordable).

Tests:
- `test_installment_earliest_is_oracle_not_start` (stub earliest 2026-02-15 vs start 2026-01-05, lenient to isolate, asserts 2026-02-15).
- `test_spending_fallback_earliest_is_original` (base earliest 2026-02-15, deadline 2026-01-10 blocks wait, fallback asserts 2026-02-15 not req_date).

RED evidence:
- Command: `python -m pytest tests/test_solver.py::test_spending_fallback_earliest_is_original -q`
- Output: `AssertionError: got {... 'earliest_date_for_full_payment': '2026-01-01' ...} assert '2026-01-01' == '2026-02-15'` (returned req_date pre-fix).

GREEN evidence:
- Command: `python -m pytest tests/test_solver.py -q` → `14 passed`; full `194 passed, 2 skipped`.

Gold alignment: req_02 (start 2025-08-08 vs earliest 2025-09-15) now propagates oracle; req_06 (plan 01-03 vs earliest 01-15) now returns original earliest.

## Task 3 — Spending fallback (all-miss) + real recurrence_flags threading
Change (`code/changes.py`):
- `def search_changes(..., recurrence_flags=None)`: `flags_to_use = recurrence_flags if not None else set()`; oracle call uses `flags_to_use`. Default preserves legacy `set()` so existing tests pass.

Change (`code/solver.py`):
- Compute `_all_miss = viable and desired_present and all(not _meets(completion))`; condition `if (not viable) or _all_miss:`.
- Call `search_changes(..., events_for_oracle, fx_rows, recurrence_flags)` (real flags, modified copy).
- Kept permitted-category/window/≤3 rules untouched in `changes.py`.

Tests:
- `tests/test_changes.py::test_search_changes_accepts_optional_recurrence_flags` (default == explicit set() == real flag set for simple fixture).
- `tests/test_solver.py::test_spending_fallback_when_all_viable_miss_deadline` (full viable safe>=req but req_date 2026-02-01 misses deadline 2026-01-15; asserts search called + `stop:event_x`).
- `tests/test_solver.py::test_solver_threads_recurrence_flags` (mocks detect to `{("u1","dining","expense")}`, captures flags).

RED evidence:
- Command: `python -m pytest tests/test_changes.py::test_search_changes_accepts_optional_recurrence_flags -q`
- Output: `TypeError: search_changes() takes 6 positional arguments but 7 were given`.
- Command: `python -m pytest tests/test_solver.py::test_spending_fallback_when_all_viable_miss_deadline tests/test_solver.py::test_solver_threads_recurrence_flags -q`
- Output: `assert None is True` (fallback not called, returned affordable_now without changes); `must thread real flags, got {'flags': None}`.

GREEN evidence:
- Command: same 3 tests `-q` → `3 passed`; full `189 passed, 2 skipped` at that stage.

## Task 4 — meets_deadline NaN/"" normalization via date parse
Change (`code/solver.py`):
- Top-level `def meets_deadline(comp_date, desired_completion_date)` + `_parse_deadline_date()` (strip, lower `{"nan","none","null","nat","n/a","na",""}`, float NaN, `date.fromisoformat`; missing/NaN either side → True, else `comp <= desired`).
- Inner `def _meets(comp): return meets_deadline(comp, desired_completion_date)`; updated 4 call sites (partial, installments, wait, spec_key).

Tests (`tests/test_solver.py`):
- `test_meets_deadline_nan_empty_normalization` (direct import, both sides NaN/empty → True; ordering still enforced).
- `test_meets_deadline_whitespace_desired_is_no_constraint` (desired `"   "` allows partial; old `str<=" "+` returned False).

RED evidence:
- Command: `python -m pytest tests/test_solver.py -q` (with initial comp-nan via solve_request)
- Output: `FAILED ... test_meets_deadline_comp_nan_is_no_constraint - assert 'not_recommended' == 'partial_payment'` (old `str(comp) <= str(desired)` mishandled `"nan"`).

GREEN evidence:
- Command: `python -m pytest tests/test_solver.py -q` → `9 passed` (after fix).

## Task 5 — Parser reuse + salary-raise via event injection (deepcopy)
Change (`code/parser.py`):
- `from code.amounts import extract_amounts, parse_amount`.
- Salary path: `parse_amount(raw_num, currency, lang)` (`lang="id"` when `gaji` present for IDR dot-thousands), fallback to `extract_amounts`; skips when None. Preserves EMP-0001 (raise + date → update) vs EMP-0002 (confirmation, no amount → none) vs bonus/contract-ended (no salary amount → none).
- Event-amend path: `parse_amount(raw_a, ccy_a)`; skips blank `related_event_id` (avoids empty updates).

Change (`code/solver.py`):
- `events_for_oracle = deepcopy(events)` at entry; all oracle calls (`res_now`, `search_changes`, `res2`) use copy; event-amendments mutate copy only.
- Salary injection loop: validate `effective_date` ISO + amount Decimal; skip if `eff > req+90` (after-window no effect, avoids polluting oracle average which includes future amounts); amend existing salary income on same settlement/event date else append synthetic `{"event_id": f"salary_raise_{eff}", "user_id", "event_type":"income", "category":"salary", "direction":"credit", "amount","currency","event_date","settlement_date":eff, "status":"scheduled", ...}`.

Tests (`tests/test_solver.py`, real oracle, no stubs):
- `test_salary_raise_before_req_shifts_safe_up` (stale 3×3000 + 6000 expense Mar01, req 2026-02-01/5000; raise to 5000 eff 2026-01-15 → safe up).
- `test_salary_raise_after_window_no_effect` (same base, eff 2026-06-01 > window 2026-05-02 → safe equal).
- `test_salary_injection_does_not_mutate_caller` (deepcopy check).
- `test_parser_emp0001_and_emp0002_patterns` (EMP-0001 → 1 update 42750000/2025-08-15; EMP-0002/bonus → none).
- `test_parser_indonesian_grouping_via_amounts` (IDR 42.750.000 → 42750000).

RED evidence:
- Command: `python -m pytest tests/test_solver.py::test_salary_raise_before_req_shifts_safe_up ...test_parser_indonesian_grouping_via_amounts -q`
- Output: `assert Decimal('3000.0') > Decimal('3000.0')` (no injection, safe same); `decimal.InvalidOperation: [<class 'decimal.ConversionSyntax'>]` at `code/parser.py:45` (`Decimal("42.750.000")` crash).

GREEN evidence:
- Command: same 5 tests `-q` → `5 passed` (after window-skip fix; intermediate 4 passed/1 failed before adding `eff > win_end` skip, then 5 passed).
- Full: `194 passed, 2 skipped`.

## Task 6 — ranking.py identical
- No edit to `code/ranking.py`; behavior verified by existing `tests/test_ranking.py` (9 tests incl. deadline>total, no-changes>total, start>legs) still passing in full suite.

## Verify
- Per-item RED+GREEN above.
- Final: `python -m pytest tests/ -q` → `194 passed, 2 skipped in 5.93s` (also `5.78s` run identical). Zero failures.
- Interface: `grep solve_safe_amount` shows 7-arg calls only in `solver.py`/`changes.py`; `oracle.py` untouched.
- Prior rulings: `ranking.py` untouched; `solver spec_key` still `0 if _meets(...) else 1`; `options.py` untouched.

## Files changed
- `code/solver.py` (Tasks 1-5)
- `code/changes.py` (Task 3 signature + flags)
- `code/parser.py` (Task 5 amounts reuse)
- `tests/test_solver.py` (+15 tests total across tasks)
- `tests/test_changes.py` (+1 flags test)

## Concerns
- Strict ledger treats empty/stubbed ledgers as safe (backward compat); real ledgers are 91-long. Legs beyond 90d window are unchecked (no forecast beyond horizon).
- After-window raise skip is required because oracle averages all future salary amounts (including beyond window); without skip, after-window raises would pollute average and shift safe.
- Salary injection is single-event amend/inject at effective date (not full forward curve); modest raises preserve CV≤0.5 recurrence, huge raises could break recurrence and reduce forecast (documented oracle behavior, frozen).
- Not_affordable earliest stays None per `check_status_method_consistency` (spec requires empty), even when oracle earliest exists.
