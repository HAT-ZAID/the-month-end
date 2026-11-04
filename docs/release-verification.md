# Release verification — review remediation

Branch `release-curate`, range `b757162..HEAD`. Verified 2026-11-04.

## Test suites

| Command | Result |
|---|---|
| `pytest -q` | **644 passed** |
| `pytest -q tests/review` | **499 passed, 1 skipped** |

Baseline before this branch was 366 default tests; the branch adds 278.

## Evaluation gates (`python -m evaluation.run --mode pr`)

| Gate | Verdict |
|---|---|
| G0 structural | PASS 15/15 checks |
| G1 safety | PASS — 0 status upgrades, 0 safe overclaims, 0 unparsed |
| G2 sample_quality | **FAIL** — full rows 3/25; amount 3/25; status 17/25; method 17/25; plan 16/25; earliest 16/25; spending 22/25 |
| G3 determinism | PASS — 2 clean runs, machine columns byte-identical |
| G4 provenance | PASS — commit `648e9b7d`, output `1f35043f00ad`, usage `20f449802e05` |
| G5 archive | NOT_APPLICABLE in PR mode |
| G6 security | NOT_APPLICABLE in PR mode (tree secret scan runs in G0) |
| G7 agent robustness | NOT_APPLICABLE in PR mode |

G2 remains the known external-contract failure documented in
`docs/g2-hypothesis-report.md`: the challenge reference arithmetic is not present
in this repository, and no gold-row fitting was performed. It is reported, not
silenced or re-baselined.

`output.csv` is byte-identical to the pre-branch file:
`1F35043F00ADDC8275D5879026FA83F6595A19712EE814AE89A194F353376486`.

## Appendix A adverse inputs (code review)

| Input | Observed |
|---|---|
| active event `direction="mystery"` | `safe=0`, `fail_closed=True`, `DIRECTION_UNKNOWN` |
| `direction="debit"`, `amount="-9000"` | `safe=0`, `fail_closed=True`, `AMOUNT_INVALID` |
| `minimum_balance_to_keep="garbage"` | `safe=0`, `fail_closed=True`, `RESERVE_INVALID` |
| `minimum_balance_to_keep="NaN"` | `safe=0`, `fail_closed=True`, `RESERVE_INVALID` |
| active settled debit with no usable date | `safe=0`, `fail_closed=True`, `DATE_UNKNOWN` |

All five produce zero safe amount and a named fail-closed reason; none raises.

## What shipped

Operations by finding:

- **CR-01…CR-04** — oracle fail-closed contract: unknown direction, signed amount,
  invalid reserve/balance, undated active rows, FX and accumulation overflow.
- **CR-05** — facts map to categories the forecast projects; income becomes
  `salary` only when recorded as monthly; the ledger form asks for category and
  recurrence and refuses a blank bill category.
- **CR-06** — FX persisted (schema v2 with migration) and passed to every kernel
  call.
- **CR-07** — chat answers are verified against a kernel result from the same
  turn; an unverified financial answer is refused.
- **CR-08** — `max_tool_calls` bounds dispatched calls, not loop turns.
- **CR-09** — closed tool schemas (`additionalProperties: false`, string money),
  local validation, structured `{code, retryable, safe_summary}` errors.
- **CR-10** — provider transport bounded by the turn deadline; cancellation
  recorded honestly (process isolation deferred).
- **CR-11** — search attempted / backend healthy / evidence found are separate.
- **CR-12** — untrusted-data block; injection tests; scope unchanged by text.
- **CR-13** — partial-month exclusion on by default; MTD and proration
  corrections behind `BUY_WAIT_FORECAST_CORRECTIONS` (default off) because
  enabling them measured 2 status upgrades and 15 safe overclaims.
- **CR-14** — blank onboarding and `solver.position` stay unknown.
- **CR-15/16** — provider registry reads documented env keys; loopback is parsed
  and checked by address; Kilo is labelled free.
- **CR-17** — chat runs in a cancellable Textual worker; explicit account picker.
- **CR-18** — why-questions explain; negated actions never mutate; confidence is
  type-checked.
- **CR-19** — bounded in-memory conversation per account, invalidated on switch.
- **CR-20** — README, architecture and handover aligned; a test fails on stale
  documented entry points.
- **Recommendations** — consent gate for hosted providers, account validator on
  every save, run summary, "what facts did you use?", scripted provider double,
  scenario API and guards, typed run state.

## Known limitations carried into release

1. `BUY_WAIT_FORECAST_CORRECTIONS` must stay off until a safety-margin design
   resolves its G1 conflict.
2. Provider calls are bounded by transport deadline, not killed; a timed-out call
   may finish in the background and is recorded as unconfirmed.
3. G2 is FAIL by external contract; the target was not re-baselined.
4. Chat memory is in-memory only; durable memory, the full orchestrator state
   machine, LangGraph and the module restructure remain Future Works.
