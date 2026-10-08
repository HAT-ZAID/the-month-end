# Reviewer 4 — Spec Compliance Gate (§6.2 / §6.3 / §6.4 / §6.5 + output contract)

**Reviewer:** 4 of 4 (read-only)
**Repo:** `D:\Hackerrank_Orchestrate\hackerrank-orchestrate-september26`
**Date:** 2026-10-08
**Scope:** `problem_statement.md` + `AGENTS.md` §6.2–§6.5 vs implementation and `output.csv`
**Python used:** `.venv\Scripts\python.exe` (pandas 2.x)

---

## Verdict: **APPROVE**

No Critical findings. Every item of the §6.2 output contract was machine-checked over all 250 rows and passes; §6.3 rules are enforced in code and covered by a passing test suite; §6.4 constraints hold (zero organizer/sample literals in `code/`); §6.5 ceiling is `MAX_PROVIDER_CALLS = 50` and is checked *before* the call is issued. Three Important findings are documentation/reproducibility issues, none of which breaks the contract or the submission artifacts.

---

## Findings

| # | Severity | Location | Problem | Exact proof | Suggested fix |
|---|----------|----------|---------|-------------|---------------|
| 1 | **Important** | `README.md:78-79` vs rerun of pipeline | README claims *"The LLM never controls a scored field, so the same inputs produce the same `output.csv` on every run."* — false as written: `decision_explanation` **is** a scored axis (`problem_statement.md:222`), and 8 of its cells came from live LLM calls, so a keyless rerun does not reproduce the committed artifact byte-for-byte. | In-memory rerun (no API key, `max_calls=0`) of the full 250-row pipeline: `all_7_decision_columns_identical: True` / `explanation diffs: 8 ['request_26','request_29','request_33','request_34','request_41','request_45','request_47','request_48']`. `evaluation/usage_report.md:5-6` corroborates: `Model calls: 25` / `Successful calls: 8` / `Rejected responses: 242` — and all 8 ids fall inside the first 25 requests (`request_26`–`request_50`), consistent with `BUY_WAIT_LLM_MAX_CALLS=25`. | Qualify the claim: "the seven decision columns are deterministic; `decision_explanation` is deterministic *given the same provider configuration*". Optionally ship the exact env (keyless) run's output, or state that the delivered artifact includes 8 provider-written explanations. |
| 2 | **Important** | `problem_statement.md:41` vs `dataset/output.csv` | The problem statement says `dataset/output.csv` is the "Blank submission template. **Fill this file** with predictions"; that file is still blank while predictions live at repo-root `output.csv`. A grader following the problem statement literally reads an empty file. | `dataset/output.csv` non-empty cells per column: `request_id 250`, all other 7 columns `0`. Root `output.csv`: `(250, 8)` with all columns populated. Repo authorities disagree in emphasis: `AGENTS.md` §6.1 "`output.csv` is the blank prediction template", `AGENTS.md` §6.2 "must write `output.csv`", `README.md:103-105` and `code/README.md:36,55` both say repository root; `tests/conftest.py:88` gates on root `output.csv`. | Leave root `output.csv` as the uploaded artifact (current behavior, matches README/tests). If desired, mirror the filled file into `dataset/output.csv` or add one README line: "`dataset/output.csv` stays blank by design; the submission file is the root `output.csv`." |
| 3 | **Minor** | `tests/test_tui.py:6`, `tools/tui.py:29`, `tests/requirements-test.txt` | Full test suite cannot even collect in the shipped venv: `test_tui.py` imports `tools.tui` → `rich` missing. `tests/requirements-test.txt` lists only `pytest>=8`, `pandas>=2`, so a documented test-deps install still errors on collection. | `$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest -p no:cacheprovider -q` → `ERROR collecting tests/test_tui.py ... E ModuleNotFoundError: No module named 'rich'` / `1 error in 0.62s` (whole run interrupted). Re-run with `--ignore=tests/test_tui.py` → `287 passed, 1 skipped in 26.35s`. | Add `rich` to `tests/requirements-test.txt` (or `pytest.importorskip("rich")` in `tests/test_tui.py`). README's `pip install rich` (line 87) already documents it for the TUI, so this is a test-harness gap, not a run-instruction gap. |
| 4 | **Minor** | `code/explanations.py:161-167` | The 50-call ceiling is only enforced when the packet carries `_usage`. A direct `generate_explanation(packet, call=...)` without `_usage` skips the guard and would issue uncapped calls. Production path always supplies it (`code/main.py:165`), so the shipped pipeline is capped. | `code/explanations.py:161` `if usage is not None:` gates the check at line 164; `code/main.py:154-166` passes `"_usage": usage` for every row. | Move the cap check outside the `usage is not None` branch (or require `_usage` when `call is not None`). |
| 5 | **Minor** | `code/usage.py:11`, `evaluation/usage_report.md:16-18` | §6.5 asks the report to include "estimated total and per-request cost". `PRICING_USD_PER_MILLION_TOKENS` is empty, so both fields are literally `unavailable` (no numeric estimate). Honest, and explicitly explained, but not a number. | `code/usage.py:11` `PRICING_USD_PER_MILLION_TOKENS: dict[...] = {}`; report lines: `Cost status: unavailable`, `Estimated total cost (USD): unavailable`, `Estimated cost per request (USD): unavailable`, plus `Unpriced cost estimate (USD): 0.000000 (not a paid estimate when pricing is unavailable)`. | Add pricing rows for `openrouter`/`inclusionai/ling-3.0-flash-fin` (as of the run) so the two cost fields carry estimates, or state in the report that pricing was not configured for the scored run (already partly stated). |
| 6 | **Minor (risk note)** | `output.csv`, 40 `wait` rows | `wait` rows emit a `payment_plan` of one future leg (`<earliest_date>:<requested_amount>`) instead of `none`. Spec says plan = "all payments in the recommendation" and `none` "when no payment is recommended"; a later full payment *is* recommended, so this is defensible, but hidden gold may prefer `none`. | 40/40 wait rows: `wait plan date==earliest: True`, `wait plan amount==requested: True`, `wait plan legs==1: True`. Format/chronology/enums all valid. | No change required for contract compliance; flag to the team as a gold-alignment gamble only. |

