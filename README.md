# Buy or Wait?

A local-first assistant that answers one question before you spend money:
**can I safely pay for this?**

You keep your own data as CSV files: balances, recurring bills, salary dates,
payments already scheduled, and the things you refuse to cut. The assistant
projects your cash flow forward, then tells you whether to pay now, pay part of
it, use installments, wait, or not proceed at all — and shows the arithmetic
behind the answer.

Nothing leaves your machine unless you explicitly configure a remote model. The
decision itself is plain Python; a language model can add narration, never a
different answer.

## Origin

This started as a submission to **HackerRank Orchestrate (September 2026)** and
the "Buy or Wait?" brief: decide, for each request in `dataset/requests.csv`,
whether the user should pay in full, pay partially, take installments, wait, or
decline, never letting the projected balance fall below the user's minimum. The
competition scaffolding is still in the repository (the dataset, the eight-column
export, the evaluation gates), but the product is now meant to be used
day to day, offline, on your own data.

### How that submission scored

**9/10 on the chat transcript.** That signal was graded against four criteria:

| Criterion | Weight |
|---|---|
| Direction and architecture ownership | 35% |
| Technical specificity and constraint | 25% |
| Iteration and verification | 25% |
| Safety and edge-case awareness | 15% |

## What it produces

Every decision is one row with eight columns. The first seven are computed in
Python and are identical on every run; only the explanation text can come from a
model, and only when one is configured and its prose passes validation.

| Column | Meaning |
|---|---|
| `request_id` | The request being answered |
| `amount_safe_to_pay` | Largest amount safe on the request date, before optional spending changes (`0 <= x <= requested_amount`) |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable` |
| `recommended_payment_method` | `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended` |
| `payment_plan` | Chronological `YYYY-MM-DD:amount` legs joined by `\|`, or `none` |
| `earliest_date_for_full_payment` | First conservative date a single safe full payment fits; empty when none does |
| `spending_changes_needed` | `none`, or up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` actions |
| `decision_explanation` | Short, grounded reason for the recommendation |

`output.csv` is the audit/export format. The interactive TUI is the everyday
interface.

## How it works

Two layers, with a hard boundary between them:

1. **A deterministic judge** reconstructs your financial position from the CSVs,
   forecasts 90 days of daily balances, and searches full / partial / installment
   / wait plans. It owns every number: safe amounts, earliest dates, plan
   feasibility, spending-change search and plan ranking.
2. **An optional agentic analyst** runs a bounded tool-calling loop over twelve
   typed tools: it reads the request context, searches evidence, projects cash
   flow, compares payment options, reconciles receipts and submits a proposal.
   A deterministic verifier recomputes the kernel row and keeps its own numbers.
   The agent owns three things and nothing else:
   - the **explanation**, shipped only when every amount, date, status and action
     in it is reproduced by the kernel row (`code/claims.py`);
   - a **guarded spending change proposal**: the kernel re-solves it and accepts
     it only when the event is permitted, unprotected and the re-solved plan
     raises the safe amount — but the shipped row keeps the kernel's own
     `spending_changes_needed`, so an accepted proposal is advice, not a change
     to the answer;
   - **concerns** about unreadable or conflicting evidence, which change no number.

   Schema errors and ledger disagreements are fed back to the model with the exact
   fix and retried up to `BUY_WAIT_AGENT_REPAIR_TURNS` (default 2). When that is
   exhausted, or on any provider error, the kernel row stands.

   It can also take plain instructions in the TUI — `ask stop my streaming
   subscription`, `ask can I afford 40,000 on the 12th`, `ask undo` — through the
   same kernel, session-only.

Supporting pieces: RAG retrieval over messages/OCR text with SQLite (FTS5) as the
canonical structured store and fallback retriever, a static HTML trace viewer for
every agent run, and a TUI for exploring, simulating and re-deciding requests
without touching your files.

### Reading images locally

Screenshots (payslips, receipts, bills, transfer confirmations) are read on your
machine, never in the cloud.

| Backend | What it does | Configure with |
|---|---|---|
| `vlm` | Qwen3-VL 2B Instruct through a local Ollama endpoint: text plus a structured amount, currency, date and merchant | `BUY_WAIT_VLM_BASE_URL`, `BUY_WAIT_VLM_MODEL` |
| `docling` | IBM Docling layout-aware markdown, run in its own interpreter | `BUY_WAIT_DOCLING_PYTHON` |
| `tesseract` | Classic OCR, kept as the comparison and last-resort path | `TESSERACT_CMD` |


