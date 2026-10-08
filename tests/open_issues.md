# Open Issues — Code Review + Test Suite Critique (6 independent agents, read-only)

Date: 2026-09-13. Scope: uncommitted working tree on top of `e695654`. No fixes applied (search task only).
Reviewers: R1 oracle slice, R2 solver pipeline slice, R3 submission readiness (web-grounded); Critics: T1 oracle tests, T2 solver tests, T3 e2e/submission tests.

## FIX CYCLE OUTCOME (2026-09-13, SDD fix→review→fix, TDD RED→GREEN)
- 3 implementers (Tasks A/B/C, disjoint scopes) → 3 task reviews (A NEEDS-FIX, B NEEDS-FIX, C APPROVED) → fix round 1 (A-fix + B-fix) → scoped re-review: ALL ADDRESSED, no new breakage.
- Fresh evidence: `pytest tests/` → **170 passed, 2 skipped, 1 xfailed** (xfail = oracle unknown-status allowlist, owned elsewhere). `tools/verify_samples` → 71 sample exact-match diffs (validity gates pass).
- Items below marked [FIXED], [XFAIL], or [OPEN] accordingly. Controller rulings: (1) shared-tree attribution findings are parallel-execution artifacts — gate is suite-green; (2) ranking.py keeps completion-first survivor order, solver keeps inline boolean-deadline spec_key, unused import removed; (3) options duration gate untouched (gold-anchored, needs spec ruling).

---

## R1 — Oracle / ledger slice (code/oracle.py, recurrence.py, fx.py, amounts.py, reconcile.py)

### Strengths
- 91-point ledger math matches plan_a.md §B + 90-day safety check; safe/earliest predicates correct shape.
- FX fail-closed preserved (exact wins, prior flagged FX_UNKNOWN, missing → None).
- Status filtering spec-aligned for known matrix; NaN/blank hardening in recurrence + oracle; CV>=3 gate intact; sorted() determinism; same-date multi-category dedup correct.

### Critical
1. **No duplicate / linked_event_id dedup — double-counts cash flow** — `code/oracle.py:87-129`. Every in-window row posts full delta; no event_id dedup, no linked_event_id collapse. Overstates safety; violates "ignore duplicate records". Fix: reconcile/dedup before window loop.
2. **Unknown/blank status falls through to COUNT** — `code/oracle.py:89-97`. Only failed/cancelled/unrealized + pending+credit excluded; `status=""`/`"weird"` counted. Fix: allowlist settled/scheduled/pending (pending+credit → skip).
3. **FX_UNKNOWN early return breaks 91-point ledger contract** — `code/oracle.py:220-225` returns `"ledger": []`. Plan promises 91 balances; downstream ranker/harness may crash. Fix: return computed balances (or 91-length placeholder) with safe=0.
4. **Mean interval is arrears-outlier-fragile (unsafe)** — `code/oracle.py:138-139,183-185`. One 60-90d gap shifts all forecasts late → under-reserves. Fix: median/trimmed-mode + require ≥2 pre-request dates.
5. **Forecast amount average polluted (unsafe)** — `code/oracle.py:146-160,190-200`. Protected-expense path mixes income+expense, includes failed/cancelled/pending; float round-trip. Fix: filter settled/scheduled (+pending+debit for expenses), stay in Decimal.

### Important
- Protected split doesn't strip whitespace — `code/oracle.py:49` (`" rent"` mismatch disables protection).
- Direct key indexing `e["user_id"]/e["currency"]/e["direction"]` can KeyError whole 250-run — `code/oracle.py:53,107,123`.
- `_amount` catches ValueError only — `code/recurrence.py:57-61`; TypeError crashes detect_recurrence.
- Single-history fallback `avg_interval=30` invents cadence — `code/oracle.py:142-143,188-189`; require ≥2 pre-request dates or skip.
- Pending-debit reserve timing posts on future settlement date, not held from req_date — `code/oracle.py:88-106`.
- Salary-raise messages parsed then discarded (`pass`) — `code/solver.py:39-44`; forecast uses stale average.
- Blank amount → whole-window FX_UNKNOWN → safe=0; spec wants image/OCR resolution (`problem_statement:45`); OCR path unwired.
- Unreachable duplicate ranking blocks after return — `code/solver.py:231-259`; delete.
- Perf: per-request detect_recurrence + per-event FX re-sort + `import math` in loops (`code/oracle.py:150,194`).

