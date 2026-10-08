# Reviewer 1 — Oracle & Safety (`suppressed_categories`)

**Scope:** `code/oracle.py`, `code/fx.py`, `code/recurrence.py`, `code/reconcile.py`, `code/amounts.py`, plus item 7 (`code/main.py`) and suppression call sites in `code/changes.py`, `code/solver.py`, `tools/tui.py` as evidence.
**Mode:** read-only. Only file written: this report.
**Verdict:** **BLOCK** — one Critical finding (C1: gold sample event ID shipped in `README.md`, outside `tests/` and `docs/`), plus two Important findings (I1, I2).

---

## Findings

| ID | Severity | Location | Finding |
|----|----------|----------|---------|
| C1 | **Critical** | `README.md:53` | Literal gold sample event ID `event_1815` in a shipped file. `README.md` is in `code.zip` via `tools/build_release.py:22` (`ROOT_FILES`), i.e. outside `tests/` and `docs/` → Critical per `task-10-brief.md:19`. |
| I1 | **Important** | `code/changes.py:131-138`, `code/solver.py:421-439`, `code/oracle.py:335` | `reduce_to` over-credits: suppression zeroes the **entire category** (not just the reduced target), so `safe_amount` can rise above the honest post-change forecast — and above what stopping the recurrence justifies. Item-5 answer = **YES, demonstrable**; all 4 committed change rows are decision-critical (`R1 < requested ≤ R2`); an honest-ledger floor breach is possible (E1: −600 vs floor). Gold semantics for category-level reduce **could NOT be verified** (71 known sample exact-match diffs, `tests/open_issues.md:8`). |
| I2 | **Important** | commit `4faecd3` (bundled) | Item-4 answer = **NO**, whole-ledger byte-identity vs pre-change fails: **271/275** requests differ. All diffs attributed to the ratified T1-b widening bundled in the same commit (suppression constructs themselves are provably inert under `None`). Brief's own carve-out: `task-1-brief.md:213/:220` ("byte-identical for protected/income"). |
| M1 | Minor | `code/oracle.py:335` (+ `tools/tui.py:301/336/669`) | No protected/income guard inside the oracle: the `suppressed_categories` param will suppress **any** category it is handed. End-to-end protection exists only at `code/changes.py:96`; TUI helper is unguarded (session-only, no disk writes). Defense-in-depth gap. |
| M2 | Minor (known/deferred) | `code/oracle.py:335` | Raw (possibly capitalized) event `category` compared against a **lowercased** suppressed set → latent case-mismatch no-op. Dataset verified all-lowercase (22 distinct categories, 0 violations) → unreachable on data; failure direction is conservative (no suppression ⇒ smaller safe). |
| M3 | Minor | `code/main.py` `_money` / `_money_plan` | Unreachable edge cases only: `_money("-0.005")` → `"-0"` (negative-zero string); non-numeric strings (`NaN`, `abc`) pass through; `_money_plan` sum can be inexact if `requested_amount` has >2 dp (304.805 → leg2 0.01 low). Dataset precision verified ≤2 dp everywhere (requests, samples, options, outputs) → unreachable. |

---

## Item 1 — Can `suppressed_categories` weaken the minimum-balance floor?

**Answer: No (mechanism intact); caveat belongs to I1 — the floor holds on the ledger the solver actually computes, but that ledger can be the wrong one.**

- 8/8 grid cases (base + suppressed × 4 requested amounts, floor 1000): `min(ledger) − safe_amount ≥ floor` → **True for all**.
- Committed `output.csv` change rows, R2 pipeline (floor from `minimum_balance_to_keep`):

```text
request_78: floor=55200  margin=56075.80 >= floor  True   (safe=50200, requested=50200)
request_99: floor=49100  margin=50595.42 >= floor  True   (safe=18062, requested=18062)
request_117: floor=1000  margin=1017.10  >= floor  True   (safe=880,   requested=880)
request_135: floor=500   margin=512.02   >= floor  True   (safe=304.8, requested=304.8)
```

- Formula (unmodified by this change): `safe_amount = max(0, min(req, min(daily) − min_bal))`, `code/oracle.py:443-444`.
- **Important caveat:** this checks the *suppressed* view of the ledger. The honest post-change view can breach the floor — see I1/E1 (honest min after payment = 400 < floor 1000, breach 600).

## Item 2 — Can it suppress a protected or income category?

**Answer: End-to-end No (blocked at `changes.py:96`); at the oracle level Yes-if-handed (M1).**

