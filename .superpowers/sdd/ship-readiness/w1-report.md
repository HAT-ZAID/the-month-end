# W1 Ship-Readiness Report — Oracle Slice (TDD RED→GREEN)

Date: 2026-09-13. Implementer: oracle slice (strict TDD, no subagents, no commits).
Scope: `code/oracle.py`, `code/recurrence.py` + `tests/test_oracle_timing.py`,
`tests/test_oracle_fx.py`, `tests/test_oracle_cashstates.py`.
Frozen untouched: `code/solver.py`, `ranking/options/changes/parser/main.py`,
`tests/test_ranking.py`, `test_changes/options/solver/submission/invariants.py`,
`spec_checks.py`, `conftest.py`, `open_issues.md`, `output.csv`, `log.txt`.
Interface freeze kept: `solve_safe_amount(user_id, request_date_str, requested_amount_str, profile, events, fx_rows, recurrence_flags) -> {safe_amount: Decimal, earliest_date: str|None, ledger: list length 91}`.

Baseline: `python -m pytest tests/ -q` → `170 passed, 2 skipped, 1 xfailed` (xfail=`test_unknown_status_ignored`).
Final: `python -m pytest tests/ -q` → `179 passed, 2 skipped` (zero failures, zero xfail).

Method: skill `test-driven-development` followed exactly — test first, verify RED reason, minimal fix, verify GREEN.

---

## 1. Unknown-status allowlist (R1 Critical #2)

Rule: statuses outside `settled/scheduled/pending` IGNORED; `pending+credit` skipped, `pending+debit` counted.

RED:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_unknown_status_ignored -v --runxfail
FAILED tests/test_oracle_cashstates.py::test_unknown_status_ignored - AssertionError: assert Decimal('2000.0') == Decimal('3000')
```
Also new blank-status pin failed the same way:
```
$ python -m pytest ...::test_blank_status_ignored ...
FAILED - AssertionError: assert Decimal('2000.0') == Decimal('3000')
```
Reason verified: `code/oracle.py:89-97` only excluded `failed/cancelled/unrealized` + `pending+credit`; `"weird"/""` fell through to COUNT.

Fix (`code/oracle.py`): allowlist gate
```python
if status not in _ALLOWED_STATUSES:  # ("settled","scheduled","pending")
    continue
```
kept `non_cash` + `pending+credit → skip` below it. Uses `_norm_status/_norm_dir` (`.get()`).

GREEN:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_unknown_status_ignored -v
XPASS (strict=False)  # pre-marker-removal confirmation
$ # removed @pytest.mark.xfail marker
$ python -m pytest tests/test_oracle_cashstates.py::test_unknown_status_ignored -v
PASSED
$ python -m pytest tests/test_oracle_cashstates.py::test_blank_status_ignored -v
PASSED
```

## 2. Duplicate / linked_event_id dedup (R1 Critical #1)

Rule: collapse by `event_id` + skip rows superseded via `linked_event_id` with
`cancellation > newer > settled > safer` before the window loop.

RED:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_duplicate_and_linked_dedup_counts_once -v
FAILED - AssertionError: assert Decimal('0') == Decimal('2000')
```
Test: `dup1` + exact duplicate `dup1` + `retry1→dup1`, all `$1000 debit settled 2026-01-02`.
Triple-count gives `5000-3000=2000 → safe 0`; single-count gives `safe 2000`.
Reason verified: `code/oracle.py:87-129` posted every in-window row, no `event_id`/`linked_event_id` handling.

Fix (`code/oracle.py` `_dedup_events` + `_pick_winner` + call after user filter):
- Group by stripped `event_id` (blank ids stay unique); winner by `_pick_winner`.
- Group `linked_event_id → [target + pointers]`; only collapse when normalized
  directions match (refund lifecycles `debit vs credit` keep both — link alone does not determine cash, AGENTS 6.1).
- `_pick_winner` tuple `(ignored_rank, -ordinal, settled_rank, safer, event_id)`:
  countable (`settled/scheduled/pending-debit`, not `non_cash`) first (=cancellation drop),
  then newer date, then `settled(0)<scheduled(1)<pending(2)`, then safer
  (`-amt` for debit = larger reserves wins; `+amt` for credit = smaller wins), then `event_id` for determinism.

GREEN:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_duplicate_and_linked_dedup_counts_once -v
PASSED
```
Refund-pair safety: `event_98(debit)→event_99(credit)` directions differ → both kept; retry `failed→scheduled` same direction → single survivor (failed already ignored, no double-reserve).