No Critical findings.

---

## Mandatory question — gold reproduction risk

**Does the implementation special-case `event_476`, `event_989`, `event_1815`, `event_1816`, or any `dataset/sample_requests.csv` answer? NO.**

Raw output of the mandated command (run verbatim from repo root):

```powershell
$m = Select-String -Path code\*.py -Pattern "event_476|event_989|event_1815|event_1816|sample_requests"
Write-Output "MATCH_COUNT=$(@($m).Count)"; Write-Output "--- raw output ---"; $m; Write-Output "--- end ---"
```

```
MATCH_COUNT=0
--- raw output ---
--- end ---
```

(Plain `Select-String -Path code\*.py -Pattern "event_476|event_989|event_1815|event_1816|sample_requests"` also produced **no output**.)

Supporting searches in `code/` (all zero hits for row/user/event id literals):

```powershell
Select-String -Path code\*.py -Pattern "request_\d|user_\d|event_\d"   # no output
Select-String -Path code\*.py -Pattern "sample|gold|label|expected"     # only OCR document-label code
                                                                        # (ocr.py:19 _GENERIC_LABELS, :25 _FAMILY_LABELS, :110 expected_currency)
```

The only occurrence of `event_476` anywhere is in tests: `tests/test_gold.py:63 assert "stop:event_476" in r6["spending_changes_needed"]` — tests are outside `code/`, so per gate rules this is not Critical.

**Every file `code/` opens** (exhaustive grep for `read_csv|to_csv|write_text|open(|glob(|Image.open|urlopen` over `code/*.py`):

