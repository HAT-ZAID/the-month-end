# Architecture

Two layers, one boundary. The deterministic judge owns every number; the
optional agentic analyst may only read and propose. Everything runs locally;
remote models are opt-in.

```text
dataset/*.csv + media/images
        |
        v
 evidence pipeline          messages, images, OCR text -> facts, warnings
 (parser, reconcile,         (untrusted input; conflicts resolved by the
  evidence, ocr, fx)          documented hierarchy, never guessed)
        |
        v
 forecast / oracle          90-day daily ledger, pending debits reserved,
 (forecast, recurrence,      essential variable spending reserved, FX and
  oracle)                    status fail-closed
        |
        v
 plan search                 full / partial / installments / wait, plus
 (options, changes,          exhaustive 1-3 stop/reduce actions, each
  solver, ranking)           candidate re-projected and ranked
        |
        v
 kernel row (seven machine columns)  ----------------------+
        |                                                    |
        v                                                    |
 optional agent loop: 7 typed tools -> propose_decision       |
 (agent, tools, rag, store)                                    |
        |                                                     |
        v                                                     |
 deterministic verifier: agree, else discard  -----------------+
        |
        v
 explanation (deterministic fallback or validated prose)
        |
        v
 output.csv / TUI / traces
```

## Layers

### 1. Deterministic judge (authoritative)

| Module | Role |
|---|---|
| `code/amounts.py` | Locale-aware money parsing shared by OCR, messages and reconciliation, plus `to_decimal`, the single scalar-to-`Decimal` coercion used across the kernel |
| `code/fx.py` | Exact-date FX, else nearest prior snapshot, else unknown (never an invented cross rate) |
| `code/parser.py` | Salary and amendment facts extracted from messages |
| `code/reconcile.py` | Conflict order: explicit cancel/settle/amend → newer same-source → settled → safer |
| `code/evidence.py` | Resolves blank event amounts from linked images, or flags them closed |
| `code/ocr.py` | Tesseract probe and text extraction; `OCR_UNAVAILABLE` instead of invented figures |
| `code/recurrence.py` | Recurrence only with ≥3 observations and low variance |
| `code/forecast.py` | 90-day projection: recurring commitments, monthly essential budget, confirmed income |
| `code/oracle.py` | Daily balances, pending-debit reservation, minimum-balance floor, safe amount and earliest date |
| `code/options.py` | Parses and filters seller payment options against user preferences |
| `code/changes.py` | Exhaustive 1–3 action spending-change search, re-validated by the oracle |
| `code/ranking.py` | Six-rank comparator: completion date → no changes → total cost → earlier start → fewer payments → option id |
| `code/solver.py` | Per-request pipeline and candidate plans |
| `code/main.py` | Batch entry point and `run_pipeline(...)` for the evaluator |

The judge is pure Python and `Decimal` arithmetic. Given the same inputs it
produces the same seven machine columns, which is what the determinism gate
checks.

### 2. Agentic analyst (optional, non-authoritative)

| Module | Role |
|---|---|
| `code/store.py` | SQLite canonical store of requests, profiles, events, options, messages and FTS5 evidence chunks |
| `code/rag.py` | Hybrid retriever: local vector search first, SQLite FTS5 when embeddings are unavailable |
| `code/tools.py` | Twelve typed tools plus the `Toolbox`; user-scoped arguments only |
| `code/agent.py` | Bounded LangChain tool-calling loop, run-wide call cap, per-turn timeout, repair turns, replay/offline paths |
| `code/intent.py` | Instruction classification: model first, deterministic rules always, source recorded |
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
| `decision_explanation` | the candidate must agree with the kernel row on every machine field, and then every amount, date, status/method name and spending-change action in its prose must be reproduced by the kernel row, the supplied request facts (requested amount, minimum balance) or the profile facts it was shown (`code/claims.py`) |
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

### 3. Interfaces

| Surface | Entry point | Writes |
|---|---|---|
| Batch | `python -m code.main` | `output.csv`, usage report |
| Single-request demo | `python -m code.agent --request-id ID --provider KIND` | one JSON trace when `--trace-dir` is set |
| TUI | `python -m tools.tui` (`ask <instruction>` for tasks) | `traces/tasks/*.json` only |
| Task engine | `python -m code.task_agent "<instruction>"` | one task trace |
| Trace viewer | `python -m tools.trace_view --trace-dir DIR --out PAGE` | static HTML |
| Gates | `python -m evaluation.run --mode pr` | `evaluation/report.{json,md}` |
| Store | `python -m code.store --rebuild` | `data/app.db` |

## Invariants

1. The seven machine columns are a function of the inputs, not of a model.
2. The projected balance never drops below `minimum_balance_to_keep` in a
   recommended plan, including later essential expenses.
3. Pending credits, bonuses, refunds and unrealized investment value never count
   as spendable cash.
4. Unresolved evidence, unknown FX, or an unsafe over-claim fails closed with a
   warning rather than a guess.
5. Untrusted text in messages or images can never grant permissions, change the
   rules, or widen a tool scope.
6. Traces never contain API keys or raw credentials.

## Evaluation gates

`evaluation/run.py` implements eight gates: G0 structural, G1 safety, G2 sample
quality, G3 determinism, G4 provenance, G5 archive, G6 security, G7 agent
robustness. PR mode requires G0–G4 and leaves the release-only gates
`NOT_APPLICABLE`. G2 measures semantic agreement with the 25 public sample rows
and is currently failing; see the limitations section of the README.
