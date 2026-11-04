# Buy or Wait?

**Know what you can actually afford — before you commit.**

Most budgeting apps tell you what you *spent*. Buy or Wait? answers the question
you actually ask at the checkout: **can I pay for this right now without
breaking the rest of my month — and if not, what is the smartest way to do it
anyway?**

Ask it about a laptop, a trip, a rent payment or an emergency repair. It reads
your balance, your recurring bills, your payday and the payment options the
seller offers, then gives you one clear answer: pay in full, pay part now, take
installments, wait for a date, or don't do it.

---

## Why it exists

A language model alone will happily invent a number that sounds right. A
spreadsheet alone can't read "the trip is IDR 46,018,000 and I need it done by
10 October". Buy or Wait? refuses to choose between the two.

**The model investigates and explains. Python decides.** Every machine-readable
field — the safe amount, the status, the plan, the date, the spending changes —
comes from a deterministic kernel that you can test, replay and audit. The model
can suggest, clarify and narrate; it never gets to move the number.

That split is the whole product.

---

## What it decides

For every request it produces one row:

| Field | Meaning |
|---|---|
| `amount_safe_to_pay` | What you can pay today, between 0 and the full request |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable` |
| `recommended_payment_method` | `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended` |
| `payment_plan` | Dated payments you can actually keep |
| `earliest_date_for_full_payment` | The first conservative date a clean full payment works |
| `spending_changes_needed` | Up to three `stop:` / `reduce_to:` actions, never on protected spending |
| `decision_explanation` | Why, grounded in the figures above |

It respects the things people care about: a minimum balance you refuse to go
below, protected categories like rent and education, the payment methods you
will actually consider, and the installments the seller actually offers.

---

## How it works

```
profile + events + FX + options + evidence
        │
        ▼
   deterministic kernel            ← owns every machine field
   facts → forecast → options
        → solver → verifier
        │
        ▼
   agent layer                     ← proposes, explains, asks, never calculates
        │
        ▼
   verified answer