| Mode | File | Cite |
|------|------|------|
| read | `dataset/requests.csv` | `code/main.py:105` |
| read | `dataset/financial_profiles.csv` | `code/main.py:106` |
| read | `dataset/financial_events.csv` | `code/main.py:107` |
| read | `dataset/images.csv` | `code/main.py:108` |
| read | `dataset/exchange_rates.csv` | `code/main.py:109` |
| read | `dataset/request_payment_options.csv` | `code/main.py:110` |
| read | `dataset/messages.csv` | `code/main.py:111` |
| read | `dataset/media/images/<image_id>.png` | `code/evidence.py:56`, `code/ocr.py:262,281,394` |
| write | `output.csv` (repo root) | `code/main.py:172` |
| write | `evaluation/usage_report.md` | `code/main.py:173` |
| write | `tools/evaluation/usage_report.md` | `code/main.py:174` |
| network (env-gated) | chat-completions endpoint | `code/explanations.py:85` |
| exec probe | Tesseract binary via `TESSERACT_CMD`/PATH | `code/ocr.py:349-369` |

No file outside `dataset/` is read: no `sample_requests.csv`, no `tests/`, no `evaluation/`, no `docs/`, no `log.txt`, no organizer artifact. `sample_requests.csv` is read only by tests (`tests/conftest.py:71-72`) and `tools/verify_samples.py`, neither of which is imported by `code/`.

---

## Pass/fail checklist

### §6.2 / output contract (`output.csv`, 250 rows)

| Check | Result | Proof |
|---|---|---|
| Exact 8 columns, exact order | **PASS** | `cols_match True` / `['request_id','amount_safe_to_pay','affordability_status','recommended_payment_method','payment_plan','earliest_date_for_full_payment','spending_changes_needed','decision_explanation']` |
| Exactly one row per `request_id` in `dataset/requests.csv`, no extras, no dupes | **PASS** | `ids exactly match set: True dup rows: 0 n: 250`; `same order as requests: True` (requests: `250 rows / 250 unique ids`) |
| `0 <= amount_safe_to_pay <= requested_amount` on all 250 rows | **PASS** | Validator over 250 rows: `ERRORS: none` (Decimal compare per row; min 0, max 38114000, 27 zeros) |
| Extra necessary condition: `amount <= current_available_balance - minimum_balance_to_keep` | **PASS** | `amount_safe > (balance - min_balance): 0` (250/250) |
| `affordability_status` ∈ 4-value enum | **PASS** | 0 violations (validator `bad_status` absent); distribution: affordable_now 66, affordable_with_plan 68, affordable_later 40, not_affordable 76 |
| `recommended_payment_method` ∈ 5-value enum | **PASS** | 0 violations; full_payment 70, installments 56, partial_payment 8, wait 40, not_recommended 76 |
| `payment_plan` = chronological `YYYY-MM-DD:amount` joined by `\|`, or `none` | **PASS** | 0 format/chronology violations; `plan==none by method:` not_recommended 76/76, all other methods 0/… (every recommended payment has a plan) |
| `partial_payment`: exactly 2 legs | **PASS** | 8/8 rows `len(legs)==2` |
| partial leg 1 on `request_date` = `amount_safe_to_pay` | **PASS** | 8/8 (`partial_leg1_date` / `partial_leg1_amt` violations = 0) |
| partial leg 2 on `earliest_date_for_full_payment` | **PASS** | 8/8 (`partial_leg2_date_vs_earliest` = 0) |
| two legs sum **exactly** to `requested_amount` (recomputed by me with `Decimal`) | **PASS** | 8/8 exact (`partial_sum` = 0 violations) |
| partial only when allowed / 0 < amount < requested / status `affordable_with_plan` / 2nd leg ≤ deadline | **PASS** | 0 violations on all four sub-conditions; also `partial without allows_partial_payment: 0` |
| installment plan exactly matches a supplied option | **PASS** | Reconstructed all 56 plans from `request_payment_options.csv` (`first_payment_date + i*payment_frequency_days`, `payment_amount`, `number_of_payments`): `installment rows: 56 matched option exactly: 56 mismatch: 0` |
| `earliest_date_for_full_payment == request_date` for `affordable_now` | **PASS** | `affordable_now earliest!=request_date: 0` (66/66) |
| earliest empty when no safe full payment in forecast | **PASS** | `not_affordable: nonempty earliest: 0` (76/76); `affordable_later: empty earliest count: 0` (40/40 non-empty, none equal `request_date`); all non-empty earliest dates inside `[request_date, request_date+90]`: `earliest range violations: 0` |
| `spending_changes_needed` = `none` or ≤3 valid `stop:`/`reduce_to:` actions | **PASS** | 4 rows with changes, 5 tokens: `rows with changes: 4 total change tokens: 5 / issues: none` (regex-validated tokens, ≤3, no stop+reduce on same event) |
| only non-protected, flexible, user-permitted events changed | **PASS** | `event_7192 streaming reducible_or_stoppable` (stop-ok: `streaming`), `event_7201 shopping reducible` (reduce-ok: `shopping`), `event_9226/10878/12447 dining reducible` (reduce-ok: `dining`); none in `expense_categories_to_protect`; every `reduce_to` amount equals the event's `minimum_allowed_amount` and is ≤ original amount |
| `decision_explanation` non-empty | **PASS** | `empty explanations: 0`; min length 102, max 306 |
| explanation grounded in row values | **PASS** | `explanations mentioning amount_safe_to_pay: 245/250` + 5 remaining use comma-formatted figures (e.g. `request_33` "42,463.44") → 250/250; every "minimum balance …" mention matches the profile: `min-balance mentions mismatching profile: 0`; the 8 provider-written rows individually verified against profile/row values (all `amount_grounded True`); `status keyword mismatches: none` |
| no invented facts / fail-closed on unresolved evidence | **PASS** | `code/main.py:41-51` `_fail_closed_result`; `code/oracle.py:426-431` fail-closed on `FX_UNKNOWN`/`STATUS_UNKNOWN`; the single unresolved-evidence row (`request_101`) is `not_affordable`/`not_recommended`/amount `0` |

