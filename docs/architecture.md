# Architecture

Two layers, one boundary. The deterministic judge owns every number; the
optional agentic analyst may only read and propose. Everything runs locally;
remote models are opt-in.

```text
mock_dataset/*.csv + media/images
        |
        v
 evidence pipeline          messages, images, OCR text -> typed facts, warnings
 (parser, facts,            (untrusted input; conflicts resolved by the
  reconcile, evidence,       documented hierarchy, never guessed)
  ocr, vision, fx)
        |
        v
 forecast / oracle          90-day window as a 91-point daily ledger; pending
 (recurrence, forecast,     debits reserved, essential spending reserved, FX and
  oracle)                    status/date/direction fail closed
        |
        v
 plan search                full / partial / installments / wait, plus an
 (options, changes,          exhaustive 1-3 stop/reduce search, each candidate
  ranking, solver)           re-projected and ranked
        |
        v
 kernel row (seven machine columns) -----------------------+
        |                                                   |
        v                                                   |
 optional agent loop: fourteen typed tools -> proposal      |
 (agent, tools, rag, store, chat)                           |
        |                                                   |
        v                                                   |
 deterministic verifier: agree, else discard ---------------+
        |
        v
 explanation (deterministic fallback or validated prose)
        |
        +--> output.csv (batch)
        +--> code.service -> tools.app / tools.tui
        +--> code.scenarios (deep-copied hypothesis, never persisted)
        +--> traces -> tools.trace_view
```

## Layers

### 1. Deterministic kernel (authoritative)

| Module | Role |
|---|---|
| `code/amounts.py` | Locale-aware money parsing shared by OCR, messages and reconciliation, plus `to_decimal`, the single scalar-to-`Decimal` coercion used across the kernel |
| `code/fx.py` | Exact-date FX, else nearest prior snapshot, else unknown (never an invented cross rate); a prior snapshot is flagged `FX_UNKNOWN` and fails closed |
| `code/parser.py` | Message parser entry point: deterministic evidence extraction |
| `code/facts.py` | Typed evidence facts from messages; pending income, transfers, valuation moves and embedded instructions never become cash |
| `code/reconcile.py` | Conflict order: explicit cancel/settle/amend → newer same-source → settled → safer |
| `code/evidence.py` | Resolves blank event amounts from linked images, or flags them closed |
| `code/ocr.py` | Tesseract probe and text extraction; `OCR_UNAVAILABLE` instead of invented figures |
| `code/vision.py` | Qwen3-VL, Docling and Tesseract behind one local image interface, with a second-witness check for model-proposed amounts |
| `code/recurrence.py` | Recurrence only with ≥3 observations and a low-variance gate |
| `code/forecast.py` | 90-day projection policy: recurring commitments, monthly essential budget, confirmed income; the corrected first-bucket arithmetic was removed and deferred |
| `code/oracle.py` | Daily 91-point ledger, pending-debit reservation, minimum-balance floor, safe amount and earliest date; fails closed on any row it cannot value |
| `code/options.py` | Parses and filters seller payment options against user preferences |
| `code/changes.py` | Exhaustive 1–3 action spending-change search, re-validated by the oracle |
| `code/ranking.py` | Six-rank comparator: completion date → no changes → total cost → earlier start → fewer payments → option id |
| `code/solver.py` | Per-request pipeline and candidate plans; `position(...)` is the only place a position figure is computed |
| `code/explanations.py` | Deterministic explanation, plus the optional one-shot explanation provider with redirect refusal |
| `code/schema.py` | Typed decision contract and candidate parser shared by the agent layer |
| `code/main.py` | Batch entry point and `run_pipeline(...)` for the evaluator; `write_output=False` keeps the gates from rewriting the deliverable |

The judge is pure Python and `Decimal` arithmetic. Given the same inputs it
produces the same seven machine columns, which is what the determinism gate
checks.

### 2. Product and service layer

| Module | Role |
|---|---|
| `code/paths.py` | Fixture resolution: `mock_dataset/` first, `dataset/` fallback |
| `code/account.py` | A user's own account shape: vocabularies, `account_data()`, `problems()` |
| `code/accounts.py` | Named users in SQLite (`data/accounts.db`, gitignored): create/load/save/list/delete |
| `code/service.py` | The engine the interface talks to: `position`, `decide`, `buy`, `add_fact`, `history`, `update_profile` |
| `code/scenarios.py` | What-if scenarios on a `copy.deepcopy` of an account: `pay_on`, `delay_days`, `change_expense`; returns `persisted: False` and never mutates the caller |
| `code/models.py` | Local model discovery (Ollama on loopback only), `provider_for`, free-tier labelling |
| `code/cache.py` | Content-addressed disk cache for OCR/VLM work, with TTL sweeping |
| `code/usage.py` | Provider-call counters, token counts and cost estimation |

### 3. Agentic analyst (optional, non-authoritative)