`BUY_WAIT_OCR_BACKEND=auto` tries Qwen3-VL, then Docling when configured, then
Tesseract. A model-proposed amount is trusted only when it is positive, in the
event's currency, and a deterministic reading of the same document reproduces it
exactly. The model's own transcript counts first; when that transcript yields no
labelled amount, an independent local Tesseract read of the same image acts as the
second witness. If neither confirms the figure, it is discarded and the parser's own
reading wins. A
non-loopback VLM host is refused unless you set `BUY_WAIT_VLM_ALLOW_REMOTE=1`.
Reads are cached on disk by image path, image bytes and backend, so repeat runs do
no model work; entries older than `BUY_WAIT_CACHE_TTL_DAYS` (default 30) are treated
as misses and swept from disk. Inspect or clean the cache with:

```bash
python -m code.cache --stats            # root, TTL, entry count
python -m code.cache --purge --stats    # delete expired entries now
```

Comparison results live in [`docs/ocr-comparison.md`](./docs/ocr-comparison.md).

### Optional JEV decision audit (OpenCode Zen)

`tools/compare_jev.py` is an experiment, not a decision source: it asks the
TypeSafe **Jev** decision model (free `jev-1.13-free` through OpenCode Zen) to
reproduce the kernel's affordability status and payment method as typed
probabilities, and to rate each decision's safety and evidence sufficiency.
The deterministic kernel still owns every machine field; the audit never edits a
row. It needs an OpenCode API key (`OPENCODE_API_KEY`, or the signed-in
`auth.json`), it is the only network call outside optional agent providers, and
the free tier answers `noul` propositions only, which the tool encodes as an
11-proposition argmax. Results live in
[`docs/jev-decision-gate.md`](./docs/jev-decision-gate.md).

See [`docs/architecture.md`](./docs/architecture.md) for the module map and data
flow, and [`docs/edge-case-policy.md`](./docs/edge-case-policy.md) for the
governed edge cases.

## Quickstart

Requires Python 3.11+.

### 1. Install the base dependencies

```bash
python -m pip install -r requirements.txt
```

`pandas` and `rich` are all the deterministic path and the TUI need. Image
reading (`pytesseract`, `Pillow`) is optional and additionally needs a Tesseract
binary at runtime; set `TESSERACT_CMD` if it is not on `PATH`. The local vision
path needs a running Ollama server serving `qwen3-vl:2b-instruct-q4_K_M` (or a
Docling interpreter); neither needs an API key.

### 2. Run the deterministic batch

```bash
python -m code.main            # writes output.csv and the usage report
python -m code.main --help     # usage, no run
```

One row per request, LF line endings, no model calls, no API key. Add
`--agent --agent-limit 3` to run the offline analyst pass over three requests
(needs the optional agent stack and a local Ollama model).

### 3. Explore in the TUI

```bash
python -m tools.tui
```

Everything you do there — `list`, `show`, `dashboard`, `whatif`, `changes`,
`apply`, `diff`, `undo`, `new`, `explain`, `trace`, `ask` — is session-only. The
TUI never writes `output.csv` or `dataset/`.

`ask` is the conversational surface: plain-language instructions, split into
ordered steps, each one executed against the same kernel.

```text
tui> ask why was request_101 refused
tui> ask where am I leaking money, then stop the worst one
tui> ask can I afford 40,000 on the 12th
tui> ask and if I wait until the 20th
```

Each step reports what it did, what changed, and what it refused (with the
nearest safe alternative). `python -m code.task_agent "<instruction>"` runs the
same engine from the terminal, and `--provider ollama --model <id>` lets the
model classify clauses the rules cannot read. Without a provider the rules answer
alone, so nothing here needs a key.

### 4. Run the evaluation gates

```bash
python -m evaluation.run --mode pr
```

This runs the real pipeline, grades the 25 public sample rows semantically,
checks determinism and provenance, and writes `evaluation/report.json` plus
`evaluation/report.md`. It exits non-zero when a required gate fails.

`--mode release` additionally runs **G7**, which breaks the agent loop on purpose
for two requests and requires that the pipeline still finishes, every machine field
matches the deterministic baseline, and each crash is recorded in
`usage["agent_loop_errors"]` and named on stderr. G5/G6 stay `NOT_APPLICABLE` until
an `--archive` is supplied.