### §6.3 Financial Decision Rules

| Rule | Result | Proof |
|---|---|---|
| Balance never below `minimum_balance_to_keep` in recommended plan | **PASS** (code-enforced + tested) | `code/oracle.py:443-444` `safe_amount = max(0, min(req_amt, min(daily_balances) - min_bal))`; `code/oracle.py:447-453` earliest requires `daily_balances[j] - req_amt >= min_bal` for all `j >= t`; `code/oracle.py:434-438` 91-point daily ledger; `code/solver.py:47-58` `_installment_safe` "every daily post-leg balance stays >= minimum_balance_to_keep", applied at `code/solver.py:306,378` with `installment_safety="strict"` pinned at `code/main.py:137`. Independent necessary condition `amount_safe <= balance - min` holds 250/250. Test suite: `287 passed, 1 skipped` incl. `test_invariants.py`, `test_oracle_cycle1_safety.py`, `test_ledger.py`. |
| Pending credits excluded, pending debits reserved | **PASS** | `code/oracle.py:16-19` allowed statuses `settled/scheduled/pending`; ignored `failed/unrealized/cancelled`; `code/oracle.py:288-289` `# Ignore pending credits, reserve pending debits.` (`if status == "pending" and dir_norm == "credit": …`) |
| Confirmed income only, no invented income | **PASS** | Income forecast built from history only (`code/oracle.py:378-424`), non-cash excluded (`code/oracle.py:56-57`), fail-closed on unknown status/FX; LLM cannot alter any decision field — `code/explanations.py:158` "the LLM never controls the financial decision", grounding gate at `:248`, fallback at `:256-259` |
| Protected categories respected | **PASS** | see spending-changes row above (4/4 rows) |
| Conflict resolution / ranking order | **PASS** (tested) | `code/ranking.py` implements the six-rank comparator (`problem_statement.md:189-196`); `tests/test_ranking.py` passed |

### §6.4 Constraints