| Module | Role |
|---|---|
| `code/store.py` | SQLite canonical store of requests, profiles, events, options, messages and FTS5 evidence chunks (`data/app.db`) |
| `code/rag.py` | Hybrid retriever: local vector search first, SQLite FTS5 when embeddings are unavailable |
| `code/tools.py` | Fourteen typed tools plus the `Toolbox`; user-scoped arguments only |
| `code/agent.py` | Bounded tool-calling loop, run-wide call cap, per-turn timeout, repair turns, replay/offline paths |
| `code/chat.py` | The conversation surface: a model chooses among six kernel-backed tools; an honest `NoModel` when no provider is configured |
| `code/intent.py` | Instruction classification: deterministic rules first, model only for an unreadable clause, source recorded |
| `code/claims.py` | Claim check that decides whether a model rationale may ship |
| `code/verifier.py` | Recomputes the kernel row; ships a candidate's prose only when the candidate agrees and its claims are supported |
| `code/task_agent.py` | Conversational task engine (`ask`): parse, decompose, act through the kernel, trace |
| `code/providers.py` | Provider factory: `none` (default), `replay`, `ollama`, `openrouter`, `kilo`, `paid`; every provider declares a privacy class |
| `code/trace.py` | Per-request JSON traces with secret redaction |

#### What the model is allowed to contribute

The loop accepts a candidate's prose only when the candidate agrees with the kernel
row; everything the model contributes on top of that is still guarded. The
contribution paths today:

| Path | Guard |
|---|---|
| `decision_explanation` | the candidate must agree with the kernel row on every machine field; then every amount, date and action in its prose must be reproduced by the kernel row for every shape the claim check recognises; an unrecognised phrasing is a residual gap, the supplied request facts (requested amount, minimum balance) or the profile facts it was shown; an explanation that discusses the outcome must name the row's own status and method, and every event id it mentions must be one the row acts on; and a statement about the earliest full-payment date must equal that column. Rejection means the kernel's text ships, so an unrecognised wording costs an explanation, never a figure (`code/claims.py`) |
| `spending_changes_needed` | `propose_spending_change` is accepted only when the kernel re-solve raises the safe amount and the event is permitted and unprotected; the shipped row keeps the kernel's value, so an accepted proposal is advice, not an edit |
| Intent / routing | `code/intent.py`; a wrong reading costs a turn, never a number |
| Concerns | `raise_concern` writes to the trace only |
| Task actions | `code/task_agent.py`; session-only and reversible |

Every other machine field stays kernel-only. A rejection is feedback: schema
errors and ledger disagreements are returned to the model with the exact fix and
retried up to `BUY_WAIT_AGENT_REPAIR_TURNS` (default 2, max 5).

The loop has three independent limits: chat turns per request (`MAX_PROVIDER_CALLS
= 6`), tool calls per request, and provider calls per run
(`BUY_WAIT_LLM_MAX_CALLS`, default 50, counted before each call so failures still
spend budget). Any exception in the agent, tool, trace or provider path is caught;
the request keeps its kernel row.

**Providers.** All are lazy and env-driven; no SDK import happens at module import
time.

| Kind | Model source | Credentials | Notes |
|---|---|---|---|
| `none` | — | none | Default; zero calls |
| `replay` | fixtures | none | Deterministic recorded candidates for tests |
| `ollama` | `BUY_WAIT_OLLAMA_MODEL` | none | Local; default model `qwen2.5:1.5b` |
| `openrouter` | `BUY_WAIT_LLM_MODEL` | `BUY_WAIT_LLM_API_KEY` or `OPENROUTER_KEY` | Opt-in remote |
| `kilo` | `BUY_WAIT_KILO_MODEL` (`nemotron-3-ultra`) | `BUY_WAIT_KILO_API_KEY` + `BUY_WAIT_KILO_BASE_URL` | Deployment target; OpenAI-compatible; no OpenRouter fallback |
| `paid` | `BUY_WAIT_PAID_MODEL` | `BUY_WAIT_PAID_API_KEY` + `BUY_WAIT_PAID_BASE_URL` | A paid endpoint the user names; https or loopback http only |

### 4. Interfaces and entry points

| Surface | Entry point | Writes |
|---|---|---|
| Batch | `python -m code.main` | `output.csv`, usage report |
| Gates | `python -m evaluation.run --mode pr` | `evaluation/report.{json,md}`; never `output.csv` (`write_output=False`) |
| Product UI | `python -m tools.app` | `data/accounts.db` |
| Fallback TUI | `python -m tools.tui` | task traces only when `ask` runs |
| Task engine | `python -m code.task_agent "<instruction>"` | one task trace under `traces/tasks/` |
| Single-request agent | `python -m code.agent --request-id ID --provider KIND` | one JSON trace when `--trace-dir` is set |
| Trace viewer | `python -m tools.trace_view --trace-dir DIR --out PAGE` | static HTML |
| Store rebuild | `python -m code.store --rebuild` | `data/app.db` |
| Cache inspection | `python -m code.cache --stats` | `data/cache/` only on `--purge` |
| G2 replay lab | `python -m tools.g2_probe` | stdout only |
| G2 model lab | `python -m tools.g2_model_mining` | stdout, plus the `--json` path |
| G2 transfer study | `python -m tools.g2_transfer_study` | stdout only |

