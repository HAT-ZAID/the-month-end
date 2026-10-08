# Buy or Wait? — HackerRank Orchestrate, September 2026

A deterministic financial decision agent that answers, for every user request,
whether the user can safely pay now, pay later, or should wait.

## Competition context

This repository is a submission for the **HackerRank Orchestrate** 24-hour
hackathon (September 2026) and answers the **"Buy or Wait?"** brief: build an
AI-powered financial agent that decides whether a user can safely afford a
requested expense.

The core question: for each of the **250 requests** in `dataset/requests.csv`,
decide whether the user should **pay in full**, **pay partially**, take
**installments**, **wait**, or **decline** — without ever letting the projected
balance fall below `minimum_balance_to_keep` at any point across a **90-day
cash-flow forecast**. Two users with the same balance can deserve different
answers: recurring commitments, pending payments, confirmed income,
priorities, payment preferences, and permitted spending changes all matter.

Each request produces exactly one output row:

| Column | Meaning |
|---|---|
| `request_id` | The request being answered |
| `amount_safe_to_pay` | Largest amount safe to pay on `request_date` before optional spending changes |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable` |
| `recommended_payment_method` | `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended` |
| `payment_plan` | Chronological `YYYY-MM-DD:amount` entries joined by `\|`, or `none` |
| `earliest_date_for_full_payment` | First conservative projected date for one safe full payment; empty if none in the forecast |
| `spending_changes_needed` | `none` or up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` actions joined by `\|` |
| `decision_explanation` | Concise, grounded explanation of the recommendation |

`0 <= amount_safe_to_pay <= requested_amount` always holds. The full contract
is in [`problem_statement.md`](./problem_statement.md) and AGENTS.md §6.2.

## Scoring axes

`output.csv` is compared against hidden ground truth on six axes, quoted from
`problem_statement.md`:

- accuracy of `amount_safe_to_pay`
- correctness of `affordability_status`
- correctness of `recommended_payment_method` and `payment_plan`
- accuracy of `earliest_date_for_full_payment`
- validity of `spending_changes_needed`
- usefulness and consistency of `decision_explanation`

## Architecture

| Module | Role |
|---|---|
| `code/amounts.py` | Locale-aware amount parsing and currency-coded figure extraction from free text (keeps digits that appear in record IDs out of money figures); shared by OCR, message parsing, and reconciliation |
| `code/fx.py` | FX resolution: exact settlement-date rate, else the nearest prior dated snapshot for the pair, else `None` with an `FX_UNKNOWN` flag — never an invented cross rate |
| `code/recurrence.py` | Recurrence detection: at least three observations plus a conservative coefficient-of-variation gate, so high-variance spending is not extrapolated as committed cost |
| `code/reconcile.py` | Message/event conflict resolution in the spec's order: explicit cancellation/settlement/amendment → newer record from the same source → settled event → financially safer interpretation |
| `code/oracle.py` | The 90-day ledger: 91 daily balance points, pending-debit reservation, FX and status fail-closed gates, category suppression for spending changes; computes `amount_safe_to_pay` and `earliest_date_for_full_payment` under `minimum_balance_to_keep` |
| `code/changes.py` | Exhaustive search over combinations of 1–3 stop/reduce actions on permitted, non-protected, flagged-recurring categories, validated by re-running the oracle |
| `code/options.py` | Parses `request_payment_options.csv` and filters options against the user's payment preferences and `max_installment_months` |
| `code/ranking.py` | Deterministic comparator for the spec's six-rank order: completion date → no changes → total cost → earlier start → fewer payments → lowest option id (the solver applies the same key inline) |
| `code/parser.py` | Extracts salary updates and event amendments from `messages.csv`; `code/solver.py` applies them to the event copy before the oracle runs |
| `code/solver.py` | Per-request pipeline: evidence injection, candidate plans (full / partial / installments / wait), strict per-leg installment safety, spending-change search, spec ranking |
| `code/ocr.py` | Optional Tesseract OCR: probes for a binary, reads linked images, and falls back to a flagged `OCR_UNAVAILABLE` verdict instead of inventing a figure |
| `code/evidence.py` | Resolves blank event amounts from linked images without inventing values; emits `AMOUNT_UNRESOLVED` / `OCR_UNAVAILABLE` / invalid-link warnings that can fail a request closed |
| `code/explanations.py` | Produces `decision_explanation` prose: optional LLM call, schema-validated and grounding-checked against the already-computed decision, deterministic fallback otherwise |
| `code/usage.py` | Token/cost accounting with the 50-call provider ceiling; writes the usage report |
| `code/main.py` | CLI entry point: loads `dataset/`, runs all 250 requests, fails closed on unresolved evidence, writes `output.csv` and the usage report |
| `tools/tui.py` | Interactive TUI over the same solver: session-only edits in memory, never writes `output.csv` or `dataset/`, never calls the LLM provider |

