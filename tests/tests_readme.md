# tests_readme — Buy-or-Wait test suite guide

## What this suite is
Spec-gated pytest suite for the HackerRank Orchestrate September 2026 Buy-or-Wait agent.
Authority: `problem_statement.md` + `AGENTS.md` (both read as binding; every test cites its rule).
It validates dataset integrity, output-contract compliance, ledger semantics, ranking logic,
evidence handling, gold-sample regression, and submission packaging — before AND after the solution exists.

## How it runs
```bash
# from the repo root, always with the project environment (global python is fallback only)
.venv\Scripts\python.exe -m pytest tests -q        # Windows
.venv/Scripts/python -m pytest tests -q             # same venv, forward slashes also work
```
- Green pre-build state: tests that need solution output **skip** (currently 7 skips); everything else passes.
- Post-build: skips re-arm automatically once root `output.csv` holds complete predictions,
  `code/main.py` is non-empty, and `code/evaluation/usage_report.md` is non-blank.
- Single-command gate for iterations: the same `pytest tests -q` line. Suite must be green
  before any full-250 run; each solution iteration must keep it green and add failing-first fixtures.

## How to use it during iterative builds
1. Build a solution increment (e.g. joins → ledger → rank → changes → validate).
2. Run the suite. Red = stop, fix, add a regression fixture for the bug found.
3. Score `sample_requests.csv` rows per-field against gold (see `test_gold.py`) before touching eval rows.
4. Run the full 250 only on green. `test_all_built_rows_pass_every_gate` then audits every predicted row.
5. Never tune the solution to gold IDs (`request_01–25` are style anchors, not labels — AGENTS 6.1).

## Relevant files
| File | Purpose |
|---|---|
| `tests/conftest.py` | Repo-root resolution (relative, cross-platform), cached dataset fixtures, `root_output_df` (returns None unless output.csv is a COMPLETE prediction file: exact ID-set + all machine cols non-blank) |
| `tests/spec_checks.py` | Pure validator oracles, reused by the solution later: plan/change parsers, CLDR-lite amount router, option expander, `check_bounds`, quad-consistency, fail-closed `check_partial`, byte-match `check_installment`, `check_spending`, `check_eligibility`, `cash_standing` classifier |
| `tests/test_dataset.py` | 9 files + 16 PNGs, 250 IDs ⊆ options, 39 message links / 0 orphans, 16 blanks ↔ 16 images, 58 linked events, 5 FX pairs + one documented off-cycle row (USD→INR 2025-10-01), locale regression pairs |
| `tests/test_invariants.py` | Bounds, quad consistency, req_19 partial-exact, installments byte-match (5 gold rows), not-affordable triple, req_12 earliest-independence, chronological plans, spending syntax, synthetic negatives, generic built-row gates, eligibility matrix on real profiles |
| `tests/test_ledger.py` | Status/flex counts, cash_standing matrix unit tests, tiny balance test (pending-credit excluded, pending-debit reserved), real fixture IDs (event_438/100/101/99/1785/476/989), message templates |
| `tests/test_ranking.py` | 6-rank order anchors (fee-vs-total on req_02 triple, deadline/changes/total/start/legs/option_id), real user_02 must-not-wait, installment expansion dates, off-by-one rejection |
| `tests/test_evidence.py` | 9 message templates verbatim, blank-related_event_id semantics, 16 PNG existence+size, ID/ES rows, req_26 flag-over-text rule |
| `tests/test_gold.py` | 25-row matrix coverage, single-partial req_19, wait single-leg pattern (gold-scoped, NOT a spec rule), fee trap, needs-a-change signature, nonzero-safe-but-not-affordable, explanation grounding |
| `tests/test_submission.py` | Root-vs-template output.csv, encoding (UTF-8/LF/no-BOM), full-surface secret scan, log untracked+ignored, usage_report structure, no-hardcoded-paths, entrypoint produces output |
| `tests/test_hallucination.py` | Role-bound grounding (chosen-option figures only, REF-strip incl. cue scan, month-name dates, cue parsing with tight adjacency), computational re-verification (partial sums, installment totals, reduce floors), citation scope checks, abstention discipline, widened injection detector + controls, checkpoint gate, fee-as-total negative controls |
| `tests/requirements-test.txt` | `pytest>=8`, `pandas>=2` (dev harness only, never shipped in code.zip) |
| `tests/__init__.py` | Package marker so imports resolve identically on any machine |