The three `g2_*` labs replay or mine the 25 labelled rows; they are investigation
tools, not decision sources, and nothing in production imports them. They need
the analysis stack (`numpy`, `pandas`, `scikit-learn`, optional boosters).

### 5. Support and release modules

| Module | Role |
|---|---|
| `evaluation/semantics.py` | Semantic comparison of the machine fields against the sample rows |
| `code/jev.py` | Typed second opinion from the Jev decision model (OpenCode Zen); measurement only, never a decision source |
| `tools/correctness_report.py` | Deterministic structural harness that gate G0 runs as a script |
| `tools/build_release.py` | Deterministic builder for the submission archive `code.zip` |
| `tools/check_eval.py` | Quick manual audit of `output.csv` against the fixture |
| `tools/evaluation/harness.py` | Per-field comparison helper used by the older evaluation path |
| `tools/evaluation/main.py` | Empty placeholder left by the older harness layout; nothing imports it |
| `tools/experiments/compare_agentic.py` | Measurement: deterministic kernel vs agent loop on the same requests |
| `tools/experiments/compare_jev.py` | Measurement: typed Jev decision model agreement with the kernel |
| `tools/experiments/compare_ocr.py` | Measurement: local image readers against the linked images |
| `tools/experiments/verify_samples.py` | Semantic sample verifier |

## The safety contract

The oracle never guesses. A ledger that cannot be valued returns a fail-closed
payload with `safe_amount = 0`, no earliest date and no synthetic ledger, and the
solver turns that into a conservative row whose explanation names the reason.

| Reason | Trigger |
|---|---|
| `DIRECTION_UNKNOWN` | An active cash row's `direction` is blank or not a known direction |
| `STATUS_UNKNOWN` | An active row's `status` is blank or unrecognised |
| `DATE_UNKNOWN` | An active cash row has neither a parsable `settlement_date` nor `event_date` |
| `AMOUNT_INVALID` | A blank, signed-negative or non-finite amount, or an FX/accumulation overflow |
| `FX_UNKNOWN` | No rate on or before the settlement date for the row's currency pair |
| `RESERVE_INVALID` | `minimum_balance_to_keep` is blank, non-finite or unparsable |
| `BALANCE_INVALID` | `current_available_balance` is blank, non-finite, unparsable, or overflows the projection |

`tests/test_oracle_contract.py` pins each named reason. `code/solver.py` also
refuses a non-finite forecast before any candidate is ranked, and `code/service.py`
refuses a position over an unknown balance instead of reporting zero.

## The deferred forecast corrections

`code/forecast.py` ships the conservative first-bucket charge. The corrected
rules (charge only the unspent remainder of the request month; prorate on the
real month length excluding the request day) lower the day-0 charge and
therefore raise the safe amount. Measured against the labelled rows they
produced 2 status upgrades and 15 safe overclaims, so they were **removed from
the code** and deferred, not left behind a default-off environment switch: a
switch inside the shipped decision path made `output.csv` bytes depend on the
shell. The shipped arithmetic keeps G1 at **0 status upgrades / 0 safe overclaims
/ 0 unparsed amounts**. The third rule — the incomplete request month is not a
budget month — is G1-neutral and ships. The corrected arithmetic must not return
until a safety-margin design resolves the G1 conflict.

## Invariants

1. The seven machine columns are a function of the inputs, not of a model.
2. The projected balance never drops below `minimum_balance_to_keep` in a
   recommended plan, including later essential expenses.
3. Pending credits, bonuses, refunds and unrealized investment value never count
   as spendable cash.
4. Unresolved evidence, unknown FX, an unknown direction/status/date, an invalid
   reserve or balance, or an overflow fails closed with a warning rather than a
   guess.
5. Untrusted text in messages or images can never grant permissions, change the
   rules, or widen a tool scope.
6. A scenario is simulated on a deep copy and is never persisted.
7. Traces never contain API keys or raw credentials.

## Evaluation gates

`evaluation/run.py` implements eight gates: G0 structural, G1 safety, G2 sample
quality, G3 determinism, G4 provenance, G5 archive, G6 security, G7 agent
robustness. PR mode requires G0–G4 and leaves the release-only gates
`NOT_APPLICABLE`. It invokes `code.main.run_pipeline` with `write_output=False`,
so a gate run reads the fixture and writes reports but never rewrites the
deliverable.

G2 measures semantic agreement with the 25 public sample rows and is currently
failing. It is an unresolved external contract, not a tuning target: no tested
configuration reproduced a sample row whose gold amount is strictly between zero
and the requested amount. See `docs/g2-hypothesis-report.md` and the limitations
section of the README.
