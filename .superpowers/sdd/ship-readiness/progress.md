# SDD ledger — plan: ship-readiness (code-review-fix to ship vote)

- BASE: will record per-wave via git rev-parse HEAD before each dispatch.
- Interface freeze: solve_safe_amount(user_id, request_date_str, requested_amount_str, profile, events, fx_rows, recurrence_flags) -> {safe_amount, earliest_date, ledger[91]} unchanged. W2 salary-raise via event injection only, no oracle signature change.


## Preflight conflict scan
- W1 files: code/oracle.py, code/recurrence.py, code/fx.py, code/amounts.py, code/reconcile.py + tests/test_oracle*.py, test_recurrence.py, test_reconcile*.py, test_ledger.py, test_amounts.py. W2 files: code/solver.py, code/options.py, code/changes.py, code/ranking.py, code/parser.py + tests/test_ranking.py, test_changes.py, test_options.py, test_solver.py. No shared files.
- Interface: solve_safe_amount signature frozen; W2 salary-raise via event injection only.
- spec_checks.py/conftest.py/test_submission.py/test_invariants.py frozen for both waves.
- W1-BASE: e695654 (plus uncommitted tree); rulings: ranking split + options-duration stands (prior).


## Wave BASE (fresh, 2026-09-13 build restart)
- HEAD e695654; tree carries prior test-cycle changes (see git status). Decision: dispatch W1 then W2 sequentially to avoid cross-cancellation; scopes disjoint per preflight.


## W1 complete
- Report: .superpowers/sdd/ship-readiness/w1-report.md. 179 passed, 2 skipped, 0 failed (8 new pins, xfail flipped to pass). Concerns noted: same-direction-only linked dedup; forecast ignores FX; integer-division median; unused pytest import in cashstates.


## W2 complete
- Report: .superpowers/sdd/ship-readiness/w2-report.md. 194 passed, 2 skipped, 0 failed (+15 new). Concerns: strict treats empty ledgers as safe, ignores legs beyond 90d; after-window raise skip needed (oracle averages future salaries); huge raises may break CV gate (oracle frozen); not_affordable earliest None per spec.


## Review round 1 outcome
- W1 review: NEEDS-FIX (5 actionable: FX forecast conversion, refund-pair pin, signed-average mixing, minors). Tree-attribution items ruled artifacts (prior-session tree state), gate is suite-green.
- W2 review: NEEDS-FIX (7 actionable: parser narrowing, fail-closed+90d horizon, pre-injection detect, caller pins, dead test block). Ranking-reorder/oracle-rewrite/evaluation items ruled pre-existing tree state, not W2 violations. Ranking split + options-duration rulings stand.