- Income: `suppressed_categories={"salary"}` → ledger **identical to baseline** (safe 500 vs 500). Income forecast loop (`code/oracle.py:~378-422`) contains no suppression check; income is never suppressible through this param. ✓
- Protected (oracle called directly with `amt=3000`, rent protected, rent in `stop_ok`):

```text
base:            safe=2700  min=3700
suppressed-rent: safe=3000  min=9700   (delta = 300)
```

  The oracle has **no internal guard** — it did exactly what it was told (M1).
- Pipeline: `changes.get_candidates` with rent protected and rent rows stoppable returned only `['stop:s0']` — rent's 2000 savings would have won the cheapest-solution ranking if not filtered at `code/changes.py:96` (protected filter before `stop_ok` at `:98`, exclusivity `:108-110`). ✓

## Item 3 — 91-point ledger preserved?

**Answer: Yes.**

- All probes: `len(ledger) == 91 == len(baseline_ledger)` (grid 8/8, scenarios A–D, dataset sweep 275/275, fail-closed returns).
- Fail-closed flat return (`code/oracle.py:426-431`) returns `[start_bal]*91` with `safe=0`, `earliest=None` — preserved identically on both sides (verified live on all 4 identity cases below).
- Ledger never has holes/shifts: suppression only changes values (item 6).

## Item 4 — Is `suppressed_categories=None` behavior byte-identical to before?