| Constraint | Result | Proof |
|---|---|---|
| Runnable from terminal | **PASS** | `python -m code.main --help` → docstring, `EXIT=0`; `python code/main.py --help` → same, `EXIT2=0` (both run without error; `--help` deliberately avoids rewriting artifacts) |
| Reads provided files from `dataset/` | **PASS** | `code/main.py:105-111` (file table above) |
| No organizer-only files / hardcoded labels | **PASS** | mandated `Select-String` → `MATCH_COUNT=0`; no `request_\d|user_\d|event_\d` literals in `code/`; exhaustive open-list contains only `dataset/` inputs |
| Deterministic where possible | **PASS with caveat (Finding 1)** | no `random|time.time|datetime.now|date.today|uuid` hits in `code/*.py`; independent in-memory rerun reproduced all 7 decision columns byte-identically (`all_7_decision_columns_identical: True`); 8 explanation cells differ keylessly (Finding 1). `tests/test_determinism.py` passed. |
| Secrets from env only | **PASS** | env reads at `code/explanations.py:41-43`, `code/usage.py:29-36`, `code/ocr.py:351`; secret-pattern grep over `code/*.py,tools/*.py` (`sk-…`, `Bearer …`, hardcoded `api_key=`) → **no output**; `evaluation/usage_report.md:26` "No API keys or credentials are included"; `code.zip` contains no key material |
| Clear setup/run instructions | **PASS** | `README.md:83-98` (`pip install pandas`, `pip install rich`, `python -m code.main  # or: python code/main.py`), `:107-115` TUI, `:117-132` env table; `code/README.md:36-41`; both READMEs are inside `code.zip` |

### §6.5 Token ceiling & submission artifacts

| Item | Result | Proof |
|---|---|---|
| Actual `MAX_PROVIDER_CALLS` value | **50** | `code/usage.py:7` `MAX_PROVIDER_CALLS = 50` |
| Runtime cap enforced *before* issuing a call | **PASS** | `code/explanations.py:162-167`: re-clamp `configured_max = max(0, min(…, MAX_PROVIDER_CALLS))`, then `if int(usage.get("calls",0)) >= configured_max:` → `return _fallback(packet)` — this returns **before** line 177 `call(packet) … _configured_call(packet)` and line 85 `urlopen`. Also clamped at construction: `code/usage.py:52` `"max_calls": max(0, min(configured_max, MAX_PROVIDER_CALLS))`. (Guard-skip edge case: Finding 4.) |
| Report summarizes the run that produced `output.csv` | **PASS** | `evaluation/usage_report.md` = 25 calls / 8 successful / 242 fallbacks / 11898 in / 19190 out / 31088 total / 124.352 avg per request / per-model + Overall rows / provider `openrouter`, model `inclusionai/ling-3.0-flash-fin` — matches the artifact exactly (8 non-fallback explanations on disk, all inside `request_26`–`request_50`; 242 fallbacks elsewhere) |
| `code.zip` includes `evaluation/usage_report.md` | **PASS** | zip listing (20 entries): `README.md`, `code/README.md`, `code/*.py` (16 files), `evaluation/usage_report.md`, `problem_statement.md`; embedded report byte-identical to root (`len=972`) |
| No API keys/credentials in artifacts | **PASS** | report line 26 + repo-wide secret grep empty |
| Cost fields present | **Partial (Finding 5)** | fields exist, values `unavailable` because pricing table is empty |

### Entry points

| Item | Result | Proof |
|---|---|---|
| `python -m code.main` documented | **PASS** | `README.md:97`, `code/main.py:6` docstring |
| `python code/main.py` documented | **PASS** | `README.md:97` (`# or: python code/main.py`), `code/README.md:41` (`python3 code/main.py`); dual-import shim `code/main.py:12-25` |
| Both entry points actually run | **PASS** | `python -m code.main --help` → `EXIT=0`; `python code/main.py --help` → `EXIT2=0` (identical usage text) |
| `python -m tools.tui` documented session-only, never writes `output.csv`/`dataset/` | **PASS (static)** | `README.md:107-115` "Every edit is **session-only** … never writes `output.csv` or `dataset/`"; `tools/tui.py:10,85,980,986,1021` state the same; grep for `to_csv|write_text|open(…'w'|output.csv` writes in `tools/tui.py` → **no output**; dataset load is read-only (`tools/tui.py:353-371`); `generate_explanation` never imported by the TUI (grep no hits) so no provider calls |
| TUI runtime behavior | **NOT VERIFIED** | `python -m tools.tui --help` → `ModuleNotFoundError: No module named 'rich'` in the shipped venv (Finding 3) |