Current state: **76 passed, 10 skipped** (venv, `pytest tests -q`). Skips re-arm on complete predictions / built entrypoint / final usage report.
## Open issues (tracked, not hidden)
1. **U1–U6 organizer unknowns** (earliest-after-deadline semantics, FX missing-pair/month
   fallback, `reducible_or_stoppable` operator, 16 PNG contents, hidden scoring weights,
   max-months unit) are parameterized guesses in validators — flagged inline, need rulings
   or sample-25 calibration. U6 is fail-closed AND (both count and duration readings must
   pass); floor is a HARD gate following gold evidence (all gold reduces sit on floors).
2. **Ranker suicide gate** (fixed order ≥23/25) is advisory; threshold unjustified on n=25 — keep as signal,
   never auto-delete, never promotion-blocker.
3. **Wait plan shape** is gold-pattern-only (spec silent); enforced on gold, never on built rows.
4. **Grounding residuals (documented, accepted):** invented small counts/durations
   ('5 installments' instead of 3, 'in 45 days') pass — count nouns are not amounts;
   ambiguous single-dot+comma figures use last-separator rule; bare years stripped as dates.
5. **`reduce_to` floor** enforced as hard gate on gold evidence (all gold reduces sit on floors);
   a spec-literal-only scorer would accept sub-floor cuts — accepted residual, flagged in code.
6. **Subagent self-logging** (§5.3): explore agents lack write tools; controller logs their dispatches instead.
7. **Independent safe/earliest ledger oracle** (recompute from cash_standing + amendments + FX +
   90-day floor) is the known next gate — shape gates + gold anchors hold the line until the
   solution's differential oracle lands with the build.

## Limitations (what the suite can and cannot prove)
- Proves output **legality** (schema, gates, formats), never output **optimality** (cheapest-safe choice needs
  the differential oracle: solver optimum == independent enumerator optimum — build-phase work).
- Fixture counts (status/flex/message totals) are frozen 2026-09 pins: they catch dataset drift, not solver bugs.
- Ranking tests pin the ORDER SPEC on synthetic candidates; solver wiring (`rank_solutions` import) lands post-build.
- Skip gates re-arm on artifacts that could be gamed (single-row output guarded by ID-set check; hello-world main
  guarded by output-production check; keyword-stuffed usage_report guarded by numbers/totals/cost checks) — reviewed,
  accepted residual: CI must eyeball first green runs.
- No runtime cap by design (no official budget exists — a cap would invent a requirement).
- Windows-first paths verified; Linux CI assumed equivalent via pathlib (no Linux runner in this environment).

## Other info
- Reviews: 4 plan critics (34 findings) + 4 post-build critics (R1/R1b fidelity, R2/R2b adversarial)
  + 2 hallucination critics (H1/H2: escapes, bypasses, tautologies) + 2 final-cycle critics
  (grounding v2, gates v2 — floor re-promoted hard, U6 fail-closed AND, AST Call-proof,
  structured usage tables, status semantics, hardcoded-labels scan, built grounding)
  + REF-strip miss caught by verification audit and fixed with regression test; all Criticals fixed.
- Determinism scope: machine columns byte-identical + amount tolerance; LLM prose checked for content-invariance, never byte-equality.
- Environment: `.venv` (pytest 9.1.1, pandas 3.0.5); `python` global is fallback only.
- Logging: suite never touches `log.txt` (hermeticity); turns logged per AGENTS.md §5.
