# Buy-or-Wait Compliance and Accuracy Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for every production change. Use `subagent-driven-development` for independent tasks and review each completed task before moving on.

**Goal:** Close the remaining gaps between the current Buy-or-Wait implementation and `problem_statement.md`, prioritizing submission-critical evidence, usage accounting, and decision correctness before optional accuracy optimization.

**Architecture:** Keep the deterministic oracle and solver authoritative for all amounts, dates, eligibility, and payment plans. Improve the OCR/evidence layer so blank image amounts are resolved only when validated, add provider response usage accounting without exposing credentials, and make spending changes first-class ranked options. LLM output remains explanation-only and always falls back to deterministic prose when unavailable or ungrounded.

**Tech Stack:** Python 3, pandas, pytest, Pillow, pytesseract/Tesseract, OpenRouter-compatible HTTP JSON API, `Decimal`, `zipfile`/PowerShell archive tooling.

**Spec:** `hackerrank-orchestrate-september26/problem_statement.md`

## Global Constraints

- Preserve the exact output columns and order required by `problem_statement.md`.
- Preserve `0 <= amount_safe_to_pay <= requested_amount` for every request.
- Never treat a blank event amount as zero; unresolved evidence must fail closed.
- Only deterministic reconciled facts may affect affordability, payment plans, or spending changes.
- LLM calls may generate `decision_explanation` only; they must not alter any machine decision field.
- Use only `dataset/` participant-facing inputs; do not use organizer-only files or hardcoded evaluation labels.
- Read API keys only from environment variables or the existing parent `.env`; never log or package secrets.
- Enforce the user-approved provider call ceiling of 50 model calls; the final run must report actual calls, tokens, and cost.
- Keep `log.txt` append-only and update it after every user turn per `AGENTS.md`.

## Current Root-Cause Findings and Ranking

| Rank | Issue | Classification | Root cause | Estimated effort |
|---|---|---|---|---:|
| 1 | Fifteen of sixteen linked image amounts remain unresolved | Critical compliance | `code/ocr.py` applies generic full-page OCR plus narrow label heuristics; it has no document-family layout strategy, no preprocessing/cropping, and no validated number-word extraction. Multiple unrelated figures cause safe rejection even when the target field is visually identifiable. | 75–105 min |
| 2 | Provider token/cost accounting is empty for successful live calls | Critical submission requirement | `code/explanations.py::_configured_call()` discards the provider `usage` payload, while `code/usage.py` never updates input/output tokens or cost. The report therefore only describes the no-provider fallback run. | 45–60 min |
| 3 | Spending-change optimization is not exercised | Critical behavioral coverage / high-impact optimization | `code/solver.py` calls `search_changes()` only when no viable base option exists or every option misses the deadline, then returns only a full-payment result. Spending-change plans are not ranked alongside no-change plans, partial payment, installments, or wait. | 75–105 min |
| 4 | Sample-exact recommendation matching is deferred | High-impact accuracy optimization | Current solver/ranking/options semantics are only minimally validated against samples. The implementation has separate inline ranking and `code/ranking.py`, option-duration assumptions, and limited direct solver integration tests; historical verification recorded 71 sample diffs. | 60–90 min |
| 5 | Final packaging and full-dataset verification after fixes | Critical release gate | Any change to OCR, solver, or provider accounting can invalidate `output.csv`, `code.zip`, or the usage report. A fresh run and archive inspection are required after all selected fixes. | 30–45 min |

### Deadline assessment

At the diagnostic time of approximately 14:52 IST, the challenge deadline at 18:00 IST leaves about 3h 08m. The complete plan is estimated at 4h 45m–6h 45m, so it exceeds the remaining window. The deadline-aware critical path is Tasks 1–3 plus Task 5 at roughly 3h 45m–5h 15m; Task 4 should be treated as a separate optimization wave if time remains.

## Task 1: Make linked-image OCR resolve validated amounts

**Priority:** Critical

**Files:**
- Modify: `code/ocr.py`
- Modify: `code/evidence.py`
- Modify: `code/main.py`
- Test: `tests/test_hybrid_evidence.py`
- Test: `tests/test_ocr_probe.py`
- Add: `tests/fixtures/ocr_expected_amounts.json` only if fixture data is needed; do not copy secrets or organizer files.

