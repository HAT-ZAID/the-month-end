# Plan A: Fused Lead System Implementation

This document outlines the design and implementation of the "Fused Lead A" solution for the Buy-or-Wait agent. This approach synthesizes the most robust elements of the proposed solution sketches into a single, verifiable pipeline.

## 1. Architectural Philosophy
The system is built as a pipeline of decoupled modules, each with its own TDD-verified a-priori oracle. The core principle is **conservative loundering**: if any piece of evidence is ambiguous or missing, the system defaults to the most financially safe interpretation.

## 2. Component Blueprint

### A. Input & Extraction (P1)
- **OCR Spike (`code/ocr.py`)**: A tiered vision pipeline. It probes for Tesseract binaries; if absent, it routes all images to a "reserve" path, flagging them as `OCR_UNAVAILABLE` rather than inventing figures.
- **FX Resolver (`code/fx.py`)**: Implements nearest-prior resolution. If an exact date match is missing, it uses the most recent prior snapshot and flags the result as `FX_UNKNOWN`.
- **Amount Router (`code/amounts.py`)**: A locale-aware lexicon. It handles ISO codes, symbols, and boundary-less currency tokens (e.g., "BalanceUSD"). It uses a robust separator heuristic: the last separator is treated as the decimal, and all preceding identical separators as groupings.
- **Reconciler (`code/reconcile.py`)**: Implements the strict conflict resolution order from `AGENTS.md` §6.3:
  `Explicit Cancellation/Settlement` $\rightarrow$ `Newer Record` $\rightarrow$ `Settled Baseline` $\rightarrow$ `Safer Interpretation`.
- **Recurrence Detector (`code/recurrence.py`)**: Flags recurring costs based on $\ge 3$ observations of the same (user, category, event_type) and a conservative Coefficient of Variation (CV $\le 0.5$) gate.

### B. The Independent Oracle (`code/oracle.py`)
The "brain" of the system that proves the legality of any proposed payment.
- **91-Point Ledger**: Reconstructs the user's balance daily for 90 days from the request date.
- **Launder Logic**:
    - `safe_amount`: The maximum $X \le \text{requested\_amount}$ such that $\text{balance}_t - X \ge \text{min\_balance}$ for all $t \in [0, 90]$.
    - `earliest_date`: The first date $t$ where a full payment is safe for all future days in the window.
    - **FX Fail-Safe**: If any event in the window is `FX_UNKNOWN`, the oracle fails-closed (`safe_amount = 0`).

## 3. Implementation Roadmap

### Phase 1: Inputs & Oracle (Completed)
- [x] TDD build of all Input modules (OCR, FX, Amounts, Reconcile, Recurrence).
- [x] Implementation of the 90-day ledger simulation and lounder logic.
- [x] Verification of the "Safe Amount" and "Earliest Date" oracles.
- [x] Suite status: 114 passed, 10 skipped.

### Phase 2: Ranking & Validation (Minimum gate completed)
- **Ranker**: Existing solver path uses the completion-first ranking order required by the specification.
- **Installment safety**: Strict plans are checked against the 91-point ledger, minimum balance, request-date ordering, and horizon; malformed plans fail closed.
- **Deferred**: Full spending-change optimization and sample-exact matching are not part of the minimum gate.

### Phase 3: Submission & Packaging (Artifact gate completed)
- **Full Run**: Completed across all 250 evaluation requests.
- **Packaging**: `code.zip` contains the runnable `code/` package, README, and `evaluation/usage_report.md`; `output.csv` and `log.txt` are present separately.
- **Hybrid evidence/explanation integration**: Linked-image OCR now runs before oracle evaluation, unresolved evidence is flagged fail-closed, and optional environment-configured LLM calls generate explanations without controlling financial decisions.
- **Usage accounting**: The final no-provider run records 0 model calls, 250 deterministic fallbacks, 0 tokens, and 0 cost.

## 4. Contingency (The Pivot Scaffold)
If Plan A fails a critical gate (e.g., solver cannot converge or oracle proves too restrictive), the system can pivot to **Plan B (P1-A-fixed)**: replacing the solver with a wholesale enumerator that iterates all possible payment combinations and selects the cheapest safe one.

## 5. Minimum defensible submission status (2026-09-13)