### 5. Optional: local agent (Ollama, no key)

```bash
python -m pip install -r requirements-agent.txt
ollama serve
ollama pull qwen2.5:1.5b
python -m code.agent --request-id <request_id> --provider ollama --trace-dir traces/demo
```

`ollama` is the local default and needs no cloud account. Every run is capped at
`BUY_WAIT_LLM_MAX_CALLS` provider calls (default 50).

Local generation settings are tunable, and the defaults stay reproducible
(`temperature=0`, `num_ctx=8192`):

```bash
export BUY_WAIT_OLLAMA_TEMPERATURE=0.0     # raise to explore different wording
export BUY_WAIT_OLLAMA_NUM_CTX=8192
export BUY_WAIT_OLLAMA_TOP_P=0.9           # optional; omitted unless set
export BUY_WAIT_OLLAMA_TOP_K=40
export BUY_WAIT_OLLAMA_REPEAT_PENALTY=1.1
export BUY_WAIT_OLLAMA_NUM_PREDICT=1024
```

Sampling can change what the model *says*, never what the answer *is*: the seven
machine-readable columns always come from the kernel. `propose_decision` takes the
ten candidate fields as flat tool arguments — small local models drop free-form
fields when the payload is nested under a single wrapper key, so the schema is
flat on purpose.

The loop now treats a rejection as feedback instead of a stop condition:

- **Intent first.** Every request is classified (`code/intent.py`) before the
  first model call. The reading is deterministic and free; when a *clause* in a
  task cannot be parsed by the rules, the model classifies it and the trace
  records which reading won (`model` or `deterministic_fallback`).