**Interfaces:**
- `extract_amount_from_text(text, expected_currency) -> dict | None` remains the parser boundary.
- `resolve_event_amounts(images_df, events, image_dir, reader=None, ocr_available=None) -> (events, warnings)` remains the evidence boundary.
- The oracle continues receiving only reconciled event dictionaries with validated amounts.

- [ ] Add RED tests for each failure mode: one expected amount from a labelled field, same-currency conflicting labels, unsupported currency, missing image, OCR unavailable, multiple linked images with conflicting amounts, and all current 16 image/event mappings.
- [ ] Run `python -m pytest tests/test_hybrid_evidence.py tests/test_ocr_probe.py -q` and confirm the new behavior fails for the current implementation.
- [ ] Add image preprocessing in `code/ocr.py`: grayscale, contrast normalization, and a bounded upscale before Tesseract. Keep the raw image path and OCR text available for provenance.
- [ ] Add document-family extraction rules driven by event category/description, not hardcoded event IDs. Examples: salary documents prioritize `Net Pay`/`Transferred`; receipts prioritize `Cash Paid`/`Total Paid`; bills prioritize `Amount Due`/`Grand Total`; payment confirmations prioritize `Total Amount Received`.
- [ ] Add a validated number-word parser for the supported dataset currencies only when the document has a unique amount-word field. If numeric and word amounts disagree, return `AMOUNT_UNRESOLVED`.
- [ ] Score candidates only within the expected currency and target label family. Reject multiple distinct candidates, unsupported currency tokens, cross-user links, missing required image ownership fields, and conflicting linked images.
- [ ] Run the focused tests and a real OCR inventory over all 16 linked images. Record resolved count and unresolved IDs without logging image contents or PII.
- [ ] Run the full pytest suite and commit the focused task.

**Acceptance:** Every image amount is either validated into its linked event or explicitly marked unresolved; no blank amount becomes zero; current OCR resolution is materially higher than 1/16 without unsafe guesses.

## Task 2: Account provider tokens, costs, and bounded calls

**Priority:** Critical

**Files:**
- Modify: `code/explanations.py`
- Modify: `code/usage.py`
- Modify: `code/main.py`
- Modify: `tests/test_llm_explanations.py`
- Modify: `tests/test_submission.py` only if the required report contract needs a stronger regression assertion.

**Interfaces:**
- `_configured_call(packet) -> provider response metadata` should preserve both explanation content and provider usage metadata.
- `generate_explanation(packet, call=None) -> str` remains backward compatible; it updates the optional `_usage` object.
- `write_usage_report(path, usage, request_count)` writes the final report consumed by both the runtime and submission checks.

- [ ] Add RED tests for a provider response containing `choices[0].message.content` and `usage.prompt_tokens`, `usage.completion_tokens`, and `usage.total_tokens`.
- [ ] Add RED tests for a provider response with missing usage, HTTP failure, timeout, malformed JSON, and call-cap exhaustion.
- [ ] Run the focused LLM/usage tests and confirm they fail because `_configured_call()` currently returns content only and token fields remain zero.
- [ ] Return a structured internal response containing content, provider, model, input tokens, output tokens, total tokens, and cost metadata without exposing the API key.
- [ ] Add a provider pricing configuration keyed by provider/model; if no price is configured, report cost as `unavailable` or an explicitly documented zero estimate rather than silently implying a paid call cost of zero.
- [ ] Count only actual provider calls, successful calls, fallback calls, cache hits, and rejected responses. Enforce `max_calls <= 50` before issuing a request.
- [ ] Bound provider timeout and retry behavior so a stalled provider cannot hold the full run indefinitely. A failed call must immediately use deterministic fallback prose.
- [ ] Run one provider-enabled smoke test and a capped production batch. Do not exceed 50 calls. Preserve the final run’s exact usage metadata in `evaluation/usage_report.md` and `tools/evaluation/usage_report.md`.
- [ ] Run the full pytest suite and commit the task.

**Acceptance:** A successful live call produces nonzero provider usage fields in the report; failed calls are counted separately; the final report has per-model and overall rows; the hard cap is enforced; no credential appears in logs, output, report, or archive.

## Task 3: Make permitted spending changes first-class safe plans

**Priority:** Critical behavioral coverage

**Files:**
- Modify: `code/changes.py`
- Modify: `code/solver.py`
- Modify: `code/ranking.py` if the shared ranking function becomes authoritative.
- Test: `tests/test_changes.py`
- Test: `tests/test_solver.py`
- Add: `tests/test_solver_changes_integration.py`

