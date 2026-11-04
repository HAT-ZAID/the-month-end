# G2 root-cause plan — exhaustive hypotheses, tests and build sequence

**Read this after any context reset, before touching the solver.**
It records everything established by the plan-mode investigation, everything ruled out (so it is
not re-run), and the full hypothesis and probe programme that has not been run yet.

Related: `docs/handover-state.md` (branch, commits, what is done outside G2),
`docs/general-use-plan.md` (the phase plan that produced the general-use build),
`docs/improvement-report.md` §7 (the author's earlier, shorter note on the same gap).

---

## 0. Purpose

G2 is the release gate that compares our decisions on the 25 labelled sample requests against the
organiser's answers. It fails: **3/25 whole rows**. The user's standing instruction is to pass G2
"at all costs", with the explicit caveat that label fitting is allowed only as a diagnostic, not
as the shipped result.

This document exists to make the next session productive from the first command instead of
re-deriving the evidence. It contains:

1. the gate definition and current numbers (§2),
2. a validated harness that reproduces the sample pipeline exactly, without a full run (§3),
3. the defects that are established and must be fixed regardless of the labels (§4),
4. a register of everything already ruled out, with the measurement that ruled it out (§5),
5. the per-row dataset and what the residuals imply (§6),
6. open hypotheses, each with a test that can confirm or kill it (§7),
7. a programme of model-driven probes, including genuinely agentic loops (§8),
8. the decision tree and the build sequence with acceptance criteria (§9, §10),
9. guardrails that must not be violated while fixing this (§11).

---

## 1. Environment and commands

- Repo: `D:\Hackerrank_Orchestrate\the-month-end-agentic`
- Branch: `release-curate`; `main` untouched; nothing pushed.
- Interpreter (all commands use this one):
  `D:\Hackerrank_Orchestrate\hackerrank-orchestrate-september26\.venv\Scripts\python.exe`
- Full suite baseline: `pytest -q` → **365 passed**.
- Review net: `pytest -q tests/review` → **499 passed, 1 skipped**.
- Deliverable: `output.csv` sha256
  `1F35043F00ADDC8275D5879026FA83F6595A19712EE814AE89A194F353376486`, must not change.
- PowerShell cannot run inline `python -c` with nested quotes or `|`; write a temp `.py` under
  `C:\Users\zaidk\AppData\Local\Temp\opencode\` or pipe a here-string into `python -`.
- Ollama server: `http://127.0.0.1:11434`.
- **Local models** (in `/api/tags`): `gemma4:e2b-it-qat` (default for bill reasoning,
  3/3 correct on the settled-vs-unpaid questions), `llama3.2:3b` (1/3),
  `qwen3-vl:2b-instruct-q4_K_M` (1/3), `qwen3-vl:2b`. No `e4b` is pulled.
- **Cloud model, primary reasoner (verified 2026-11-02): `gemma4:31b-cloud`** — 32.7 B params,
  BF16, capabilities `completion`, `thinking`, `tools`, `vision`. It chose the correct tool in
  3/3 selection tests (`position` for "how much can I spend", `list_events` for "what did I spend
  on groceries", `decide` for "decide request_07") at **7–19 eval tokens** per turn, with no prose
  before the call. Use it for the agentic probes A1–A7.
- **`gpt-oss:20b-cloud` is demoted.** It answers and does emit `tool_calls`, but its tool selection
  proved unreliable in use, so it is not the primary. Keep it only as a prose/fallback model, never
  as the tool chooser, and never as the authority on a number.
- **Cloud models do not appear in `/api/tags`.** `code/models.py` discovery therefore misses
  `gemma4:31b-cloud` and `gpt-oss:20b-cloud`; menu entries have to be added explicitly
  (build-mode step 7).
- **`.env` is not loaded by any code.** It contains exactly one key, `OLLAMA_API_KEY` (57 chars),
  and no module calls `load_dotenv`; the interactive probes read it explicitly. Either load it
  deliberately (and keep it out of traces) or export it before the process starts. Never print it.
- **Privacy:** a cloud model leaves the machine. Under `AGENTS.md` §2 it is *not* `local`; it may
  see development fixtures and non-sensitive text only, and the UI must label it as such.
- Local inference runs sequentially on this hardware. Never start two model jobs at once.

---

## 2. What G2 is, and the current numbers

Gate code: `evaluation/run.py`.

- `SAMPLE_THRESHOLDS` (line ~120): `full_row_matches: 20`, per field
  `amount_safe_to_pay: 22`, `affordability_status: 23`, `recommended_payment_method: 23`,
  `payment_plan: 22`, `earliest_date_for_full_payment: 22`; spending recall `2/3`, precision `0.5`.
- `sample_quality_gate(metrics)` (line 215) turns those into problems.
- Reference rows: `mock_dataset/sample_requests.csv` — 25 rows carrying the organiser's completed
  output columns beside the request columns.

Current production result (`python -m evaluation.run --mode pr`):

```
G0 PASS 15/15   G1 PASS   G2 FAIL   G3 PASS   G4 PASS   G5-G7 NOT_APPLICABLE
G2: full-row 3/25; amount_safe_to_pay 3, affordability_status 14,
    recommended_payment_method 14, payment_plan 12, earliest_date 11,
    spending_changes_needed 21; spending rows TP 0/FP 1/FN 3 of 3 changed
```

`PRE_FIX_BASELINE` in the same file records 3/25 measured at `dde3b48` before kernel work, so this
is not a regression introduced by the general-use branch. `docs/improvement-report.md` §7 states
plainly that the label style is not fully known.

---

## 3. Reproduction harness (validated)

**Key fact: the sample pipeline can be reproduced without running it.** Feeding
`mock_dataset/financial_events.csv` with five blank image-resolved amounts patched in, plus the
real FX rows and `detect_recurrence`, reproduces the recorded `solve_safe_amount` output on
**25/25 rows**. That makes every experiment a ~0.1 s oracle call instead of a pipeline run.

Blank image amounts to patch (from the recorded-vs-raw diff):

| event_id | resolved amount |
|---|---|
| `event_253` | `4365000` |
| `event_1442` | `100000` |
| `event_1545` | `41272.00` |
| `event_1700` | `2854.00` |
| `event_1786` | `704.05` |

Signatures used:

```python
solve_safe_amount(user_id, request_date_str, requested_amount_str, profile, events,
                  fx_rows, recurrence_flags,
                  suppressed_categories=None, *, category_overrides=None,
                  evidence_flags=None, expense_facts=None)
# -> {"safe_amount", "earliest_date", "ledger" (91 Decimals), "fail_closed",
#     "processed_events", "forecast", ...}
```

```python
build_forecast(events, req_date, *, user_id, home_currency, fx_rows, recurrence_flags,
               suppressed_categories, protected_categories, category_overrides,
               evidence_flags, expense_facts)
# -> ForecastResult(entries=[ForecastEntry(date, amount, category, kind, source, reason), ...])
```

Appendix A holds a complete, runnable harness skeleton. Lift it to `tools/g2_probe.py` in build
mode so it survives temp cleanup.

---

## 4. Established defects (label-independent, must be fixed)

These are wrong on their own merits. Fixing them does not make G2 pass by itself, but leaving them
is indefensible.

### D1. Month-to-date double count — fixed bills are charged twice

`code/forecast.py::_monthly_budget_forecast` posts every category's monthly budget on the bucket
start. The first bucket is the request date. `_real_in_window_expenses` only subtracts real rows
dated **on or after** the request date, so anything already settled earlier in the same month is
posted again as a prorated remainder.

Evidence (user_08, request 2025-02-07):

| event | date | amount | status |
|---|---|---|---|
| rent | 2025-02-01 | 467.50 | settled |
| utilities | 2025-02-05 | 80.55 | settled |
| groceries | 2025-02-04 | 72.38 | settled |
| transport | 2025-02-05 | 47.21 | settled |

The day-0 lump still includes `rent 358.42` (a prorated 467.50) and `utilities 61.23`. 20 of 25
sample requests have same-month spend before the request date; 6 include rent or housing. For
request_04 the rent already paid is 12,293,000 — about the size of the day-0 excess.

### D2. Proration off-by-one and a fixed denominator, hidden by a clamp

`code/forecast.py`:

```python
days_left = (_first_day_of_next_month(req_date) - req_date).days   # already inclusive
prorated = amount * Decimal(days_left + 1) / Decimal(PRORATION_DAYS_PER_MONTH)  # +1 double-counts
amount = min(amount, prorated)                                     # hides factors above 1
```

For 3 January 2026, `days_left` = 29, so the factor is `(29+1)/30 = 1.0`: the **entire** January
budget posts on day 0. The clamp exists only because the numerator can exceed the denominator; the
comment in the code admits this. Correct forms: `remaining / days_in_month` or `remaining / 30`
without the `+1`.

### D3. The partial request month is counted as a full budget month

`_monthly_budget_history` counts the request month's days before the request as one monthly total.
At the start of a month that total is tiny and drags the category median down; the same category's
in-month spend is then posted again (D1). Excluding the partial month scores better against the
labels (MAPE 0.488 vs 0.513), which suggests the reference excludes it.

### D4. The tool layer prints unrounded money, and `position` has no date

Gemma's replies over the tool loop included `12,112,828.65466…`, `47.353000000000000000000000 EUR`,
`234.30 EUR`. `service.position(account)` defaults to today, so an agent cannot replay a historical
request; the same account showed 906.475 today against 937.2318 on 2026-04-03.

### D5. FX gap, cause unverified

request_25 (user_25, IDR home) has USD events; `resolve_rate(fx_rows, "usd", "idr", "2024-03-06")`
returns `None`. Whether the rate table lacks that pair, or the direction is wrong, has not been
checked. If the oracle fails closed on the row, the answer is 0 for the wrong reason.

---

## 5. Ruled-out register — do not re-run these

Each line is a completed measurement. Re-running them wastes turns and will not change the result.

| # | Hypothesis | Test performed | Result |
|---|---|---|---|
| R1 | Income projection is capped at 2 occurrences and drops a payday | `MAX_INCOME_OCCURRENCES` 2, 3, 4 under the best structural config | **Identical values on all 25 rows.** Cap is not binding. |
| R2 | Forecast horizon should stop at `desired_completion_date` | Horizon = deadline offset, 16-config grid | Only request_01 changes (becomes a full match). Fixes 3→4/25; no informative row. |
| R3 | Evidence messages are being misapplied | Rerun with `evidence_flags`/`expense_facts` removed | No change on any informative row. |
| R4 | Image-resolved amounts are wrong | Raw events vs recorded events | Raw + five patched amounts reproduces recorded output 25/25. |
| R5 | A single category is wrongly included or excluded | Suppress each debit category in turn, plus all debits, non-fixed, subscriptions, discretionary | 0 of 21 informative rows matched by any suppression. |
| R6 | Recurring bills should post on their due day, not the bucket start | Due-day detector + posting, with and without MTD | request_06 unchanged at 515.88; 0 exact. |
| R7 | The labels round to cents or whole units | Round entries, balance, floor the safe amount, "integer world" | 0 exact; closer-counts move by noise (12–14 of 21). |
| R8 | The budget estimator is mean or last-three mean, not median | Estimator sweep under corrected proration | 0 exact; mean/mean3 no better than median. |
| R9 | The proration denominator is the cause | Denominator 30, 31, 45, 60, 90, 120, 180, 365, 1e9 | 0 exact at every value. |
| R10 | The proration gate on projected income is the cause | Always-prorate vs gated | Always is worse (MAPE 0.557 vs 0.488). Only 4 rows lack projected income; the zeros on user_08 and user_10 are not explained by the gate. |
| R11 | The oracle ledger is built differently from balance + deltas | Offline rebuild | **Harness bug, not a repository bug:** `processed_events` already contains the forecast entries (`code/oracle.py` 289–296). Rebuilding must not add `forecast` separately. |
| R12 | The labels are reachable by fitting the tested families | ~90 configurations (cap × horizon × MTD scope × due day × proration × estimator × partial month × rounding × suppression), held-out half | 0 of 21 informative exact; **0 of 10 on the held-out half**. |

The fit in R12 is the strongest evidence that this is not a parameter-tuning problem.

---

## 6. Per-row data and what the residuals say

`ours` = production output (`solve_safe_amount` with the current code).
`best` = in-memory structural variant: partial request month excluded, MTD subtracted only for
recurring bills, proration `remaining / days_in_month`.

| row | user | gold | ours | best | income projected | note |
|---|---|---|---|---|---|---|
| 01 | user_01 | 25,256.00 | 22,018.72 | 25,256.00 | no | wait/affordable today under `best` |
| 02 | user_02 | 17,229,139.20 | 12,112,828.65 | 16,357,614.96 | yes | −5% residual |
| 03 | user_03 | 873,000.00 | 808,840.41 | 886,004.53 | yes | +1.5% |
| 04 | user_04 | 8,401,800.00 | 0.00 | 7,721,879.28 | yes | −8% |
| 05 | user_05 | 737.00 | 0.00 | 4,310.89 | no | **5.8× too high** |
| 06 | user_06 | 603.30 | 515.88 | 556.31 | yes | −8% |
| 07 | user_07 | 87,170.56 | 69,750.79 | 101,472.08 | yes | +16% |
| 08 | user_08 | 284.57 | 0.00 | 0.00 | yes | zero in every variant |
| 09 | user_09 | 166.61 | 166.61 | 166.61 | yes | exact (clipped) |
| 10 | user_10 | 12,700.00 | 0.00 | 0.00 | no | zero in every variant |
| 11 | user_11 | 12,510,645.00 | 11,565,693.80 | 12,715,726.14 | yes | +1.6% |
| 12 | user_12 | 65,164.00 | 65,164.00 | 65,164.00 | no | exact (clipped) |
| 13 | user_13 | 433.40 | 47.35 | 678.82 | yes | +57% |
| 14 | user_14 | 597.74 | 114.93 | 843.00 | yes | +41% |
| 15 | user_15 | 83.05 | 0.00 | 12.37 | yes | −85% |
| 16 | user_16 | 122,500.00 | 122,500.00 | 122,500.00 | yes | exact (clipped) |
| 17 | user_17 | 243,849.58 | 244,941.16 | 244,941.17 | yes | +0.4% |
| 18 | user_18 | 462.00 | 337.09 | 522.08 | yes | +13% |
| 19 | user_19 | 28,820.00 | 22,915.86 | 25,670.60 | yes | −11% |
| 20 | user_20 | 5,400.00 | 0.00 | 7,635.73 | yes | +41% |
| 21 | user_21 | 1,543.35 | 937.23 | 1,574.40 | yes | clipped to the request |
| 22 | user_22 | 475.46 | 234.30 | 411.12 | yes | −14% |
| 23 | user_23 | 9,152.00 | 0.00 | 10,074.34 | yes | +10% |
| 24 | user_24 | 13,420.00 | 0.00 | 14,334.85 | yes | +7% |
| 25 | user_25 | 1,425,000.00 | 0.00 | 1,161,224.64 | yes | −19%, FX gap |

Reading the residuals:

- **The sign flips.** After the principled fixes most rows sit 5–20% *above* gold; request_05,
  request_20 and request_25 are the outliers. A single scaling factor is ruled out (R9, R8).
- **The zeros cluster on users with no projected income or a large day-0 lump** (05, 08, 10, 15,
  23, 24, 25). The `best` config fixes four of the seven; 08 and 10 stay at zero.
- **request_06 is exactly 87.42 short.** Gold 603.30, ours 515.88. The minimum is on day 0
  (balance 1,942.40 → 1,315.885), floor 800. The implied gold day-0 lump is **539.10** against our
  **626.515**. No subset of three or fewer window entries sums to 87.415, and moving streaming or
  cloud storage to a due day does not move the safe amount. The day-0 lump's *composition* differs,
  not its timing.
- **Gold's "wait" rows put the full payment on the deadline**, not on the first safe date:
  request_03 → 2019-11-15, request_08 → 2025-04-15, request_13 → 2024-05-15,
  request_18 → 2026-09-15, request_23 → 2025-07-15. All equal `desired_completion_date`.
  Our wait plans pick the earliest safe date (request_18: 2026-08-14 vs gold 2026-09-15). This is a
  separate, testable rule and it affects `payment_plan` and `earliest_date_for_full_payment`
  on every wait row — worth fixing even though it does not move `amount_safe_to_pay`.

---

## 7. Open hypotheses

Each entry states the hypothesis, the mechanism, the exact test, the signal that would confirm it,
the cost, and what would kill it. Run them in this order; the cheap deterministic ones first.

### H1 — The reference does not double-count this month's settled recurrent bills (D1)

- **Mechanism:** subtract same-month spend from the first bucket, at least for bills already paid.
- **Test:** the structural grid already exists; extend it to *per-category* MTD using the
  flexibility and recurrence signals separately for bills and variable spend.
- **Signal:** exact matches on request_04, request_06, request_21 — the rows where the rent/subs
  remainder is a large share of the lump.
- **Cost:** low (in-memory, ~25 oracle calls per config).
- **Kill:** if no MTD rule produces an exact match on any of those three rows.

### H2 — The reference's day-0 lump is a fixed fraction of the monthly budget (request_06's 539.10)

- **Mechanism:** the pro-rata factor may be computed from a different day count, or from the
  remaining *calendar* days including the request day's bill.
- **Test:** solve for the factor per row: `factor_gold = implied_lump / sum(monthly_budgets)` using
  the gold numbers, and plot it against the request date's day-of-month. If it follows a smooth
  function (e.g. `1 - dom/days`), the rule is identified.
- **Signal:** a consistent curve across 21 rows.
- **Cost:** low (pure arithmetic on data already captured).
- **Kill:** if the implied factors scatter (they look scattered from request_06 alone: 0.86 vs the
  expected 0.94).

### H3 — The budget is per-category median of *transaction* amounts times observed frequency

- **Mechanism:** the reference may not aggregate by month at all; it may take the median ticket
  size and multiply by how many times the category occurs per month.
- **Test:** compute both estimators for every category and compare against the implied day-0 lump
  from H2.
- **Signal:** the implied lump matches the transaction-median estimator on most rows.
- **Cost:** low.

### H4 — The reference uses the *last complete month* rather than a median

- **Test:** estimator variant "most recent complete month" on top of the best structural config.
- **Signal:** exact match on rows whose last month is atypical (user_08 January salary aside).
- **Cost:** low.

### H5 — Variable-category spend is accrued daily, not posted in a lump

- **Mechanism:** `ForecastEntry` per day with `amount/interval`, or one entry per historical
  transaction day-of-month.
- **Test:** emit daily accruals for the variable categories only, keep bills as lumps.
- **Signal:** the minimum moves off day 0 for the rows where `minDay = 0`, and the safe amounts rise
  toward gold without touching the bills.
- **Cost:** medium (needs a real patched forecaster, not a constant).

### H6 — Fixed bills are posted on their historical due day (re-test after D1/D2)

- **Status:** tested alone (R6) and failed; not yet tested *together with* the corrected MTD and
  proration.
- **Test:** re-run the due-day variant on top of the best structural config.
- **Cost:** low.

### H7 — Income is projected with a different cadence (payday shifts, month-end credit)

- **Mechanism:** `anchor + interval` with `interval` = median day gap. A salary paid on the 15th
  gives 30–31 day gaps; if the reference anchors on the *month boundary* the dates shift by 1–2
  days, which changes the minimum on tight rows.
- **Test:** variant that projects salary on `min(day_of_month, month length)` rather than
  `anchor + interval`.
- **Signal:** rows whose minimum sits next to a salary date (request_08, request_20) move.
- **Cost:** low-medium.

### H8 — The safe amount is clipped to the currency's minor unit (floor to whole units)

- **Status:** tested alone (R7) — 0 exact, 14 of 21 closer.
- **Test:** retest on top of the best structural config (rounding and structure interact).
- **Cost:** low.

### H9 — The reference applies the request on day 0 before taking the minimum

- **Mechanism:** `safe = min over t of (balance_t) − floor` with the payment already deducted on
  day 0 gives the same number as ours; but if the reference then *adds back* the payment on day 0
  (i.e. allows a negative day-0 dip), the amount differs.
- **Test:** compute `min(ledger[1:]) − floor` (skip day 0) and compare with gold.
- **Signal:** `no_day0` was tested once and made things worse, but that run used the invalid
  double-counted rebuild (R11). Redo it with the corrected series.
- **Cost:** low.

### H10 — A different spend ceiling: the reference may cap variable spend at the historical
minimum month rather than the median

- **Test:** estimator "minimum observed month" and "25th percentile".
- **Cost:** low.

### H11 — request_25's FX gap is the whole difference for that row

- **Test:** check `exchange_rates.csv` for `usd`→`idr` around 2024-03-06; if the pair is missing,
  add the inverse direction and see whether the row lands near 1,425,000.
- **Signal:** the row moves from 0 to a plausible number.
- **Cost:** very low, and it is a correctness bug regardless of G2.

### H12 — `earliest_date_for_full_payment` for wait rows must be the deadline

- **Mechanism:** all five gold wait rows equal `desired_completion_date`; our solver reports the
  first safe date.
- **Test:** in `_build_candidates`, for the wait option, report the deadline when the full amount is
  safe on or before it. Compare `payment_plan` and `earliest_date` against gold on the wait rows.
- **Signal:** request_08, 13, 18, 23 gain a plan/date match; request_03 too.
- **Cost:** low, and it is defensible on its own (a "wait until" recommendation should name the
  date the person asked for, not an arbitrary earlier one).
- **Note:** this cannot lift `amount_safe_to_pay`, but it moves two of the five per-field counts.

### H13 — The reference's ledger excludes certain statuses we keep (or vice versa)

- **Test:** rerun with `scheduled` rows dropped, then `pending`, then `settled` future rows, one at
  a time; measure exact matches.
- **Cost:** low.

### H14 — Duplicate or overlapping categories for some users

- **Mechanism:** a user may have both `rent` and `housing`, or `utilities` and `bills`, and the
  reference may merge them. Ours budgets both.
- **Test:** count users whose category sets contain synonyms; compare the merged-budget variant.
- **Cost:** low.

### H15 — Deduplication of `linked_event_id` retries differs

- **Test:** disable `_dedup_events` and measure. (Careful: this changes safety semantics; run as a
  diagnostic only.)

### H16 — The 91-day window starts at the settlement date rather than the request date

- **Test:** shift the window start by the settlement lag for scheduled rows.
- **Cost:** low-medium.

### H17 — The reference uses a 90-day horizon *inclusive* (i.e. 90 rows, not 91)

- **Test:** drop the last day of the ledger and recompute.
- **Cost:** very low.

### H18 — Amounts are converted at the request-date rate, not the settlement-date rate

- **Mechanism:** `resolve_rate` uses the settlement date; the reference may use the request date.
- **Test:** variant that pins the rate date to the request date for all rows.
- **Signal:** multi-currency users only (it cannot explain the EUR/USD rows).
- **Cost:** low.

### H19 — The gold labels were produced from a *different* event snapshot

- **Mechanism:** `sample_requests.csv` may predate the current `financial_events.csv` (event
  amendments, cancellations). The 25 rows would then be unreachable by construction.
- **Test:** for each row, check whether the gold explanation's stated facts are consistent with
  today's events (e.g. request_06's "stop the family streaming plan" implies a streaming charge
  the data has; request_03's "pay on 15 November 2019" implies a credit that day). Any row whose
  explanation contradicts the current events is evidence of a snapshot mismatch.
- **Signal:** a set of rows that cannot be satisfied by any model of the current data.
- **Cost:** medium, and it is the test that decides whether "pass G2" is achievable at all.
- **Importance:** this is the single highest-value remaining check. If H19 is true, the goal must
  change from "pass G2" to "document the snapshot mismatch", and no amount of modelling will help.

### H20 — The sample labels are internally inconsistent

- **Test:** check each gold row against the contract in `AGENTS.md` §6.2: `amount_safe_to_pay`
  must be `0 ≤ x ≤ requested_amount`; `affordable_now` implies `earliest_date == request_date`;
  partial payment implies exactly two legs; installments must match a supplied option; a
  `not_recommended` row with a positive safe amount must explain why (request_05, 10, 14, 15, 20,
  24 do exactly this).
- **Signal:** any gold row that violates the contract is a labelling defect, not a modelling gap,
  and should be excluded from the target (and reported).
- **Cost:** low.
- **Importance:** high. If the labels contain contract violations, "20/25" may be unreachable by
  definition.

---

## 8. Agentic probe programme

The deterministic probes have been exhausted at the level of simple families. What is left is
search and induction, and that is where models can genuinely help — as **proposers and auditors**,
never as the authority on a number. Every model output must be converted into a deterministic test
that the kernel runs.

Rules for all probes below:

- **model tiering:** `gemma4:31b-cloud` is the primary reasoner for A1–A7 — it is the only model
  here that both has a 30 B+ budget and selects tools correctly in testing;
  `gemma4:e2b-it-qat` is the local fallback, the default for the product UI, and the
  bill-semantics regression model; `gpt-oss:20b-cloud` is demoted because its tool selection was
  unreliable; `llama3.2:3b` and `qwen3-vl` are not to be trusted with accounting questions (1/3
  each). Kilo only if all of the above fail;
- probes run sequentially, with a small `max_tool_calls` (2–3) and `think=False` where the model
  supports it, because a thinking turn costs 4× the tokens for the same tool call;
- the model never computes a final money figure that ships; it proposes a *rule* or a *parameter*,
  and the harness evaluates it against the oracle;
- record every prompt and reply in `docs/agentic-runs/` (existing convention) so the probe is
  reproducible after a context reset;
- the cloud model must only ever see `mock_dataset` fixtures. It never sees an account from
  `data/accounts.db`.

### A1 — Parameter search with the model as the proposer (highest value)

Give the model the current score (per-field counts + MAPE) and the available knobs with their
domains, and ask for the next parameter set. The harness runs it deterministically and returns the
score. Iterate for N rounds.

Knobs: MTD scope (none / all / recurring-only / variable-only), proration formula
(current / remaining-over-30 / remaining-over-month), partial-month inclusion, budget estimator
(median / mean / last-3 / last-1 / min / p25), due-day posting (on/off), rounding (none / cents /
units), income cadence (interval / calendar).

This is a black-box optimiser with a language model as the proposal distribution. Even if it does
not find an exact match, the parameter sets it converges on are hypotheses in their own right.

**Acceptance:** every proposed parameter set is evaluated by the harness, and the transcript shows
the score after each round. A hit must be validated on a held-out half before it counts.

### A2 — Rule induction from worked examples

Show the model five worked rows (facts in, gold out, with the intermediate day-0 lump and ledger
minimum), ask it to state the rule in one paragraph plus pseudocode, then translate that pseudocode
into a deterministic variant and score all 25 rows.

**Why it can work:** the model may notice a pattern in the *explanations* ("pay in full on 15 April
2025. Paying earlier would take the balance below the 800 minimum") that arithmetic search misses.

**Guard:** the rule must be checkable and must not reference specific request ids.

### A3 — Explanation mining (deterministic, no model needed for the core)

Parse the gold `decision_explanation` for stated invariants:

- "leaves at least X available" → the reference's own minimum balance,
- "paying earlier would take the balance below X" → the binding constraint,
- "Use 3 installments of X, starting DATE" → option maths,
- "Stop the … and reduce … to X" → spending-change actions.

Then test each stated invariant against our ledger. This gives a per-row, independent check of the
reference's own arithmetic and can localise the divergence to a category. The model's role is only
to extract the sentence structure reliably when the regex fails.

### A4 — Agent as auditor of a single row

Give gemma a row's facts, our ledger entries for the first 30 days, our safe amount and the gold
safe amount, and ask which single entry it would remove or move to reconcile them, with a reason.
Collect the answers across 21 rows; if the model repeatedly names the same category, that is a
hypothesis to test deterministically.

**Cost note:** one call per row, sequential; ~10–20 s per call on this hardware.

### A5 — Tool-augmented search

Extend `code/chat.py`'s tool set (in the probe only) with `forecast_dump()` and
`simulate(mtd_scope, proration, estimator)`, and let the model search per row for a parameter set
that reproduces gold. The model then *reports the parameters it used*. Aggregate across rows: if
the same parameters recur for most rows, that is the reference's rule.

This is the most direct "agentic" version of the problem, and it exercises the same tool loop the
product ships.

### A6 — Cross-model self-consistency

Ask gemma, llama3.2 and qwen the same bill-semantics questions already used (settled vs unpaid).
Gemma was 3/3, the others 1/3. Extend to the accounting questions that matter for the forecast
(does a monthly bill recur in the same month after payment? does a salary paid on the 1st recur on
the 1st?). Use agreement as a weak signal only; the kernel remains the authority.

### A7 — Adversarial judge

Show the model our decision row and the gold row side by side (both explanations included) and ask
which of the two is defensible against the contract in `AGENTS.md` §6.2. This tests H20 (label
inconsistency) with a language model as the reader. Any gold row that loses the argument is a
candidate labelling defect.

### A8 — Agentic regression pack

Whatever fixes ship must keep the agent honest. Add a pack that, for a handful of accounts, asks
gemma for the safe amount and asserts the reply contains the kernel figure (the existing probe did
this 10/10). Run it whenever the forecast or the tool layer changes.

---

## 9. Decision tree

```
Start
  │
  ├─ H19/H20 (are the labels consistent with today's data and the contract?)
  │     ├─ No  → document the mismatch, exclude those rows from the target,
  │     │        fix D1–D5 anyway, and stop calling G2 passable.
  │     └─ Yes → continue
  │
  ├─ H12 (wait rows = deadline)  → cheap, defensible, moves plan/earliest on 5 rows.
  │
  ├─ H1/H2/H3 (composition of the day-0 lump)
  │     ├─ One rule matches ≥3 informative rows exactly → implement, then re-score.
  │     └─ None → H5 (daily accrual) and H7 (income cadence), then A1.
  │
  └─ A1 (agentic parameter search)
        ├─ A parameter set hits ≥10 informative rows exactly and holds on the held-out half
        │     → implement behind tests, re-run G2.
        └─ No hit → G2 is a documented calibration gap; ship D1–D5 and the TUI work.
```

---

## 10. Build-mode implementation plan

Order matters: each step is independently revertible and testable.

### Step 1 — failing tests first

`tests/review/test_forecast_calibration.py` (new):

- `test_settled_bill_is_not_charged_twice_in_the_same_month` — user_08 shape; assert the day-0
  entry for `rent` is absent/zero after the 1 Feb payment.
- `test_first_bucket_factor_never_exceeds_one` — 3 Jan 2026; assert the factor is `29/31`, not 1.0.
- `test_off_by_one_is_not_masked_by_the_clamp` — assert no `+1` in the numerator.
- `test_partial_request_month_is_not_a_budget_month` — a category with 3 complete months and a
  2-day partial request month must use the complete months.
- `test_money_in_tool_payloads_is_two_places` — `chat.dispatch(..., "position")` returns strings
  with at most 2 decimals.
- `test_position_takes_an_as_of_date` — `service.position(account, as_of="2026-04-03")` matches
  the recorded row, and `chat`'s `position` tool passes a date through.

### Step 2 — forecast fixes

`code/forecast.py`:

- introduce `_month_to_date_spend(events, req_date, categories, …)`;
- subtract it from the first bucket for the chosen scope (decide with the user: bills only vs all);
- replace the proration with `remaining / days_in_month`;
- delete the `min(amount, prorated)` clamp after the off-by-one is gone (a test should fail if the
  factor can exceed 1);
- exclude the partial request month from `_monthly_budget_history`.

### Step 3 — re-score

Run the harness and record the numbers in this document. Expect G2 to remain failing; record the
exact per-field counts. If `amount_safe_to_pay` rises, good; if it falls, the change is still right
on the merits but must be justified in the report.

### Step 4 — decision layer

`code/solver.py::_build_candidates` — for the wait option, report `desired_completion_date` as the
payment date when the full amount is safe on or before it (H12). Add a test with request_18's shape.

### Step 5 — tool layer (D4)

- `code/chat.py`: round money to 2 places in payloads (`json.dumps(..., default=str)` is not
  enough); keep `Decimal` internally.
- `code/service.py`: `position(account, as_of=...)` already accepts a date; make the chat
  `position` tool pass the account's request date when replaying, and default to today otherwise.

### Step 6 — FX (D5)

Check `mock_dataset/exchange_rates.csv` for `usd`→`idr`; add a unit test that a missing pair fails
closed *loudly* (warning) and that the inverse direction is used when present.

### Step 7 — model defaults

`code/models.py` — default to the gemma entry when present (already implemented);
mark `llama3.2:3b` and `qwen3-vl` as unreliable for bill reasoning in the docstring; keep the three
semantic questions as a regression pack (A8).

The agentic probes use `gemma4:31b-cloud` as the primary reasoner: 32.7 B, and the only model here
that both has a 30 B+ budget and selects tools correctly in testing (3/3). `gpt-oss:20b-cloud` is
demoted because its tool selection was unreliable in use. Both are **cloud** models: they must
appear in the menu as non-local, they may only see fixtures, and `/api/tags` will not list them, so
the entries have to be added explicitly. `.env` is not loaded by any code today (`OLLAMA_API_KEY`
sits there unused); decide deliberately how it reaches the process without landing in a trace.
See Appendix C.

### Step 8 — the rest of the goal (outside G2)

- TUI alignment: done in `tools/app.py` (fixed column widths, right-aligned metrics) — verify
  visually and add a screenshot-based check if possible.
- Model selector: done (`Select` on the Chat tab, gemma preferred, discovery switchable).
- CodeRabbit: **not run**. `coderabbit review --deep --committed --base main` (CLI 0.9.0 present),
  iterate until clean, fix every finding.
- Two independent subagent reviews: correctness/determinism, and security/privacy.
- Full gates: `pytest -q`, `pytest -q tests/review`, `python -m evaluation.run --mode pr`,
  `output.csv` byte-identical.

### Acceptance for the G2 work

- D1–D5 fixed with tests that fail before and pass after.
- G2 numbers recorded honestly in this document and in `docs/improvement-report-2.md`.
- No change to G1 (0 status upgrades, 0 safe overclaims), G3 (determinism) or G4 (provenance).
- `output.csv` byte-identical to `1F35043F…`.
- If G2 still fails, the reason is stated with the evidence and the decision the user made.

---

## 11. Guardrails

- **Never** hardcode a request id, a gold value, or a per-row constant. The repo has a test that
  scans for sample event ids; a label fit that passes G2 by memorising rows is a failure, not a win.
- A change that moves a shipped field needs a test that pins it.
- G1 must stay at 0/0/0: the safety gate catches overclaims, and every "fix" that raises safe
  amounts risks it.
- Determinism (G3) and provenance (G4) must stay green.
- `mock_dataset/` is never edited. `mock_dataset/output.csv` stays the blank template.
- Secrets only from the environment. The free tier trains on what it is sent; the UI says so.
- No Docker build on this machine.
- One commit per change, each revertible.

---

## Appendix A — harness skeleton

```python
"""Replay the sample requests through the real oracle without a pipeline run.

Validated: reproduces the recorded solve_safe_amount output on 25/25 rows.
"""

import calendar
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

import code.forecast as forecast
from code.oracle import solve_safe_amount as real_oracle
from code.recurrence import detect_recurrence

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "mock_dataset"

IMAGE_AMOUNTS = {
    "event_253": "4365000", "event_1442": "100000", "event_1545": "41272.00",
    "event_1700": "2854.00", "event_1786": "704.05",
}

raw = pd.read_csv(M / "financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
for event in raw:
    if event["event_id"] in IMAGE_AMOUNTS and not str(event["amount"]).strip():
        event["amount"] = IMAGE_AMOUNTS[event["event_id"]]

profiles = pd.read_csv(M / "financial_profiles.csv", dtype=str, keep_default_na=False).set_index("user_id")
fx = pd.read_csv(M / "exchange_rates.csv", dtype=str, keep_default_na=False).to_dict("records")
sample = pd.read_csv(M / "sample_requests.csv", dtype=str, keep_default_na=False)
flags = detect_recurrence(raw)


def dec(value):
    try:
        return D(str(value).strip())
    except Exception:
        return None


def as_date(value):
    return date.fromisoformat(str(value).strip()[:10])


def ok(value, gold):
    return value is not None and gold is not None and abs(value - gold) <= D("0.01")


# --------------------------------------------------------------------------
# A patched first bucket. `mtd_scope` in {"none", "all", "recurring_only",
# "variable_only"}; `proration` in {"current", "fixed30", "actual"}.
# `partial` in {"include", "exclude"} decides whether the request month counts
# as a budget month.
# --------------------------------------------------------------------------

ORIG_FN = forecast._monthly_budget_forecast
ORIG_HIST = forecast._monthly_budget_history
PROJECTED = set(forecast.FORECASTABLE_EXPENSE_CATEGORIES) - set(forecast.DISCRETIONARY_CATEGORIES)


def detect_recurring(hist):
    """Category -> usual day of month, when it occurs about monthly on a stable day."""
    by_cat = {}
    for category, when, _ in hist:
        by_cat.setdefault(category, []).append(when)
    out = {}
    for category, days in by_cat.items():
        months = {}
        for when in days:
            months.setdefault((when.year, when.month), []).append(when)
        if len(months) < 2:
            continue
        counts = sorted(len(v) for v in months.values())
        if counts[len(counts) // 2] != 1:
            continue
        doms = sorted(when.day for when in days)
        median_day = doms[len(doms) // 2]
        if sum(1 for x in doms if abs(x - median_day) <= 3) >= 0.7 * len(doms):
            out[category] = median_day
    return out


def history_with(partial):
    def wrapper(*args, **kwargs):
        events, req_date = args[0], args[1]
        monthly = {}
        for category, event_day, amount in forecast._eligible_expense_rows(
                events, categories=PROJECTED, home_currency=kwargs["home_currency"],
                fx_rows=kwargs["fx_rows"], warnings=kwargs.get("warnings", []), warn_on_fx=True):
            if event_day >= req_date:
                continue
            if partial == "exclude" and (event_day.year, event_day.month) == (req_date.year, req_date.month):
                continue
            bucket = monthly.setdefault(category, {})
            key = (event_day.year, event_day.month)
            bucket[key] = bucket.get(key, D(0)) + amount
        budgets = {}
        for category, months in monthly.items():
            if len(months) < forecast.MIN_BUDGET_MONTHS:
                continue
            values = sorted(months.values())
            n = len(values)
            mid = n // 2
            budgets[category] = values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2
        return budgets
    return wrapper


def make_patched(mtd_scope, proration):
    def patched(events, req_date, window_end, *, home_currency, fx_rows, budgets, has_projected_income):
        if not budgets:
            return []
        categories = set(budgets)
        real = forecast._real_in_window_expenses(events, req_date, window_end, categories=categories,
                                                 home_currency=home_currency, fx_rows=fx_rows)
        hist = [(c, d, a) for c, d, a in forecast._eligible_expense_rows(
            events, categories=categories, home_currency=home_currency, fx_rows=fx_rows) if d < req_date]
        month_start = req_date.replace(day=1)
        mtd = {}
        for c, d, a in hist:
            if d >= month_start:
                mtd[c] = mtd.get(c, D(0)) + a
        recurring = detect_recurring(hist)
        remaining = (forecast._first_day_of_next_month(req_date) - req_date).days
        if proration == "current":
            numer, denom = remaining + 1, D(forecast.PRORATION_DAYS_PER_MONTH)
        elif proration == "fixed30":
            numer, denom = remaining, D(30)
        else:
            numer, denom = remaining, D(calendar.monthrange(req_date.year, req_date.month)[1])
        entries = []
        cursor, first = req_date, True
        while cursor <= window_end:
            bucket_end = forecast._next_bucket_start(cursor)
            for category in sorted(budgets):
                amount = budgets[category]
                if first and has_projected_income:
                    amount = min(amount, amount * D(numer) / denom)
                if first:
                    is_bill = category in recurring
                    apply_mtd = (mtd_scope == "all"
                                 or (mtd_scope == "recurring_only" and is_bill)
                                 or (mtd_scope == "variable_only" and not is_bill))
                    if apply_mtd:
                        amount = max(D(0), amount - mtd.get(category, D(0)))
                covered = sum((value for day, value in real.get(category, ())
                               if cursor <= day < bucket_end), D(0))
                uncovered = max(D(0), amount - covered)
                if uncovered > 0:
                    entries.append(forecast.ForecastEntry(
                        date=cursor, amount=-uncovered, category=category,
                        kind="expense", source="history", reason="monthly_accrual"))
            first = False
            cursor = bucket_end
        return entries
    return patched


def run(partial="include", mtd_scope="none", proration="current"):
    forecast._monthly_budget_forecast = make_patched(mtd_scope, proration)
    forecast._monthly_budget_history = history_with(partial)
    try:
        values = {}
        for _, req in sample.iterrows():
            day = as_date(req["request_date"])
            out = real_oracle(req["user_id"], day.isoformat(), str(req["requested_amount"]),
                              profiles.loc[req["user_id"]].to_dict(), raw, fx, flags)
            values[req["request_id"]] = dec(out["safe_amount"])
        return values
    finally:
        forecast._monthly_budget_forecast = ORIG_FN
        forecast._monthly_budget_history = ORIG_HIST


if __name__ == "__main__":
    gold = {row["request_id"]: dec(row["amount_safe_to_pay"]) for _, row in sample.iterrows()}
    baseline = run()
    print("replicate recorded:",
          sum(1 for rid, value in baseline.items() if ok(value, gold[rid])), "of", len(gold))
    for partial in ("include", "exclude"):
        for scope in ("none", "all", "recurring_only", "variable_only"):
            for proration in ("current", "fixed30", "actual"):
                values = run(partial, scope, proration)
                exact = sum(1 for rid, value in values.items() if ok(value, gold[rid]))
                print(partial, scope, proration, exact)
```

---

## Appendix B — data facts worth not re-deriving

- `mock_dataset` has 25 sample requests, 21 of them "informative" (`0 < gold < requested`).
- Gold "wait" rows and their dates: 03 → 2019-11-15, 08 → 2025-04-15, 13 → 2024-05-15,
  18 → 2026-09-15, 23 → 2025-07-15. All equal the deadline.
- Rows with no projected income: 01, 05, 10, 12.
- Row 25 is the only foreign-currency row among the informative set (user_25 is IDR, has USD events).
- request_06 detail: balance 1,942.40, floor 800, day-0 lump 626.515, minimum 1,315.885 on day 0,
  gold 603.30 → implied day-0 lump 539.10 → gap 87.415.
- user_08 detail: balance 1,536.57, floor 800; rent 467.50 settled 2025-02-01; salaries 1,422.85
  monthly with a single 782.57 in January; day-0 lump 803.11; lowest balance −844.02 on 2025-05-07
  under the latest-salary projection.
- `MAX_INCOME_OCCURRENCES = 2`, `MIN_BUDGET_MONTHS = 2`, `PRORATION_DAYS_PER_MONTH = 30`,
  `HORIZON_DAYS = 90` in `code/forecast.py`.
- The oracle's safe amount is exactly `max(0, min(requested, min(daily_balances) − floor))`;
  this reproduces all 25 rows (formula check 25/25).
- The oracle's `processed_events` already includes the forecast entries; never add `forecast` again.

---

## Appendix C — the model tier, verified 2026-11-02

Everything below was measured, not assumed.

### What is installed

| model | where | params | quant | capabilities | tool selection | bill questions |
|---|---|---|---|---|---|---|
| `gemma4:31b-cloud` | Ollama Cloud | 32.7 B | BF16 | completion, **tools**, thinking, vision | **3/3 correct** | not tested on the 3 questions |
| `gemma4:e2b-it-qat` | local | 4.6 B | Q4_0 | completion | not tested | **3/3** |
| `gpt-oss:20b-cloud` | Ollama Cloud | 20.9 B | MXFP4 | completion, **tools**, thinking | unreliable in use — demoted | not tested |
| `llama3.2:3b` | local | 3.2 B | Q4_K_M | completion | — | 1/3 |
| `qwen3-vl:2b-instruct-q4_K_M` | local | 1.7 B | Q4_K_M | completion, vision | — | 1/3 |
| `qwen3-vl:2b` | local | 2.1 B | Q4_K_M | completion, vision | — | not tested |

`/api/show` for `gemma4:31b-cloud` returns
`capabilities: ['completion', 'thinking', 'tools', 'vision']`. It is **not** in `/api/tags`, so
discovery misses it; cloud entries must be added explicitly.

### Verified calls

Tool selection test, three questions, three tools offered (`position`, `list_events`, `decide`):

```
"How much can I safely spend today? Use a tool."        -> position      (7 eval tokens)
"What did I spend on groceries last month? Use a tool." -> list_events   (9 eval tokens)  CORRECT
"Decide request_07 for me. Use a tool."                 -> decide        (19 eval tokens) CORRECT
```

Tool call (the shape `code/chat.py` consumes):

```python
POST http://127.0.0.1:11434/api/chat
{"model": "gemma4:31b-cloud",
 "messages": [{"role": "user", "content": "Call the position tool."}],
 "tools": [{"type": "function",
            "function": {"name": "position", "description": "...",
                         "parameters": {"type": "object", "properties": {}}}}],
 "stream": false, "options": {"num_predict": 256}}
# -> message.tool_calls = [{"id": "call_…",
#                           "function": {"index": 0, "name": "position", "arguments": {}}}]
```

`gpt-oss:20b-cloud` also emits `tool_calls` (68 eval tokens with `think=False`, 272 with thinking),
but its tool *selection* was unreliable, which is why it is demoted rather than primary. If it is
used at all, disable thinking to keep the turn cheap.

### Authentication and discovery gaps to close in build mode

1. `.env` holds `OLLAMA_API_KEY` and **nothing loads it**. Options: a small `code/envfile.py` that
   parses `.env` without exporting it into traces, or a documented `set` before running. Either way,
   the key must never appear in a trace, a report or a commit.
2. `code/models.py::ollama_models` reads `/api/tags`, which omits cloud models. Add configured
   cloud entries (`gemma4:31b-cloud` primary, `gpt-oss:20b-cloud` fallback) behind an explicit flag,
   labelled with their privacy class.
3. `code/providers.py::OllamaProvider` builds `ChatOllama`; confirm it forwards an API key and a
   `base_url` for a cloud model, otherwise the loop will fail with a provider error rather than a
   clear message.
4. The model menu must show the cloud entry as **not local** — under `AGENTS.md` §2 it may not see a
   real person's text, only the fixtures.

### Probe budget guidance

- A1 (parameter search) is the most expensive: keep each model turn to one proposal, ask for JSON
  only, and cap the loop at ~12 rounds.
- A4 (row auditor) is one call per row: 21 calls. With `gemma4:31b-cloud` that is minutes, not
  hours; with `gemma4:e2b-it-qat` it is slower and weaker.
- A6 (three bill questions) is the cheap regression: 9 calls total.