- **Repair turns.** A schema rejection or a disagreement with the ledger is sent
  back with the exact fix ("you proposed X for `payment_plan`, the ledger computes
  Y; re-read `get_cashflow_projection` and resubmit all ten fields"), bounded by
  `BUY_WAIT_AGENT_REPAIR_TURNS` (default 2, max 5).
- **Claim-checked explanations.** A model's `rationale` ships only when every
  amount, date, status/method name and spending-change action in it is reproduced
  by the kernel row (`code/claims.py`). Wrong about the plan, still allowed to
  explain it; never allowed to invent a number.
- **Guarded spending changes.** `propose_spending_change` lets the model try a
  permitted `stop`/`reduce_to`; the kernel re-solves and accepts it only when it
  actually raises the safe amount. The shipped row still carries the kernel's own
  `spending_changes_needed`: the proposal is a tested recommendation, not an
  edit to the answer.
- **Concerns.** `raise_concern` records unreadable evidence or ambiguity in the
  trace and the task report. It never changes a number.

### 6. Optional: Kilo + Nemotron Ultra deployment

The deployment provider is Kilo's OpenAI-compatible endpoint serving
**Nemotron Ultra**. **You must configure the API yourself** — the repository
ships no endpoint and no key, and `OPENROUTER_KEY` is deliberately not used as a
fallback.

```bash
export BUY_WAIT_KILO_BASE_URL="<your Kilo OpenAI-compatible base URL>"
export BUY_WAIT_KILO_API_KEY="<your Kilo API key>"
export BUY_WAIT_KILO_MODEL="nemotron-3-ultra"      # default; override if needed

# single-request demo
python -m code.agent --request-id <request_id> --provider kilo --trace-dir traces/demo

# batch agent pass (provider selection also via BUY_WAIT_LLM_PROVIDER=kilo)
BUY_WAIT_LLM_PROVIDER=kilo python -m code.main --agent --agent-limit 5
```

If the URL or key is missing the two entry points behave differently, on purpose:

- `python -m code.agent --provider kilo` **exits with an error** naming the missing
  variable. It is a debug entry point, and a silent fallback there would hide a
  configuration mistake.
- `python -m code.main --agent` (and the TUI) **falls back to the deterministic
  kernel** row for that request and records the reason in the trace, so a batch run
  never dies halfway.

The 50-call cap is shared with the other remote providers.

### Docker

```bash
docker build -t month-end:test .
docker run --rm month-end:test --help       # usage
docker run --rm month-end:test              # deterministic batch inside /app
touch output.csv                            # needed before a bind mount: Docker
                                            # would otherwise create a directory
docker run --rm -v "${PWD}/output.csv:/app/output.csv" month-end:test
```

The container runs as uid **10001**, so a host `output.csv` must be writable by that
user (on Linux: `chown 10001 output.csv`, or mount a writable directory and copy the
file out). `code.main` always writes `/app/output.csv`, so a directory mount needs a
code change and is not offered here.
The image is `python:3.11-slim`, installs both requirement files plus Tesseract,
runs as a non-root user, and reads `dataset/` from `/app`. Pass `-e
BUY_WAIT_KILO_BASE_URL=... -e BUY_WAIT_KILO_API_KEY=...` to enable the remote
agent; without them the container is offline and keyless.

**Build status: unverified.** The image definition is checked by the packaging
tests and ships with the repository, but it has not been built here because the
development machine ran out of disk space. Treat the first real build as
unproven and report anything it breaks.

## Data and privacy

- Your data stays in local CSV files (`dataset/`) and a local SQLite database
  (`data/app.db`, rebuildable with `python -m code.store --rebuild`).
- With no provider configured, the assistant makes **zero network calls** and
  needs no credentials. The local Ollama path also stays on your machine.
- Remote providers are opt-in and hard-capped; API keys are read from environment
  variables only and are never written to traces, reports or output.
- Messages and images are **untrusted evidence**. They may confirm, amend, delay
  or cancel a financial fact, but instructions embedded in them are ignored, and
  anything they cannot prove is flagged rather than guessed.
- The pipeline fails closed: unresolved image evidence, unknown FX, or an
  over-claimed amount produces a conservative row with a warning, never an
  invented number.

## Current limitations

- `output.csv` is generated, not golden. The committed file comes from the release
  run (250 rows, sha256 `1f35043f?`) and G4 ties it to that commit; regenerate it
  after any forecast or policy change.
- Semantic agreement with the 25 public sample rows is still partial: the last PR
  run passed the structural and safety gates (G0/G1/G3/G4) but failed the sample
  quality gate (G2), with `recommended_payment_method`, `payment_plan`,
  `earliest_date_for_full_payment` and spending-change rows the main gaps.
- Image reading is local but uneven. On the 16 linked images Tesseract and
  Qwen3-VL 2B both resolve 15 and agree on 14 of those, so the 2B model buys no
  extra coverage here; `image_14` stays unresolved for both. The model did
  propose two wrong amounts (a 40.00 USD receipt and a garbled nine-digit INR
  total), both rejected by the deterministic check, which is why the parser stays
  the authority. A third reader, IBM Docling, resolves 13/16 and agrees with
  Tesseract on only 7 of those — it loses grand totals on dense, itemised
  receipts — so it stays an optional third opinion. See
  [`docs/ocr-comparison.md`](./docs/ocr-comparison.md) and
  [`docs/docling-vs-tesseract.md`](./docs/docling-vs-tesseract.md).
- Live model quality is measured only on small local models. `llama3.2:3b` and
  `qwen3-vl:2b-instruct` both fail to converge on a schema-valid proposal under a
  bounded tool budget, and every request falls back to the kernel. The
  Kilo/Nemotron path is wired but has never been run with real credentials, so no
  remote quality or latency number exists yet.
- The projection horizon is 90 days with fixed, dated exchange rates. It does not
  fetch live rates, bank balances or market data.

## Development

```bash
python -m pytest tests -q                      # full suite
python -m pytest tests/test_providers.py tests/test_packaging.py -q
python -m evaluation.run --mode pr             # required gates
```

- Tests are offline by default: no network, no Ollama server, no API key. Model
  providers are exercised with recording fakes and replay fixtures.
- The machine-readable columns are determinism-checked; explanations are not
  compared byte for byte.
- Every change is reviewed with a scoped CodeRabbit pass before merge, and
  review findings are fixed or documented rather than parked silently.
- Never commit secrets. Use `.env` (gitignored) or your shell; `.env.example`
  carries placeholders only.

## Layout

| Path | Role |
|---|---|
| `code/` | Deterministic kernel, evidence pipeline, agent loop, providers, store, traces |
| `dataset/` | Input CSVs and image evidence; `dataset/output.csv` is the blank template |
| `evaluation/` | Gate runner, semantic comparator, usage report |
| `tools/` | TUI, static trace viewer, sample verifier |
| `tests/` | Pytest suite (offline) |
| `docs/` | Architecture, edge-case policy, plans |

The full problem specification is preserved in
[`problem_statement.md`](./problem_statement.md); the repository rules for
automated agents are in [`AGENTS.md`](./AGENTS.md).
