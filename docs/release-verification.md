# Release verification — review remediation

Branch `release-curate`, range `b757162..HEAD`. Verified 2026-11-04.

## Test suites

| Command | Result |
|---|---|
| `pytest -q` | **717 passed** |
| `pytest -q tests/review` | **503 passed, 1 skipped** |

Baseline before this branch was 366 default tests; the branch adds 306.

## Evaluation gates (`python -m evaluation.run --mode pr`)

| Gate | Verdict |
|---|---|
| G0 structural | PASS 15/15 checks |
| G1 safety | PASS — 0 status upgrades, 0 safe overclaims, 0 unparsed |
| G2 sample_quality | **FAIL** — full rows 3/25; amount 3/25; status 17/25; method 17/25; plan 16/25; earliest 16/25; spending 22/25 |
| G3 determinism | PASS — 2 clean runs, machine columns byte-identical |
| G4 provenance | PASS - commit `0960e01`, output `0b0a1384de1c`, usage `20f449802e05` |
| G5 archive | NOT_APPLICABLE in PR mode |
| G6 security | NOT_APPLICABLE in PR mode (tree secret scan runs in G0) |
| G7 agent robustness | NOT_APPLICABLE in PR mode |

G2 remains the known external-contract failure documented in
`docs/g2-hypothesis-report.md`: the challenge reference arithmetic is not present
in this repository, and no gold-row fitting was performed. Reported, not
silenced, not re-baselined.

`output.csv` was regenerated once from the shipped kernel at the end of the
branch: `1F35043F…376486` → `0B0A1384DE1C612CCBACA5A6392A0CDB3BC754DECF48DCE8E1C6CF8DE15AD366`.
The old file predated the fail-closed oracle contract and the forecast change,
so it no longer described the code. G1 stayed 0/0/0 and G3 stayed byte-identical
after the regeneration. A machine without the local vision backend still cannot
reproduce the file byte-for-byte; see issue #13.

## Appendix A adverse inputs (code review)

| Input | Observed |
|---|---|
| active event `direction="mystery"` | `safe=0`, `fail_closed=True`, `DIRECTION_UNKNOWN` |
| `direction="debit"`, `amount="-9000"` | `safe=0`, `fail_closed=True`, `AMOUNT_INVALID` |
| `minimum_balance_to_keep="garbage"` | `safe=0`, `fail_closed=True`, `RESERVE_INVALID` |
| `minimum_balance_to_keep="NaN"` | `safe=0`, `fail_closed=True`, `RESERVE_INVALID` |
| active settled debit with no usable date | `safe=0`, `fail_closed=True`, `DATE_UNKNOWN` |

All five produce zero safe amount and a named reason; none raises.

## What shipped, by finding

- **CR-01…CR-04** — oracle fail-closed contract: unknown direction, signed
  amount, invalid reserve/balance, undated active rows, FX and accumulation
  overflow, negative reserve.
- **CR-05** — facts map to categories the forecast projects; income becomes
  `salary` only when recorded as monthly; the form asks category and recurrence
  and refuses a blank bill category.
- **CR-06** — FX persisted (schema v2 with migration) and passed to every kernel
  call; rates parsed strictly, non-positive or unparsable rates fail closed.
- **CR-07** — chat answers verified against a kernel result from the same turn;
  signed figure matching against the named field, so a balance cannot witness a
  safe-amount claim and a sign flip is rejected.
- **CR-08** — `max_tool_calls` bounds dispatched calls, not loop turns.
- **CR-09** — closed tool schemas, string money, local validation, structured
  `{code, retryable, safe_summary}` errors.
- **CR-10** — deadline passed to the client where honoured; otherwise recorded
  as unbounded with `cancellation_confirmed=False`.
- **CR-11** — search attempted / backend healthy / evidence found separated.
- **CR-12** — untrusted-data block, injection tests, unchanged tool scope.
- **CR-13** — partial-month exclusion shipped; the MTD and proration corrections
  measured G1 regressions and were **removed** (deferred), not flag-gated.
- **CR-14** — blank onboarding and `solver.position` stay unknown.
- **CR-15/16** — documented env keys, parsed loopback check, Kilo labelled free.
- **CR-17** — cancellable Textual worker, explicit account picker.
- **CR-18** — why-questions explain, negation never mutates, confidence typed.
- **CR-19** — bounded in-memory conversation per account, invalidated on switch.
- **CR-20** — docs aligned; a test fails on stale entry points.
- **Recommendations** — hosted-provider consent bound to provider+model, account
  validator on save, run summary, "what facts did you use?", provider double,
  scenario API and guards, typed run state, `trust_env=False` on provider clients,
  no silent account overwrite.

## Reviews

- **CodeRabbit (stored findings, 4)** — fixed: `6e59a5b` (trace userinfo),
  `9ac4384` (kernel-explanation fallback), `5b72f18` (count prefix), `ba358d0`
  (scoped denial).
- **CodeRabbit deep review on this range** — **NOT RUN**: the CLI reports an
  unauthenticated session (`coderabbit auth login` required). Reported, not
  claimed.
- **Independent correctness review** — 1 Critical, 4 Important found and fixed:
  `ffdb9b0` (signed figure verification), `38b51a8` (removed the env switch),
  `6c42193` (FX rate parsing), `e58bf02` (non-finite service amounts), `414412a`
  (negative reserve).
- **Independent security review** — 1 Critical, 3 Important found and fixed:
  `164ad64` (`trust_env=False`, proxy leak), `85465bb` (real transport deadline),
  `45af94a` (no silent account overwrite), `b71009b` (consent bound to the
  model it was asked for).

## Known limitations

1. The MTD and proration corrections are removed pending a safety-margin design
   that keeps G1 at 0/0/0.
2. Provider traffic ignores `HTTP(S)_PROXY` by design (`trust_env=False`);
   operators needing an egress proxy must configure it explicitly. Model-list
   probes still use `urllib` and honour proxy env, carrying no prompt or key.
3. `ChatOpenAI` may retry a timed request under its own policy; the recorded
   deadline is per attempt, and the outer join plus `cancellation_confirmed`
   remain the wall-clock truth.
4. G2 is FAIL by external contract; the target was not re-baselined.
5. Chat memory is in-memory only; durable memory, the full orchestrator state
   machine, LangGraph, process isolation and the module restructure remain
   Future Works.