### Minor
- Unused import `detect_recurrence` (`code/oracle.py:14`); stale module docstring (earliest predicate); NaN dead code (`code/oracle.py:231-236`); silent 0 fallbacks (`code/oracle.py:28-41`); `< req_date` vs `<=` 1-day bias (`code/oracle.py:79`).

### Assessment
**Not ready to merge — fix Criticals first** (duplicate counting, unknown-status, mean-interval, polluted averaging, ledger-length).

---

## R2 — Solver pipeline slice (solver.py, options.py, changes.py, ranking.py, parser.py, main.py)

### Strengths
- Inline spec_key order (deadline, changes, total, start, legs, id) matches spec; full/partial eligibility + two-leg math correct shape; spending filter enforces protected/permitted/window/≤3; total_payable separated; LF output; dual entrypoint.

### Critical
1. **Dead code after early return** — `code/solver.py:231-259`. Two unreachable `rank_options()` blocks; `ranking` import never executes; authoritative path ambiguous. Fix: delete, single ranking implementation.
2. **ranking.py order wrong vs spec** — `code/ranking.py:11-25` is (total, completion, changes, legs, start, id); spec is (deadline, no-changes, total, start, fewer-legs, option_id). If dead code ever enabled, cheapest-late beats on-time. Fix: reorder key.
3. **Installments added without oracle safety check** — `code/solver.py:118-140`. Only deadline tested; per-leg cash vs min-balance floor never simulated. Can recommend overdrawing plans. Fix: simulate legs against ledger.
4. **earliest corrupted for installments** — `code/solver.py:124-135,219-228`. Returns installment start as `earliest_date_for_full_payment` (e.g. req_02 earliest 2025-09-15 vs start 2025-08-08). Fix: always propagate oracle earliest (except affordable_now).
5. **Spending fallback returns wrong earliest** — `code/solver.py:182-192` returns req_date; spec says earliest is without optional changes (gold req_06 plan 01-03 vs earliest 01-15). Fix: return original earliest.
6. **wait gating incomplete** — `code/solver.py:142-160` omits safe<req and earliest>req_date; can emit affordable_later with earliest==req_date alongside full. Fix: require strictly-future earliest + safe<req + will + deadline.

### Important
- Partial fail-open on missing flag (`code/solver.py:54,92-98`): `None` defaults to eligible; gate is fail-closed. Default False.
- max_installment_months check diverges (`code/options.py:59-71`): `n*freq//30` vs gate `(n-1)*freq/30.44` AND; 8x1-day and 2x90-day mishandled.
- search re-check uses `set()` flags (`code/changes.py:109-112`) not real recurrence flags.
- stop+reduce on same event not forbidden (`code/changes.py:93-106`); violates :198.
- changes.py missing status/direction filter (failed/cancelled/non_cash waste ≤3 budget).
- meets_deadline string compare mishandles NaN/"" (`code/solver.py:61-67`); parse dates.
- Spending fallback only when zero viable (misses deadline-missed viable); `code/solver.py:164-204`.
- parser.py bypasses amounts.py; salary path dead (`code/parser.py:35-54`, `code/solver.py:39-44`).
- main.py fragile I/O: CWD-relative paths, iloc[0] IndexError, no per-request isolation (`code/main.py:22-34`).

### Minor
- solver Decimal(str(req)) unguarded; options fee/total divergence assumption; savings zip order fragility; DataFrame column order implicit; missing EOF newlines; Decimal formatting stability.

### Assessment
**Not ready — with fixes.** Installment safety unchecked + earliest propagation wrong + wait/ranking violations guarantee wrong method/earliest on gold req_02/06/12.

---

## R3 — Submission readiness (web-grounded checklist)

### Strengths
- output.csv core passes: 250 rows + header, LF, UTF-8 no BOM, exact column order, ID coverage exact, bounds clean, 71/71 installments match, 5/5 partial valid, entrypoint real, transcript ignored/untracked correctly, no absolute paths/secrets.

### Critical
1. **No code.zip — submission blocked** (repo root Test-Path False). Required by problem_statement 227-234, README 179-185, AGENTS §6.5.
2. **evaluation/usage_report.md missing at required path**; `tools/evaluation/usage_report.md` 0 bytes. Required fields: providers/names, calls, in/out tokens, total+avg per-request, cost total+per-request, per-model + overall. If zero LLM calls, still needs explicit zeros table.
3. **Blank-amount image path unimplemented** — main.py never loads images.csv/media; oracle maps blank→FX_UNKNOWN→0. Spec :45 mandates image resolution, never zero.
4. **Generic decision_explanation (250/250 template)** — `Recommended X based on ranking.` Risks scoring cap (~70) on usefulness/consistency axis.
5. **Dead ranking blocks** (solver.py:219-259) + ranking.py order divergence — see R2.
6. **spending_changes always none (250/250)** — plan-C never exercised; 60 not_affordable may hide fixable cases.