### Why deterministic

Safety arithmetic lives entirely in code: ledgers, safe amounts, earliest
dates, plan feasibility, spending-change search, and ranking are computed with
`Decimal` in plain Python. The model writes prose only — `decision_explanation`
is schema-validated (`_schema_ok`) and grounding-checked (`_grounded`) against
the already-computed decision before it is accepted, and any validation
failure, provider error, or missing configuration falls back to deterministic
text. The LLM never controls a decision field: the seven machine columns are a
pure function of the inputs, so they are identical on every run. Only the
prose in `decision_explanation` can vary when a live provider call is accepted
(`evaluation/usage_report.md` records how many were); with no provider
configured every explanation is the deterministic fallback and the whole file
reproduces byte for byte.

## Running it

### Install

```bash
pip install pandas   # batch pipeline (code/main.py)
pip install rich     # interactive TUI only (tools/tui.py)
```

OCR is optional: with `pytesseract`, `Pillow`, and a Tesseract binary
(see `TESSERACT_CMD` below) the pipeline reads linked images; without them it
continues with flagged `OCR_UNAVAILABLE` and affected rows fail closed.

### Batch run

```bash
python -m code.main    # or: python code/main.py
```

Both entrypoints are equivalent. Dataset paths are anchored to the repository
root, so data is found regardless of where the command runs (`python -m
code.main` still needs the repository root as the working directory so the
`code` package resolves). Each run processes all 250 requests, writes
`output.csv` in the repository root with LF line endings, and writes the usage
report. `python -m code.main --help` prints usage without running the pipeline.

### Interactive TUI

```bash
python -m tools.tui
```

Every edit is **session-only**, held in memory: the TUI never writes
`output.csv` or `dataset/`, and it never calls the LLM provider — `explain`
prints the explanation the solver already computed for that decision.

### Environment variables

| Variable | Purpose |
|---|---|
| `BUY_WAIT_LLM_ENDPOINT` | Chat-completions endpoint used for explanation calls; when only a key is set, the OpenRouter URL is used |
| `BUY_WAIT_LLM_API_KEY` | API key for that endpoint; falls back to `OPENROUTER_KEY` |
| `OPENROUTER_KEY` | OpenRouter API key; its presence alone enables the provider path |
| `BUY_WAIT_LLM_MODEL` | Model used for explanation calls |
| `BUY_WAIT_LLM_PROVIDER` | Provider label recorded in the usage report |
| `BUY_WAIT_LLM_TIMEOUT_SECONDS` | Per-call timeout in seconds |
| `BUY_WAIT_LLM_MAX_CALLS` | Per-run call budget; never exceeds the 50-call ceiling |
| `TESSERACT_CMD` | Path to the Tesseract executable when it is not on `PATH` |

Secrets are read from environment variables only and never appear in code,
reports, or this document. With no key or endpoint configured, the pipeline
makes **zero provider calls** and uses deterministic explanation fallbacks.

### 50-call provider ceiling

A run makes **at most 50 provider calls**: `MAX_PROVIDER_CALLS = 50` in
`code/usage.py` caps any configured `BUY_WAIT_LLM_MAX_CALLS`, and
`code/explanations.py` re-clamps the budget before every call. Timeouts,
malformed responses, and schema/grounding failures consume budget but never
block a request — they fall back to deterministic prose.

### Usage report

Every batch run writes `evaluation/usage_report.md` (mirrored under
`tools/evaluation/`) with provider and model names, call counts, input/output/
total tokens, average tokens per request, and estimated cost — reported as
unavailable rather than as a paid zero when pricing is not configured.
`code.zip` must include `evaluation/usage_report.md` (AGENTS.md §6.5).

### Submission bundle

| Artifact | Contents |
|---|---|
| `code.zip` | Full runnable solution, this README, and `evaluation/usage_report.md` |
| `output.csv` | 250 prediction rows with the exact required columns in order |
| `log.txt` | Uploaded as the `chat_transcript` — the append-only development log, gitignored, never committed |

## Edge-case policy

The seven governed edge cases — missing income, overlapping commitments,
partial installment feasibility, ambiguous spending-adjustment candidates,
spending changes acting on recurrence, unresolved evidence, and unknown FX —
are documented with policy, rationale, and verified `file:line` enforcement
citations in [`docs/edge-case-policy.md`](./docs/edge-case-policy.md).