---

## Evidence appendix — key raw command outputs

```
# structural validation (root output.csv)
root==dataset output identical: False          # dataset/output.csv is the blank template (Finding 2)
ids exactly match set: True dup rows: 0 n: 250
same order as requests: True
ERRORS: none                                   # bounds, enums, formats, partial arithmetic, earliest rules

# installments
installment rows: 56 matched option exactly: 56 mismatch: 0

# spending changes
rows with changes: 4 total change tokens: 5
issues: none

# explanation grounding
empty explanations: 0 ; unique explanations: 249 of 250 (2 shared by request_144/request_242: identical facts, amount 0, minbal 600)
min-balance mentions mismatching profile: 0
explanations NOT containing amount_safe_to_pay: 5  (all 5 use comma-formatted figures — verified)

# cross-tabs
affordable_now      -> full_payment 66
affordable_with_plan-> full_payment 4 (all with spending changes), installments 56, partial_payment 8
affordable_later    -> wait 40
not_affordable      -> not_recommended 76

# plan / eligibility
plan completes after deadline: 0
plan starts before request_date: 0
ineligible method recommendations: 0
installment preference violations: 0
partial without allows_partial_payment: 0

# format
size 50870 CRLF count 0 LF count 251 BOM False  trailing newline: True
numeric format issues: 0                        # no >2-decimal money, no thousands separators

# independent in-memory rerun (106.2s, no writes)
all_7_decision_columns_identical: True
explanation diffs: 8 ['request_26','request_29','request_33','request_34','request_41','request_45','request_47','request_48']
usage_calls 0 rejected 250 fallbacks 250        # my run forced max_calls=0

# tests
full suite:  ERROR collecting tests/test_tui.py → ModuleNotFoundError: No module named 'rich' (1 error, interrupted)
--ignore=tests/test_tui.py: 287 passed, 1 skipped in 26.35s

# mandated gold-reproduction command
MATCH_COUNT=0  (raw output empty)
```

---

## What I could NOT verify, and why

1. **Full `python -m code.main` run in place.** It rewrites `output.csv` and `evaluation/usage_report.md` (`code/main.py:172-174`), and I am read-only. Substitute: an in-memory replication of the exact main-loop (same calls, same `_money`/`_money_plan`/`generate_explanation`, `max_calls=0`) compared against the on-disk file — reproduced all 7 decision columns exactly (Finding 1 for the 8 explanation cells).
2. **Runtime behavior of `python -m tools.tui`.** `rich` is absent from `.venv`; verified read-only/no-write behavior statically (`tools/tui.py` open/write grep empty, docstrings at `:10,85,980,986,1021`).
3. **Accuracy against hidden ground truth.** Not available to reviewers; internal gold checks (`tests/test_gold.py`, `tests/spec_checks.py`) pass but are not the hidden labels.
4. **A fully independent re-derivation of all 90-day forecasts.** I validated the safety machinery by reading `code/oracle.py`/`code/solver.py`, running the 287 invariant/oracle/ledger tests, and checking the necessary condition `amount_safe <= balance - min_balance` on 250/250 rows — not by rebuilding a second ledger for every row.
5. **Which file a human grader will open** (`output.csv` at root vs blank `dataset/output.csv`) — flagged as Finding 2; all in-repo authorities point to root.
6. **Live provider path (real LLM call, cap trip at 50).** No API key in this environment; cap ordering verified by code reading (`code/explanations.py:162-167` precedes `:177`/`:85`), and the shipped `evaluation/usage_report.md` shows a capped run (`BUY_WAIT_LLM_MAX_CALLS=25 ≤ 50`, 25 calls, 242 fallbacks) consistent with the artifact.