### Important
- amounts.py:5 comment trips hardcoded-label gate (`event_1815`).
- main.py not --help-safe / CWD-fragile.
- Evidence pipeline partial (salary pass, narrow regex).
- FX fail-closed may over-trigger not_affordable (prior flagged UNKNOWN).
- log.txt tail malformed (line 1128 literal \n; 1120 missing label).
- __pycache__/tools will pollute naive zip.

### Grounded checklist (condition → status)
| Condition | Status |
|---|---|
| output.csv 250 rows + header | PASS (251 LF lines) |
| Columns exact order | PASS |
| ID coverage, no dup, order | PASS |
| 0<=safe<=req | PASS |
| affordable_now ⇒ earliest==req_date | PASS (73/73) |
| partial 2-leg + deadline + eligibility | PASS (5/5) |
| installment exact match | PASS (71/71) |
| spending format / flexible-only | PASS format / RISK coverage (250 none) |
| chronological plans | PASS |
| runnable code/main.py | PASS with caveats |
| code.zip | FAIL (absent) |
| evaluation/usage_report.md populated | FAIL (wrong path + empty) |
| chat transcript | PASS with minor defect |
| no hardcoded labels/organizer files | FAIL (1 hit amounts.py:5) |
| no absolute paths/secrets | PASS |
| deterministic | PASS (sorted, no random) |
| blank via image not zero | FAIL |
| grounded explanations | FAIL (generic) |

### Assessment
**Not ready to submit — with fixes.** Six criticals above; rebuild output.csv + code.zip after.

---

## T1 — Oracle-side test suite critique

### Strengths
- Zero mocks; real FX asserts (exact/prior/missing/pre/post); dual safe+earliest on happy paths; recurrence threshold real; ledger classifier encodes cash states correctly. 36 passed in scope; full 124 passed.

### Critical
- `test_safer_interpretation_fallback` is `pass`-only (test_reconcile_hierarchy.py:28-32) — vacuous green.
- `test_blank_related_event_leaves_unchanged` never tests blank (sends non-blank event_2) — false documentation (test_reconcile.py:25-31).
- No oracle+solver 90-day integration test (forecast + cash-state + earliest interaction uncovered).
- Failing-first claim unverifiable (no red logs/artifacts).

### Important
- timing test asserts safe==0 only, no earliest (test_oracle_timing.py:32).
- FX-prior fail-closed untested at oracle level (only missing-pair covered).
- Cash-state branches (pending debit vs credit, failed, NaN/blank) untested at solve_safe_amount level.
- Same-date multi-category forecast, arrears-outlier skew, recurrence 2-tuple asserts (always-true), intra-day credit ordering, determinism rerun, salary-raise/weekly-groceries — all untested.
- `parse_amount("nan")==Decimal('NaN')` leak (amounts.py:61-64) uncaught.

### Missing tests
14 concrete cases listed by agent (timing earliest None; prior-FX fail-closed; NaN/blank fail-closed; pending credit ignored vs debit reserved; same-date multi-cat; arrears outlier; salary raise + weekly groceries; recurrence NaN ignored + 3-tuple asserts; amounts blank/NaN; reconcile true-blank + settled-vs-safer; determinism 20x + PYTHONHASHSEED; 90-day mixed integration; same-day credit ordering).

### Assessment
**Not adequate alone — adequate with additions.**

---

## T2 — Solver-side test suite critique

### Strengths
- spec_checks + invariants are real validators (partial/installment/eligibility/spending); negative controls + eligibility matrix + gold anchors (req_02 fee trap, req_12 earliest independence, exclusivity).

### Critical
- **Ranking suite locks wrong order** (total-first, legs-before-start); no tradeoff test (cheap-late vs expensive-ontime) can fail — green proves the bug.
- **solver inline spec_key vs ranking.py diverge, untested**; two unreachable rank_options blocks; zero tests import solve_request/get_valid_options; tests_readme claims false coverage.
- **test_options*/test_solver* do not exist** — options + solver have zero direct tests.

### Important
- Eligibility wiring untested (allows_partial None default, meets_deadline NaN, wait future-only, (n-1)*freq vs n*freq).
- Partial/installment math verified on gold, never on solver formatting paths.
- Spending search barely covered (3 cases; no permission/window/≤3/exclusivity/combo tests); solver only tries changes when zero viable.
- Invariants gate skipped until output complete + incomplete (now-viable spending, later+changes, with_plan earliest, earliest>deadline, safe optimality, explanation grounding unchecked).