```

- **Facts** are reconstructed from structured profiles and financial events:
  settled, pending, scheduled, cancelled, linked.
- **Forecast** projects only spending the history supports, at the cadence the
  history shows. Income is projected from a real salary stream, on its real
  payday.
- **Options** are the seller's actual plans, never invented.
- **Solver** searches and ranks plans; **verifier** rebuilds the row from the
  kernel and accepts model prose only when it agrees and its claims check out.
- **Agent** tools are an explicit allowlist. There is no shell, filesystem or
  network tool in the answer path.

---

## Quickstart

```bash
pip install -r requirements.txt        # deterministic core
python -m code.main                    # decide every request, write output.csv
```

That path needs no model, no key and no network. It is the same answer every
time you run it.

Want the interactive product?

```bash
pip install -r requirements-agent.txt  # adds Textual + LangChain providers
python -m tools.app                    # the desktop-style app
python -m tools.tui                    # the terminal version
```

### Other entry points

| Command | What it does |
|---|---|
| `python -m code.main` | Batch decision over `mock_dataset/requests.csv` → `output.csv` |
| `python -m tools.app` | Product interface: accounts, facts, decisions, chat, scenarios |
| `python -m tools.tui` | Terminal REPL: list, show, whatif, changes, apply, undo, ask |
| `python -m code.task_agent "<instruction>"` | One plain-language task over the kernel |
| `python -m evaluation.run --mode pr` | Release gates G0–G7 |
| `python -m tools.trace_view` | Render a trace |
| `python -m tools.g2_probe` | Replay lab for the sample-quality investigation |
| `python -m tools.g2_model_mining` | Model zoo and SHAP analysis over the sample rows |
| `python -m tools.g2_transfer_study` | Train on the 25 labelled rows, compare against the 250 real ones |

---

## Runs offline, by default

The batch path is offline and keyless. Install from `requirements.txt`, run it,
and the answer is the same every time — no model, no network, no account.

```bash
pip install -r requirements.txt
python -m code.main
```

The container image is authored but **not built on this development machine**
(disk constrained), so treat it as unverified:

```bash
docker build -t buy-or-wait .
docker run --rm buy-or-wait
```

## Deploying with Kilo (optional)

For a hosted deployment, Kilo serving Nemotron Ultra (`nemotron-3-ultra`) is the
trodden path. Nothing is bundled and no endpoint is committed — **you must**
supply the endpoint, key and model yourself:

```bash
BUY_WAIT_KILO_BASE_URL=...   # your OpenAI-compatible endpoint
BUY_WAIT_KILO_API_KEY=...    # never committed
BUY_WAIT_KILO_MODEL=nemotron-3-ultra
BUY_WAIT_LLM_PROVIDER=kilo
```

## Reading receipt images locally

Some financial events arrive as photos with no typed amount. The local vision
path reads them on your machine, with three backends chosen by
`BUY_WAIT_OCR_BACKEND`:

- **qwen3-vl** through Ollama (the default local reader),
- **Docling** for structured documents,
- **Tesseract** as the plain-text fallback.

Measured coverage on the calibration fixture: the local readers resolve 15 of 16
images (15/16). The remaining one stays unresolved by design — the app reports a
missing amount and refuses to decide on it rather than inventing a number.

## The product surface

The app is built around one idea: **change a fact, see the decision move.**

- Record income, bills and subscriptions with a real category and cadence, so
  the forecast actually uses them.
- Ask "what if I wait until the 20th?" and get a simulated answer on a throwaway
  copy — never a silent edit to your ledger.
- Ask a follow-up in plain language. The reply is verified against the kernel
  before you see it; if it cannot be verified, the app says so instead of
  guessing.
- Press **"What facts did you use?"** to see the exact tool results behind the
  last answer.
- Long model calls run in a cancellable worker, so the window never freezes.

---

## Privacy is a product decision, not a setting

Every provider declares one of three classes:

| Class | What it means | What it may see |
|---|---|---|
| `local` | Ollama on your machine | anything |
| `paid` | paid endpoint, no-training terms | your text, never secrets |
| `free` | free tiers that train on submissions | development fixtures only |

- Free tiers trigger a plain warning — **before** anything is sent — and a hosted
  provider needs your explicit consent for each question, for that specific
  model.
- Provider HTTP clients ignore `HTTP_PROXY`/`HTTPS_PROXY` by design, so a proxy
  cannot quietly receive your prompt or API key.
- Secrets live in the environment. Traces record provider, model and token
  counts — never credentials, and never the text of a typed instruction.

---

## The safety contract

The kernel fails **closed**. A malformed row never makes you look richer:

| Situation | Result |
|---|---|
| Unknown or blank direction | `DIRECTION_UNKNOWN`, safe 0 |
| Negative or non-finite amount | `AMOUNT_INVALID`, safe 0 |
| Invalid, non-finite or negative minimum balance | `RESERVE_INVALID`, safe 0 |
| Active cash row with no usable date | `DATE_UNKNOWN`, safe 0 |
| Missing, stale or non-positive FX rate | `FX_UNKNOWN`, safe 0 |
| Arithmetic overflow anywhere in the ledger | fails closed, never raises |

And the rule that outranks everything: **your balance never drops below the
minimum you said you want to keep** after any projected essential expense or
payment in the recommended plan.

---

## Evaluation, honestly

`python -m evaluation.run --mode pr` runs eight gates:

The gate scores the committed snapshot and does not rewrite `output.csv`; provenance is read from the file on disk.

| Gate | Checks |
|---|---|
| G0 | Structure, contracts, no secrets in the tree |
| G1 | Safety: no status upgrades, no safe overclaims, no unparsed amounts |
| G2 | Agreement with the 25 public sample answers |
| G3 | Two clean runs produce byte-identical machine columns |
| G4 | Provenance: commit, output and usage hashes |
| G5–G7 | Archive, security and agent robustness (release mode) |

Current status: **G0, G1, G3 and G4 pass. G2 fails.** The 25 sample answers were
produced by the challenge organisers' reference implementation, and roughly 250
scored forecast variants — including a fifteen-model zoo and two independent
reviews — could not reproduce a single informative gold amount from the data this
repository holds. We report that gap and did not fit the sample, because a model
that memorises 25 answers would tell you nothing about your money.

Full evidence: [`docs/release-verification.md`](docs/release-verification.md) and
[`docs/g2-hypothesis-report.md`](docs/g2-hypothesis-report.md).

---

## Bring your own model

Nothing is bundled. Point it at whatever you trust:

```bash
# local, no key
BUY_WAIT_LLM_PROVIDER=ollama BUY_WAIT_OLLAMA_MODEL=qwen2.5:1.5b