## 3. Robust forecasting (R1 Critical #4 + #5)

Rules: median intervals; require `>=2` pre-request dates else skip; Decimal averages
over `settled/scheduled (+pending+debit for expenses)` only, same `event_type`.

RED (all three fail for the expected arithmetic reason):
```
$ python -m pytest tests/test_oracle_timing.py::test_median_interval_uses_median_not_mean ...
FAILED - AssertionError: assert Decimal('5000') == Decimal('4000')
  # intervals [30,30,90]: mean 50 → 2026-05-20, median 30 → 2026-04-30 (day29)

$ python -m pytest tests/test_oracle_timing.py::test_single_history_does_not_forecast ...
FAILED - assert False  # single history invented avg_interval=30 forecast

$ python -m pytest tests/test_oracle_timing.py::test_forecast_average_excludes_failed_and_pending_credit ...
FAILED - AssertionError: assert Decimal('3800.0') == Decimal('4000')
  # polluted mean (1000*3+10000+10000)/5=4600 → 5000-4600=400? actual 3800 via rolling; filtered avg 1000 → 4000
```
Reason verified: `code/oracle.py:138-139,183-185` mean `sum//len`; `142-143,188-189`
single-date fallback `30`; `146-160,190-200` float mean over all rows incl. `failed/pending-credit` and mixed `event_type`.

Fix (`code/oracle.py`):
- `_median_interval()` (sorted, odd→middle, even→`(a+b)//2`).
- `if len(dates) < 2: continue` in both protected + income loops (no invented cadence).
- `_history_eligible(e, for_expense)` gate for both date collection and amount averaging;
  expenses: `settled/scheduled` + `pending+debit`; income: `settled/scheduled` only; same `event_type`; `non_cash` excluded.
- `_amount_abs()` Decimal path (`is_finite` check, `abs`), signed for expenses (`-d` if debit), positive for income; `sum(..., Decimal("0"))/Decimal(n)`.

GREEN:
```
$ python -m pytest tests/test_oracle_timing.py::test_median_interval_uses_median_not_mean \
    tests/test_oracle_timing.py::test_single_history_does_not_forecast \
    tests/test_oracle_timing.py::test_forecast_average_excludes_failed_and_pending_credit -v
3 passed
```

## 4. Fail-closed 91-length ledger (R1 Critical #3)

Rule: `safe=0, earliest=None, ledger length 91` on `FX_UNKNOWN`.

RED:
```
$ python -m pytest tests/test_oracle_fx.py::test_fx_unknown_forces_zero_safe_amount -v
FAILED - assert 0 == 91 (len([]))
```
Reason verified: `code/oracle.py:220-225` returned `"ledger": []`.

Fix: return `[start_bal for _ in range(91)]` placeholder (computed balances would be
misleading under ambiguity; placeholder keeps the 91-point contract for ranker/harness).

GREEN:
```
$ python -m pytest tests/test_oracle_fx.py::test_fx_unknown_forces_zero_safe_amount -v
PASSED  # now asserts safe==0, earliest is None, len==91
```

## 5. Hardening (small)

(a) Protected split: strip + drop `nan`/empty.

