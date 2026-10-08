# Task 1 Report — Teach the oracle to suppress forecasted spending changes

**Status:** DONE
**Branch:** `sol/p1-inputs-oracle`
**Commit:** `4faecd3` — feat: forecast flexible recurring categories with category suppression

## What I implemented

All changes in `code/oracle.py`, exactly as the brief's Step 3 specifies:

1. **New optional keyword** on `solve_safe_amount`: `suppressed_categories: set[str] | None = None` (after `recurrence_flags`). Default `None` keeps every existing caller working (7-arg calls in `code/solver.py`, `code/changes.py`, and all existing tests).
2. **Normalization** placed immediately after the protected-categories parse:
   `suppressed = {str(c or "").strip().lower() for c in (suppressed_categories or set()) if str(c or "").strip()}` — drops empties/`None`, case-folds, so `{"", "no_such_category"}` is harmless.
3. **Widened forecast set (ruling T1-b):** renamed `protected_recurring_cats` → `recurring_expense_cats` and `protected_cat_freq` → `recurring_expense_freq` (plus `is_prot`→`is_exp`, `prot_ok`→`exp_ok` for name honesty). It is now built from *every* category carrying an expense **or** subscription recurrence flag, not only protected ones. Income set (`recurring_income_cats`) untouched — suppression can never remove income.
4. **`_history_eligible(e, for_expense=True)` accepts `subscription` alongside `expense`** (oracle.py:139-141), so flexible subscription rows count for both frequency and amount averaging. Every other rule (`non_cash` rejected, status in `settled/scheduled/pending`, pending requires debit) is unchanged; the income path is unchanged.
5. **Suppression in the expense forecast loop** (oracle.py:335-360): `cat_events` is still built with the *unsuppressed* eligibility rule (suppression does not widen history eligibility); only the forecast delta changes — `avg_amount = Decimal("0")` when `cat in suppressed`. Next-date rolling and `ESSENTIAL_FORECAST` emission are untouched, so a suppressed category still yields zero-valued forecast rows and the 91-point ledger shape holds. In-window historical events are never suppressed ("historical amounts stay in the ledger").

## What I tested and results

- New file `tests/test_oracle_changes.py` (verbatim from brief Step 1): **5 tests, all pass.**
- Targeted run (Step 5): `tests/test_oracle_cashstates.py tests/test_oracle_timing.py tests/test_oracle_fx.py` → **24 passed**.
- Full suite: **283 passed, 1 skipped, 0 failed, no warnings** (output pristine).

## TDD Evidence

**RED** — `python -m pytest tests/test_oracle_changes.py -v` → `5 failed in 0.22s`:

- 4 failures exactly as the brief predicted: `TypeError: solve_safe_amount() got an unexpected keyword argument 'suppressed_categories'` (tests 2–5, which pass the new kwarg).
- 1 failure on the baseline test (which passes no kwarg), and it is the failure the test comment anticipates:
  `AssertionError: assert Decimal('10000') < 10000` — the flexible subscription is never forecast, so the ledger is flat at the starting balance.

Both failure modes were expected: the missing parameter (brief Step 2) and the missing baseline forecast (ruling T1-b — suppression alone is a no-op until the forecast set is widened).

**GREEN** — `python -m pytest tests/test_oracle_changes.py -v` → `5 passed in 0.05s`:

```
tests/test_oracle_changes.py::test_baseline_forecasts_flexible_recurring_category PASSED
tests/test_oracle_changes.py::test_suppressing_flexible_category_raises_safe_amount PASSED
tests/test_oracle_changes.py::test_suppression_preserves_ledger_shape_and_day_zero PASSED
tests/test_oracle_changes.py::test_no_suppression_matches_default_behavior PASSED
tests/test_oracle_changes.py::test_unknown_suppressed_category_is_ignored PASSED
```

Note: brief Step 4 says "Expected: 3 passed"; the Step 1 file contains 5 test functions, so 5 is the correct count (see Concerns).

**Full suite** — `python -m pytest tests/ -q` → `283 passed, 1 skipped in 40.07s`.

## Files changed

- `code/oracle.py` — modified (new param, normalization, widened forecast set, subscription eligibility, suppression).
- `tests/test_oracle_changes.py` — new (5 tests).

Committed only these two files (`git add code/oracle.py tests/test_oracle_changes.py`). The working tree contains unrelated pre-existing modifications (README, ranking, reconcile, usage reports, etc.) which I did not touch or stage.

## Existing tests updated

**None.** No existing test needed editing: the full suite passed on the first post-implementation run (283 passed, 1 skipped), so there are no old/new assertion pairs to record. Step 5's anticipated intentional failures (tests encoding the old, narrower baseline) did not materialize — no existing test has unprotected recurring categories whose expectations move under the widened forecast. The `suppressed_categories=None` path was never edited to hide a mismatch.

## Self-review findings

- **Completeness:** every brief Step 3 element implemented verbatim (signature, normalization, widened set, subscription eligibility, suppression branch, untouched income loop, untouched next-date rolling/emission). Edge cases: empty-string/`None`/unknown suppressed categories ignored (tested); ledger always 91 points (tested); income never suppressed (structurally impossible — the income loop does not read `suppressed`).
- **Quality:** renames carried through consistently (`recurring_expense_cats`, `recurring_expense_freq`, `is_exp`, `exp_ok`); comments updated to match the widened meaning; no debug residue; test output pristine (no warnings).
- **Discipline:** no speculative code — no reduce/suppression-to-minimum logic (that belongs to a later task), no signature churn beyond the one required keyword, existing patterns (Decimal, `_history_eligible`, forecast loop shape) preserved.
- **Testing:** tests pin recurrence flags rather than depending on `code.recurrence` internals; assertions use `min(ledger)` per ruling T1-a so the requested-amount clamp cannot mask the effect; TDD order followed strictly (test → RED → implement → GREEN).

## Issues / concerns

1. **Brief count mismatch (benign):** Step 4 says "Expected: 3 passed" but Step 1's verbatim test block defines 5 tests. I implemented the verbatim block; GREEN is 5 passed. The test code is the authoritative acceptance criterion.
2. **Brief Step 5 predicted possible existing-test failures; none occurred** — recorded above under "Existing tests updated" (empty).
3. **`protected_cats` is now parsed but unused inside the oracle** (protection no longer gates forecasting; it still gates spending-change eligibility in `changes.py`/`solver.py`). I kept the parse because the brief explicitly anchors the suppression normalization "after protected categories are parsed". Harmless, but it is a dead local inside `oracle.py`.
4. **Suppression match is exact against the raw category label** (`if cat in suppressed`), while the supplied set is lowercased — verbatim from the brief. A mixed-case category label would not match. Observed dataset categories are lowercase; left as specified rather than deviating.
5. **Line-ending git warnings** (`LF will be replaced by CRLF`) appeared on both files — a pre-existing repo-wide `core.autocrlf` behavior, not introduced by this change; file contents are correct.