# a paid endpoint you control
BUY_WAIT_LLM_PROVIDER=paid BUY_WAIT_PAID_BASE_URL=... BUY_WAIT_PAID_API_KEY=... BUY_WAIT_PAID_MODEL=...

# OpenRouter or Kilo
BUY_WAIT_LLM_PROVIDER=openrouter BUY_WAIT_LLM_MODEL=...
BUY_WAIT_LLM_PROVIDER=kilo BUY_WAIT_KILO_BASE_URL=... BUY_WAIT_KILO_API_KEY=...
```

No provider? The kernel still answers; the app simply says there is no model and
keeps the deterministic tabs working.

---

## Simulate before you commit

```python
from code.scenarios import Scenario, simulate_scenario, compare_scenarios

result = simulate_scenario(account, Scenario(kind="delay_days", by=14))
# result["kernel_result"], result["assumptions"], result["persisted"] is False
```

Scenarios run on a deep copy through the same kernel, refuse to invent income or
strip protected bills, and never write to storage.

---

## Layout

```
code/          deterministic kernel + agent, chat, providers, accounts, scenarios
tools/         app (Textual), tui, trace viewer, G2 analysis labs
evaluation/    gates G0–G7 and the PR report
mock_dataset/  calibration fixture — never your money, never edited by the app
docs/          architecture, handover, G2 investigation, release verification
tests/         the release gate; tests/review/ is the wider net
```

Read [`docs/architecture.md`](docs/architecture.md) for the module map and
[`AGENTS.md`](AGENTS.md) for the rules this repository is built to.

---

## Running it in the cloud

The deterministic kernel is deployed on **AWS** as a Lambda function URL in
`ap-southeast-2` (account `316658412595`), packaged by CI and capped by a
**$10/month budget** (`month-end-monthly-10`):

```
https://73rb5yi7skfyh5y2ivmkay2ivi0pvihx.lambda-url.ap-southeast-2.on.aws
```

Every route needs a bearer token held in the function's environment, never in
this repo: `GET /health`, `GET /sample`, `GET /sample/{id}`, `POST /evaluate`,
and `GET /explain/{id}` (kernel decision, then a plain-language rewrite from
Kilo's free pool — no API key — accepted only if every figure it uses already
appears in the decision).

Lambda was chosen over Fargate because Fargate idles at roughly $11/month plus a
public IP, and an ALB alone is about $16/month — both would break the budget.
Lambda scales to zero and fits the free tier.

CI (`.github/workflows/ci.yml`) runs the release suite, the review suite and the
evaluation gates; it fails only on the gates that must stay green (G0, G1, G3,
G4), with G2 reported as the documented external gap. CD
(`.github/workflows/deploy.yml`) builds the Linux package and publishes it;
the final `aws lambda update-function-code` is a documented one-liner because
the account's SCP denies `iam:CreateOpenIDConnectProvider`, so GitHub cannot
assume a role without long-lived keys.

The agent loop runs client-side: a public chat endpoint would be a model call
per request behind nothing but that token.

## Status

- Deterministic core: stable, 717 tests green, 503 in the wider review suite.
- Interactive product: accounts, facts, decisions, verified chat, scenarios,
  provider consent and cancellations.
- Known gaps and deferred work are tracked as issues: the G2 reference gap, the
  deferred forecast corrections, provider cancellation semantics, durable memory
  and the fuller orchestration layer.

Do not use this as an unattended financial decision-maker. It is decision
support: it tells you what the numbers say, and shows its work.