**Answer: NO for whole ledgers (271/275 differ); YES for the suppression logic itself and for protected/income (brief's definition).**

Baseline: no git in this checkout. Pre-change `oracle.py` reconstructed by reverse-applying the recorded diff (`.superpowers/sdd/2026-10-08-the-month-end-finalize-and-publish/task-1-review-package.md`, commit `4faecd3`, 4/4 hunks verified byte-exact at recorded line positions, `norm()` tolerance used for one mojibake comment line `90ΓÇæday`↔`90‑day`; no evidence of drift in hunk spans).

```text
Scenario A (protected expense + income, None):  byte-identical=True  (safe 500, min 10000 both)
Scenario D (None omitted vs explicit None):     byte-identical=True
Scenario B (flexible subscription):             DIFF  new min=9700 vs old min=10000
Scenario C (protected SUBSCRIPTION):            DIFF  new=9700 vs old=10000  (_history_eligible widened to subscription)
Dataset sweep: total=275 identical=4 different=271
identical = request_16, request_20, request_64, request_73 — all four are fail-closed
  flat-ledger cases (safe=0, 91 identical points) where forecast logic never differentiates:
  request_16: identical=True safe=0 flat_ledger=True  min=362370   (trigger: event_1442 amount='' → oracle.py:298-307 → FX_UNKNOWN → :426)
  request_20: identical=True safe=0 flat_ledger=True  min=102609.05
  request_64: identical=True safe=0 flat_ledger=True  min=98573.01
  request_73: identical=True safe=0 flat_ledger=True  min=278542.5
```

- **Attribution:** every one of the 271 differing requests belongs to a user whose *widened* forecast set (recurring expense/subscription categories outside the old protected∩expense rule) is non-empty — e.g. user_26 gains `dining/transport/utilities/streaming/cloud_storage`. Diff ⇔ widening; suppression constructs are inert under `None` (scenarios A + D; empty suppressed set ⇒ `cat in set()` is always False).
- **The bundled widening is explicitly ratified:** `task-1-brief.md:15` (T1-b ruling), `:153`, `:213` ("widening the forecast set (ruling T1-b) intentionally changes `safe_amount` for any user with unprotected recurring categories"), `:220` ("`suppressed_categories=None` must remain byte-identical for protected/income forecasting"); `task-1-report.md:14`.
- Also changes `subscription` handling: old `_history_eligible` accepted only `expense` for forecasting; new accepts `subscription` too (scenario C) — part of T1-b's ratified scope, but worth flagging as the largest behavioral delta.

## Item 5 — Can a suppressed category *increase* safe beyond what stopping justifies?

**Answer: YES — demonstrated end-to-end (I1).**

**E1, full pipeline, synthetic fixed sibling** (fixed electric 200 + stoppable 50 in `utilities`, floor 1000, requested 9000):

```text
search_changes -> ['stop:stop_2025-09-01']        (whole-category suppression + copy-edit)
R0 baseline:  safe=8625  min=9625   -> not_affordable
R2 pipeline:  safe=9000  min=10000  -> affordable          (suppression removed the fixed 200 too)
Rh honest:    safe=8400  min=9400   -> not_affordable      (stop only, fixed sibling still forecast)
over-credit vs honest = +600
post-payment honest min = 400 < floor 1000  -> FLOOR BREACH by 600 (suppressed view: 1000, OK)
```

The suppressed view never breaches the floor (item 1 passes) — but it is not the ledger a truthful post-change forecast produces. Spec §6.3: "The balance must never fall below `minimum_balance_to_keep` after any projected essential expense or payment in the recommended plan."

**Decision-critical on real, committed rows** — recomputed just now with the committed actions (`stop`/`reduce_to` copy-edit per `changes.py:116-123`, then oracle with/without `suppressed_categories`); `R1 < requested ≤ R2` on all four ⇒ full-payment affordability only exists because of suppression:

```text
request_78  acts=stop:event_7192 + reduce_to:event_7201:1480  cats={streaming,shopping}
  committed=45888.78  R0(raw oracle)=45955.2196063492063492063492  R1=46748.4776063492063492063492  R2=50200  requested=50200  CRITICAL
  (committed baseline < raw R0 by 66.44 = solver evidence/salary injection; flip claim unaffected)
request_99  acts=reduce_to:event_9226:1065.9  cats={dining}
  committed=R0=17445.42  R1=17570.82949572649572649572650  R2=18062  requested=18062  suppression contribution +491.17  CRITICAL
request_117 acts=reduce_to:event_10878:20.5   cats={dining}
  committed=R0=859.10    R1=861.076487179487179487179486   R2=880    requested=880    suppression contribution  +18.92  CRITICAL
request_135 acts=reduce_to:event_12447:17.5   cats={dining}
  committed=R0=283.18    R1=285.2529829059829059829059828   R2=304.8  requested=304.8  suppression contribution  +19.55  CRITICAL
```

All four are committed as `affordable_with_plan` + `full_payment` with full amount on request date; without suppression (`R1 < requested`) that full-payment recommendation could not stand.

Production-faithful spy on `request_135` (patching `solver.solve_safe_amount`/`changes.solve_safe_amount` during `solve_request`):

```text
baseline 283.1891367521367521367521367  == committed output ✓
R1=285.2529829059829059829059828  R2=304.8  suppressed={'dining'}  suppression_critical=True
```

**Live reachability of the fixed-sibling shape** (candidate + non-candidate siblings in same profile-permitted category with recurrence): **19 instances** — user_35 `dining`; user_84/86/102/126 `shopping`. request_78's rows: `stop:event_7192` (streaming, 5 members, uniform 1-description) + `reduce_to:event_7201:1480` (shopping, 5 members, uniform); request_99/117/135 all reduce within `dining` categories carrying 13 members / 12 non-target merchants.

**Why the risk is accepted-but-unverifiable:** gold's semantics for `reduce_to` (whole-category zero vs reduced-avg continuation) **could NOT be verified** — the three gold samples with change rows diverge from baseline from the start (safe 620.4 vs gold 603.3; 13110000 vs 12510645; 1574.4 vs 1543.35; solver returns `affordable_now/changes=none` for all three) — known state: `tests/open_issues.md:8` "tools/verify_samples → 71 sample exact-match diffs (validity gates pass)" (sample exact-match is not a gate). Gold `request_11` does `reduce_to:event_989:665950` on a 9-member/6-distinct-description dining group, so gold does not *obviously* require single-target reduce; no blank amounts / linked images / messages for users 06/11/21 (evidence layer no-op) — inputs are what they are.

**No honest re-check exists:** `solver.py:421-439` always passes `suppressed_cats`; nothing recomputes the floor with suppression off after changes are chosen.

## Item 6 — Does suppression alter settled history or the schedule?

**Answer: No — values only, never schedule or history.**

Same-inputs identity (item 6 as "same flags/inputs ⇒ same result as baseline under `None`"): scenario D (`None` omitted vs explicit `None`) byte-identical; fail-closed returns identical across old/new on all 4 identity cases (item 4).

Delta analysis (suppressed vs base, streaming example):

```text
len: 91 == 91;   day0: 10000 == 10000
delta-diff indices: [3, 33, 63]  = 2026-02-04, 2026-03-06, 2026-04-05  (exact streaming forecast dates)
settled in-window groceries idx 9: delta -80 in BOTH (history row untouched)
negative-delta days: base=[3,9,29,33,59,63,89]  supp=[9,29,59,89]
  -> the 3 forecast dates simply become 0; no day added, removed, or shifted.
```

Suppression only sets forecast contributions to zero (`code/oracle.py:335`); forecast-date computation (recurring set → frequency → roll-forward) is computed before suppression and identical on both paths.

## Item 7 — `_money` / `_money_plan` (rounding, partial payment)

**Answer: Correct within reachable inputs; edge cases unreachable (M3).**

```text
_money: '0.009'->'0'  '1.239'->'1.23'  '7.999999'->'7.99'  '1e3'->'1000'
         '-0.005'->'-0' (negative-zero; input direction unreachable)  'NaN'/'Infinity'/'abc'/'' pass through (unreachable)
2000 random in-range cases: 0 violations (0 <= out <= in, <= 2 decimal places)
_money_plan partial: 2000 random cases, 0 sum violations (leg1 + leg2 == requested exactly)
  >2dp requested gives inexact sum (304.805 -> leg2 0.01 low) -- unreachable: dataset max dp = 2
dataset precision: requests.csv / sample_requests.csv requested_amount max dp=2;
  request_payment_options.csv amounts max dp=2; output.csv safe_amount max dp=2
committed partial_payment rows (8): all exact sum; leg1 == amount_safe_to_pay;
  leg2 date == earliest_date_for_full_payment  (request_56, 138, 168, 210, 214, +3)
output.csv bounds: 250 rows, 0 <= safe <= requested, 0 violations
```

---

## Gold-reproduction question (task-10-brief.md:19)

**Q: Does the new spending-change path risk special-casing the public sample IDs (`event_476`, `event_989`, `event_1815`, `event_1816`)?**

**A: The code path does not special-case them — but one literal occurrence ships outside `tests/`/`docs/`: `README.md:53` → Critical (C1).**

Shipped set = `ROOT_FILES` (`README.md`, `problem_statement.md`) + `TREE_DIRS` (`code`, `evaluation`) per `tools/build_release.py:22-23`.

```text
scan: event_476|event_989|event_1815|event_1816  over README.md, problem_statement.md, code/**, evaluation/**, output.csv
README.md:53: | `code/amounts.py` | Locale-aware amount parsing and currency-coded figure
  extraction from free text (keeps digits in IDs such as `event_1815` out of money figures); ...
evaluation/** : (no matches)
code/** incl code/README.md : (no matches)
output.csv : (no matches)
scan: request_(01..25)\b over README.md, problem_statement.md, evaluation/**, code/** : (no matches)
code/*.py : (no matches)
```

- `code/amounts.py` itself does **not** contain the ID (it is prose in README's module table quoting an example).
- The project's own gate misses it: `tools/correctness_report.py:415-434` `check_no_sample_event_ids` scans only `output.csv` + `code/`.
- Spec-violating per `task-10-brief.md:19` ("Any literal occurrence outside `tests/` and `docs/` is a **Critical** finding") → per `:23` needs a RED test + minimal fix (remove/redact the example) → re-run this reviewer; per `:31` do not proceed with open Criticals.

---

## Test evidence

```text
> .venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_oracle.py tests\test_oracle_timing.py tests\test_oracle_fx.py tests\test_oracle_cycle1_safety.py tests\test_oracle_changes.py tests\test_oracle_cashstates.py tests\test_fx.py tests\test_recurrence.py tests\test_reconcile.py tests\test_reconcile_hierarchy.py tests\test_amounts.py tests\test_ledger.py
103 passed in 2.16s
```

## Method & limitations (could NOT verify)

1. **Pre-change baseline** reconstructed by reverse-applying the recorded diff (no git in checkout). All 4 hunks verified exact at recorded positions (one comment line normalized for mojibake); regions *between* hunks are assumed unchanged since commit `4faecd3` — supported by scenario-A/D byte-identity and per-user diff attribution, not exhaustively proven.
2. **Gold semantics for `reduce_to`** (whole-category zero vs reduced-avg continuation): could NOT verify — samples themselves diverge (71 known diffs, `tests/open_issues.md:8`); treated as unknown in I1.
3. `tools/verify_samples.py` run directly fails: `ModuleNotFoundError: No module named 'code.solver'; 'code' is not a package` (needs `-m`/sys.path invocation) — not required for this review.
4. Full-repo re-scan after C1 fix should re-run both gold-ID patterns above over the rebuilt `code.zip` file list.

## Verdict

**BLOCK.** C1 (Critical, gold ID in shipped `README.md:53`) must be fixed and this review re-run before approval (`task-10-brief.md:23/:31`). I1 and I2 should be dispositioned explicitly in `verdict.md` (I1: decide whether whole-category suppression for `reduce_to` matches gold intent or needs an honest-floor re-check; I2: accept as ratified T1-b debt with the brief's carve-out text cited).