**Interfaces:**
- `search_changes(...) -> list[str]` must continue returning only validated `stop:<event_id>` and `reduce_to:<event_id>:<amount>` actions.
- Solver option records must carry `changes`, `completion_date`, `total_cost`, `start_date`, `legs`, and `option_id` so they can use the same ranking tuple as other plans.

- [ ] Add RED tests for a request where a permitted flexible recurring expense makes full payment safe, a request where no-change is preferred over a changed plan, protected-category rejection, user-permission rejection, one-time-event rejection, cross-user rejection, stop/reduce exclusivity, and the three-change cap.
- [ ] Run the focused tests and confirm the solver currently returns `spending_changes_needed=none` or `not_affordable` for the intended change-resolvable fixtures.
- [ ] Make candidate filtering require the event to be the same user, recurring/flexible, within the forecast window, permitted by the relevant profile category, and not protected.
- [ ] Evaluate spending-change variants before final fallback, including partial payment/installments/wait where the changed ledger makes those plans safe.
- [ ] Add changed plans to the common ranking pool rather than returning early from the spending branch. Preserve the specification order: deadline, no changes, lowest total paid, earlier start, fewer payments, lowest option ID.
- [ ] Preserve the original no-change `earliest_date_for_full_payment`; spending changes must not corrupt that independent field.
- [ ] Run focused integration tests, full pytest, and output invariant checks.

**Acceptance:** The solver can emit valid spending changes when required and permitted, never changes protected/non-recurring/cross-user events, never uses more than three changes, and ranks changed versus unchanged plans according to the problem statement.

## Task 4: Reduce sample mismatches and consolidate ranking semantics

**Priority:** Optimization / high-impact accuracy

**Files:**
- Modify: `code/ranking.py`
- Modify: `code/solver.py`
- Modify: `code/options.py`
- Test: `tests/test_ranking.py`
- Test: `tests/test_options.py`
- Test: `tests/test_solver.py`
- Add: `tests/test_sample_tradeoffs.py`

- [ ] Add RED tradeoff tests for deadline-over-cost, no-change-over-cost, earlier-start-over-later-start, fewer-legs, and payment-option-ID tie breaks.
- [ ] Add direct solver tests for full payment, partial payment, wait, installment fee traps, malformed dates, missing eligibility flags, and earliest-date independence.
- [ ] Make one ranking implementation authoritative; remove or route the inline solver key through `rank_options()` so the two paths cannot diverge.
- [ ] Reconcile installment duration semantics with the problem statement’s actual payment dates and `max_installment_months`, using `Decimal`/date arithmetic instead of integer-month truncation.
- [ ] Run the sample verifier and classify each mismatch by amount, status, method, plan, earliest date, spending change, or explanation. Fix only causes supported by the specification; do not hardcode sample labels.
- [ ] Re-run the sample verifier and record the remaining mismatch count in `plan_a.md`.

**Acceptance:** Ranking order matches the six rules in `problem_statement.md`, direct solver tests cover each allowed method, and sample mismatches decrease without hardcoded IDs or weakened safety checks.

## Task 5: Final release verification

**Priority:** Critical release gate

- [ ] Run the final full-dataset command from the documented entrypoint.
- [ ] Verify exact output columns, one row per request, ID uniqueness, amount bounds, chronological plans, installment-option equality, spending-change validity, and no forbidden secrets.
- [ ] Verify the final usage report describes the exact run that produced `output.csv`.
- [ ] Rebuild `code.zip` with `code/`, README, and `evaluation/usage_report.md`; exclude `__pycache__`, `.env`, keys, and temporary scripts.
- [ ] Run `python -m pytest tests -q` and archive-content checks.
- [ ] Update `plan_a.md` with resolved counts, test totals, final usage numbers, remaining gaps, and the final submission verdict.

## Submission decision rule

The solution is **artifact-ready** when Task 5 passes. It is **fully problem-statement compliant** only when Task 1 resolves or validates every linked blank amount, Task 2 accurately reports any provider-backed final run, and Task 3 demonstrates valid spending-change decisions. Task 4 is not a hard packaging blocker but is a high-impact accuracy optimization because the evaluator scores recommendation fields against hidden ground truth.