RED:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_protected_split_strips_whitespace_and_nan -v
FAILED - assert Decimal('5000') < Decimal('5000')  # " rent " mismatch → no forecast
```
Fix: split `|`, `strip()` each, skip `""` and `lower()=="nan"`.

GREEN: `PASSED`.

(b) `.get()` on event dicts in hot paths.

RED:
```
$ python -m pytest tests/test_oracle_cashstates.py::test_missing_keys_do_not_crash -v
KeyError: 'currency' at code/oracle.py:107
```
Fix: `e.get("user_id")` filter, `e.get("currency")/e.get("amount")/dir_norm` via
`_norm_*` helpers; `code/recurrence.py` `detect_recurrence` now `e.get(...)` + skip on
`None`/non-dict (was `e["user_id"]/e["category"]/e["event_type"]`).

GREEN: `PASSED` (no crash, `len(ledger)==91`).

(c) Pending-debit semantics pin (keep current: post on settlement date).

Characterization (passed before and after, as intended — documents behavior + adds comment):
```
$ python -m pytest tests/test_oracle_cashstates.py::test_pending_debit_posts_on_settlement_date -v
PASSED  # ledger[0]==5000, ledger[4]==4000, safe==2000
```
Code comment added: “Pending debits post on their settlement date (not held from req_date).”

## 6. Perf (trivial only, no restructure)

- Removed per-event `import math` inside forecast loops (now Decimal helpers, no float).
- Hoisted `window_end`, single user-filter, `_ALLOWED_STATUSES` module constant,
  per-event `dir_norm` computed once and reused (was `str(...).strip().lower()` twice).
- No ranking/ledger restructure; `fx.resolve_rate` per-event sort untouched (non-trivial to hoist safely).

---

## VERIFY — full suite + interface

```
$ python -m pytest tests/ -q
179 passed, 2 skipped in 5.66s
```
Zero failures; zero xfail (prior xfail flipped to XPASS then marker removed — passes clean).
Skips are pre-existing (2, same as baseline).

Interface:
```
$ python -c "import inspect; from code.oracle import solve_safe_amount; ..."
(user_id: 'str', request_date_str: 'str', requested_amount_str: 'str', profile: 'dict', events: 'list[dict]', fx_rows: 'list[dict]', recurrence_flags: 'set[tuple[str, str, str]]') -> 'dict'
['earliest_date', 'ledger', 'safe_amount']
Decimal 2026-01-01 91
failclosed 0 None 91
```
Signature and `{safe_amount: Decimal, earliest_date: str|None, ledger: 91}` shape unchanged.

Files changed this wave (allowed only):
- `code/oracle.py` (allowlist, dedup, median/Decimal/>=2 gate, 91-ledger, strip/nan, `.get()`, hoist, pending comment)
- `code/recurrence.py` (`.get()` + skip missing keys)
- `tests/test_oracle_cashstates.py` (removed xfail; added blank-status, dedup, protected-strip, missing-keys, pending-debit pin)
- `tests/test_oracle_timing.py` (added median, single-history, polluted-average pins)
- `tests/test_oracle_fx.py` (added `len(ledger)==91` assertion)

---

## Concerns / residual risks

- Linked dedup scope is intentionally narrow (same-direction only). Cross-category same-direction
  links collapse to one survivor — matches retry semantics but could undercount if a linked pair
  represents two genuinely separate same-direction cash moves. Refund pairs (different direction)
  are explicitly preserved per AGENTS 6.1.
- Forecast ignores FX (amounts assumed home currency) — pre-existing behavior kept; foreign recurring
  amounts may mis-forecast.
- `pending+debit` history included for expenses but excluded for income (per task). A pending salary
  credit that later settles will not seed income cadence until settled.
- Even-interval median uses integer `(a+b)//2`; sub-day rounding not applicable (day-granular ledger).
- `test_oracle_cashstates.py` still imports `pytest` (now unused after xfail removal) — harmless, left to minimize diff.