- Bare-minimum solver safety completed: strict installment plans are checked against the 91-point oracle ledger, minimum balance, request-date ordering, and the 90-day horizon.
- Strict installment validation fails closed for malformed, partial, negative, non-finite, pre-request, beyond-horizon, and overdrawing plans.
- Oracle earliest-date propagation is preserved for installment and spending-change outcomes.
- `output.csv` regenerated for all 250 requests.
- Required `evaluation/usage_report.md` is included inside `code.zip`; the final run used zero external/model calls and zero token/cost usage.
- `code.zip` created with runnable Python modules, README, and `evaluation/usage_report.md`; `__pycache__` excluded.
- Final local suite: 248 passed, 1 skipped.
- Artifact status: `output.csv`, `code.zip`, `evaluation/usage_report.md` inside the archive, and `log.txt` are present and validated.
- OCR availability: native Tesseract 5.5.3 is installed at `D:\Tesseract_OCR`; `main.py` now discovers/configures it through environment, PATH, standard Windows location, or registry metadata.
- Spec limitation: the linked-image amount path is now wired end-to-end through `images.csv`, Tesseract, contextual amount parsing, provenance, and oracle input. On the current dataset/engine run, 1 of 16 blank-amount events resolved automatically; 15 remain ambiguous or low-quality OCR and conservatively fail closed. No blank amount is treated as zero.
- Explanation limitation: the LLM adapter is implemented with grounding validation, environment-only credentials, deterministic fallback, usage reporting, and a 50-call safety cap. OpenRouter model discovery confirmed the requested display name `Ling 3.0 Flash Fin` maps to provider id `inclusionai/ling-3.0-flash-fin`; a bounded provider smoke path reached the API, but the 10-call batch stalled and was terminated before producing a provider-backed final artifact. The final local run therefore used deterministic fallbacks for all 250 explanations and records 0 final-run model calls.
- Quality limitations (final): `image_14` remains unresolved (fail-closed, may over-classify the affected request); the final artifact uses zero live provider calls, so explanations are deterministic fallbacks (usage report records this explicitly); sample-exact optimization remains deferred (71 style diffs vs gold samples; no validity-gate failures).
- Review outcome: two independent post-implementation reviews found critical evidence, grounding, usage, and packaging issues; the fix round added regression coverage, corrected those issues, rebuilt `code.zip`, and restored the full suite to green.
- Provider-call safety: the configured batch cap was 10, below the user-specified 50-call limit; no API key or credential was written to the repository or report.
- Overall status: the hybrid architecture is integrated and submission artifacts are regenerated, but full problem-statement quality is still limited by 15 unresolved image amounts, fallback-only explanations in the current run, and deferred spending-change/sample optimization.

## 6. Final release state (2026-09-13, ~16:10 IST)

All three critical tasks are committed and the final artifacts were regenerated from the final code state:

- Task 1 OCR/evidence: commit `25fa78d` (15 of 16 linked images resolved; `image_14` fails closed).
- Task 2 provider usage/cost accounting: commit `431f53b` (structured usage, 50-call hard cap, timeout/fallback).
- Task 3 spending-change first-class plans: commit `45a766b` (changed plans ranked in the common pool; no-change earliest date preserved).
- Final run: `python code/main.py` with `BUY_WAIT_LLM_MAX_CALLS=0` → `output.csv` 250 rows regenerated; both `evaluation/usage_report.md` and `tools/evaluation/usage_report.md` describe that exact run (0 calls, 0 tokens, cost unavailable→0.00 documented).
- `code.zip` rebuilt after the final run; contains `code/` (15 modules), `README.md`, `evaluation/usage_report.md`; no `.env`, keys, or `__pycache__`.
- Final verification: full suite **276 passed, 1 skipped**; `test_submission.py` + `test_invariants.py` 23 passed against the final artifacts.

### Historical note

An earlier stop instruction paused the wave mid-flight outlines below; Task 1's commit landed after that pause and final packaging was completed in the release state above.

### Critical tasks to execute first

| Priority | Workstream | Agent split | Root cause | Revised estimate |
|---|---|---|---|---:|
| Critical 1 | OCR/evidence recovery | Popper (`01a09a21-c70a-79b2-a953-e01b39da0106`): stopped before commit/report; `code/ocr.py`, `code/evidence.py`, focused OCR tests remain unresolved | Generic full-page OCR and narrow labels reject most valid document layouts; number-word and document-family extraction are missing. | 60–90 min; paused |
| Critical 2 | Provider usage/cost accounting | Hegel (`01a09a21-c6ac-7903-8bbd-48e03de2ace3`): commit `431f53b`; report recorded | Provider usage metadata is now retained and capped, but the bounded live smoke failed, so successful nonzero live usage is not demonstrated. | 35–50 min; implementation complete, review pending |
| Critical 3 | Spending-change integration | Epicurus (`01a09a21-c754-7e83-b1cc-1c7b2de89514`): commit `45a766b`; report recorded | Spending changes now enter the common ranking pool, subject to independent review and final artifact verification. | 60–90 min; implementation complete, review pending |
| Critical 4 | Release verification | Controller plus two independent reviewers: not started | Every critical fix can change output and usage metadata; artifacts must be regenerated and checked together. | 25–35 min; paused |

### Multi-agent execution protocol

- Use the current model for all implementers and reviewers; do not change models.
- Agents A, B, and C were dispatched after approval; their write scopes were disjoint. Task 1 was shut down at the user's stop instruction.
- Each agent must use RED → verify RED → minimal GREEN → targeted tests → self-review.
- Launch two independent reviewers after the implementation wave: one for OCR/evidence/oracle safety and one for provider accounting/solver/package compliance. This was intentionally not started before the stop instruction.
- Any Critical or Important review finding gets one scoped fix round and re-review before packaging.
- The controller owns integration, final full-suite execution, output regeneration, and `plan_a.md` updates.

### Revised total estimate

- Revised critical path from execution start: **3h 00m–4h 45m** including implementation, two reviews, targeted fix/re-review if required, and final packaging.
- Optional sample-exact optimization: **45–75m** after the critical path.
- Current status: **paused; not submission-ready**. The required reviews, final full run, archive verification, and OCR recovery remain outstanding.