### Missing tests
11 concrete cases (ranking tradeoffs deadline>total, no-changes>total, start>legs; solver full/partial/wait; options max-months + totals + plan build; installment fee-trap total; changes permission/exclusivity/cap; solver+changes integration; solver+oracle integration; output contract None-vs-"").

### Assessment
**Not adequate.** Add tradeoff ranking, direct options/solver units, armed e2e before trusting green.

---

## T3 — E2E / submission test critique

### Strengths
- Deliverable-vs-template separation explicit; encoding gate matches writer; broad built-row contract; anti-gaming intent (AST proof, hello-world control, secret/banned/label/path scans); gold anchors.

### Critical
- **usage_report path wrong** (`code/evaluation/...` absent; real file under tools/) → gate permanently SKIP + secrets never scanned there.
- **Hardcoded-label regex misses real eval IDs** (`request_26…request_99` 2-digit don't match `request_(0\d\d|...)`); `if request_id=="request_27": return gold` passes scan.
- **Scan scope code/-only** — tools/ stash (verify_samples reads sample_requests; harness importable) bypasses.
- **Whole-file `_REF = re.compile` exemption** — one literal skips entire file.
- **Entrypoint test doesn't test documented entrypoint** — runs `code/main.py --help` (no argparse → full 250-row solve, slow/side-effectful); module form + non-root CWD never exercised.
- **No determinism rerun test** despite claims (5-template output, no rerun proof).
- **NaN-string/earliest hole** — `earliest="NaN"/"None"/"null"` on with_plan+installments passes; to_decimal NaN raises InvalidOperation (ERROR not FAIL).
- **Spending gate allows cross-user/one-time events** (global events_by_id, no user_id filter; recurring qualifier caller-supplied).

### Important
- Encoding gate partial (lone \r, trailing newline count, trailing spaces, blank records, quoting unguarded; no .gitattributes).
- Secret scan misses tools/, dataset/, root .env, AGENTS.md, .png/.zip; actual report unscanned.
- AST I/O proof passes on dead code (`if False:`); template explanations pass (5 strings/250 rows, min len 32); SKIP-masking fixture (one blank masks all); log hermeticity asserted not measured; wait-plan/deadline gaps documented-but-open.

### Missing tests
12 concrete cases (rerun determinism byte-identical machine cols; documented-command realism both forms + non-root cwd; --help no-solve; 2-digit ID + user_/GOLD/ground_truth patterns + tools/ scope + narrow _REF; correct usage_report path + negative control; NaN-string gate; earliest format gate all rows; explanation substance (length + <K% duplicates); spending ownership + recurring; CSV strictness; dead-code AST control; code.zip contents gate).

### Assessment
**Not adequate — with additions.** Shape/bounds/match gates real, but submission-breaking defects pass today.

---

## Consolidated submission blockers (dedup)
[FIXED] Dead code solver tails removed; unused rank_options import removed; ranking header corrected.
[FIXED] Hardcoded-label scan upgraded (2-digit IDs, user_, GOLD, tools/ scope); amounts.py _REF guard intact.
[FIXED] to_decimal/is_valid_earliest NaN gates; determinism pinned; main.py REPO_ROOT.
[OPEN] 1. code.zip absent; evaluation/usage_report.md absent at required path (empty stub elsewhere).
[OPEN] 2. Installment safety unchecked; earliest corrupted for installments + with-changes paths; wait gating incomplete — wait/partial fail-closed + strictly-future now fixed; installment simulation + earliest propagation still open.
[OPEN] 3. Duplicate/linked_event_id dedup missing (unsafe); unknown-status counted → [XFAIL] test pins it, oracle allowlist fix still open; mean-interval/amount outlier-fragile + polluted averaging open.
[OPEN] 4. Blank-amount image path unwired (fail-closed to 0); generic explanations; spending plan-C never exercised (250 none).

## Consolidated first-objective leftovers
- [OPEN] Reconcile/dedup integration into oracle window; unknown-status allowlist; median + Decimal filtered averaging with ≥2-date gate; 91-length ledger on fail-closed.
- [OPEN] Salary-raise + image-amount evidence wiring; pending-debit hold semantics documented/tested.
- [FIXED] amounts.py:5 label-scan hit (kept green via _REF guard + scan separation); EOF newlines; import hoisting; docstring refresh.
