# What changed, and why it matters

This document explains the overhaul of `the-month-end` for a reader who does not
work on this codebase every day. It is not a changelog. It explains **what the
system used to get wrong, what it does now, what we measured, and where it is
still weak**.

If you only read one paragraph: the original pipeline produced confident-looking
answers from a forecasting rule that quietly ignored month-end spending, and it
trusted its own OCR. The rebuilt system separates the two jobs that were tangled
together — a language model reads and argues, a deterministic judge decides — and
it fails closed whenever the evidence is not good enough to be safe.

---

## 1. What the product is

You ask: *"Can I safely pay for this?"* — a purchase, a bill, a payment request.

The system reads a synthetic-but-realistic financial profile and a history of
financial events, then answers with eight fields:

| Field | Plain meaning |
|---|---|
| `amount_safe_to_pay` | The largest amount that is safe to pay **today**, before asking the user to change any habits |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later` or `not_affordable` |
| `recommended_payment_method` | Pay in full, pay part now, instalments, wait, or decline |
| `payment_plan` | Dated amounts, e.g. `2026-01-04:1200.00\|2026-02-01:800.00` |
| `earliest_date_for_full_payment` | The first date a single full payment is safe |
| `spending_changes_needed` | Optional `stop:<event>` / `reduce_to:<event>:<amount>` actions |
| `decision_explanation` | A short, grounded reason |

The hard constraint behind every field: **the projected balance must never drop
below the user's `minimum_balance_to_keep`**, on any day of a 90-day forecast,
after any essential expense or scheduled payment.

---

## 2. Where it came from

This repository started as a submission to the **HackerRank Orchestrate**
24-hour hackathon (September 2026), *"Buy or Wait?"*. The competition framing
still shapes the data contract: 250 requests, one row each, exact column names.
The product framing has since moved on — it is now a local-first assistant that
can be pointed at your own data, with no cloud dependency required.

---

## 3. The three bugs that made the first version untrustworthy

These were not style issues. Each one changed answers.

### 3.1 Month-end spending was invisible

The original forecast projected each historical spending pattern forward using
the **day of the month it happened on**. Groceries bought on the 5th were
expected on the 5th; rent on the 28th was expected on the 28th.

That sounds reasonable until a user's spending clusters at the end of the month.
The forecast would place those expenses *after* the 90-day window's inside edge,
or on a single day, and the "safe to pay" amount would come out too high. The
user would be told they could afford something they could not.

**Fix (Task 8).** Essential variable spending is now accrued as a **monthly
budget**: take the historical total per month, use a median (robust to one
unusual month), and spread it evenly across the month. Recurring fixed
commitments still land on their real dates, because those dates are known.

The measured effect: the safety gate went from failing to passing — **0 status
upgrades, 0 overclaimed safe amounts across the 25 labelled sample requests**.
A "status upgrade" means the system said *affordable* where the reference answer
said otherwise. That count is the one that matters: it is the difference between
a cautious assistant and a harmful one.

### 3.2 The OCR result was trusted without a second opinion

Image evidence is where this dataset hides its traps. A receipt image can say
`TOTAL 9968.00`, and a vision model can confidently answer `1142513200`.

In the live comparison (see §5), exactly that happened: Qwen3-VL 2B returned a
figure about **114,000× too large** for `image_15`. A pipeline that trusted the
model's JSON would have recorded a 1.14-billion-rupee pharmacy bill.

**Fix (Task 9).** The model's structured amount is now a **proposal**, not a fact.
It is accepted only when:

- it is positive and below a sane ceiling,
- its currency matches the event's currency,
- and, when the raw text is also available, the **deterministic parser agrees**
  with it.

If any of those fail, the text parse is used instead. If both fail, the amount
stays unknown — and the forecaster fails closed rather than inventing a number.

### 3.3 "Fail closed" was doing more work than anyone noticed

`event_9421` is a pharmacy purchase with a blank amount, linked to `image_14`.
That image is a **handwritten** carbon-copy invoice. A human reads its line items
(1500, 724, 796, 550, 303, 670) and the `TOTAL 4543.00` beside them. Tesseract and
Qwen3-VL 2B both fail on the handwriting.

Because the amount is unknown, the forecaster refuses to project, and
`request_101` comes back as `amount_safe_to_pay = 0`, `not_affordable`, with the
explanation that the evidence for `event_9421` is unresolved.

That answer is *conservative and honest*, and it is also **one handwritten
receipt deciding an entire request**. The right next step is a human confirmation
step in the TUI, not a guess. This is documented as a known limitation rather
than papered over.

---

## 4. The architecture now: a reader and a judge

```
CSV data ─┐
          ├─► evidence layer ─► forecast ─► candidate plans ─► ranking ─► decision
images ───┘        │                                                        │
                   │                                                        ▼
             local readers                                        deterministic verifier
        (Qwen3-VL / Docling / Tesseract)                                   │
                   │                                                        ▼
                   └──────────────► optional agentic analyst ────────► trace JSON
                                    (LangChain, typed tools)
```

The split is the important idea:

- **The analyst may read, search and argue.** It has typed tools: search the
  evidence, inspect the cash-flow projection, propose a decision.
- **The judge owns the numbers.** Every one of the six machine fields is produced
  by deterministic code. If the analyst's proposal disagrees with the verifier,
  the verifier's answer wins and the disagreement is recorded.

This is why the agentic comparison in §6 shows **machine fields identical in every
request**: the language model can change the *explanation*, never the *arithmetic*.

### 4.1 The local readers

| Backend | What it is | Role |
|---|---|---|
| `vlm` | Qwen3-VL 2B Instruct via a local Ollama endpoint | Primary reader for screenshots |
| `docling` | IBM Docling, run as an optional subprocess in its own interpreter | Layout-aware document text |
| `tesseract` | The original OCR engine | Comparison and last-resort fallback |

`BUY_WAIT_OCR_BACKEND=auto` tries them in that order. A model amount is validated
against the deterministic parser; a **non-loopback** VLM URL is refused unless the
user explicitly opts in, so image bytes do not leave the machine by accident.

### 4.2 The cache

OCR and vision are the slowest steps, so their results are cached on disk:

- keys are **content-addressed** (sha256 of the image bytes *plus* the image path,
  backend, model and parser versions);
- byte-identical images at different paths stay **separate** entries — no
  deduplication, as requested;
- entries **expire** after `BUY_WAIT_CACHE_TTL_DAYS` (default 30) and are deleted
  by `purge_expired()`;
- a corrupt entry is a miss, never a crash;
- failures are never cached, so a flaky server does not poison the next run.

A repeat run on unchanged inputs makes **zero** model calls.

---

## 5. Tesseract vs Qwen3-VL: what the live comparison actually showed

Sixteen images are linked to financial events with blank amounts. Both readers
were run over all sixteen, with no cache on the first pass.

| Metric | Tesseract | Qwen3-VL 2B |
|---|---|---|
| Resolved an amount | 15 / 16 | 15 / 16 |
| Agreement with the other reader | 14 / 15 | 14 / 15 |
| Hallucinated amount caught by the validator | — | 2 |

Three findings worth keeping:

1. **They mostly agree, and the disagreements are formatting.** `100000.00` vs
   `100000`, `41272.00` vs `41272.0`, `723.00` vs `723`. The comparison tool
   originally compared strings and counted five false disagreements; it now
   compares `Decimal` values. Numbers are money, not text.

2. **The VLM is better at layout, worse at self-control.** It produced two
   impossible amounts, both rejected. Tesseract made no such claim because it does
   not make claims at all — it returns text and lets the parser decide.

3. **Neither can read handwriting.** `image_14` defeats both. That is a capability
   limit, not a tuning problem, and it is why the answer for `request_101` stays
   conservative.

The practical conclusion: keep both. Use the VLM where layout matters, keep the
deterministic parser as the arbiter, and never let either one write a number
straight into the ledger.

Full table: [`docs/ocr-comparison.md`](ocr-comparison.md).

---

## 6. Agentic vs non-agentic

*(run 1: `llama3.2:3b`, 5 requests, shipped 6-turn budget, §6.1. Run 2: same
model, 600 s per-call timeout, §6.4. Run 3: `gemma4:e2b-it-qat`, before and after
the flat proposal schema, §6.5. Run 4: the same model once the loop was allowed to
contribute and to repair, §6.6.)*

The point of the experiment is not "is the LLM smarter". It is: **does adding an
LLM loop change any number the user acts on?** If it does, that is a bug.

### 6.1 What we measured

| Metric | Result |
|---|---|
| Machine fields identical to the kernel | **5/5** |
| Explanations rewritten by the analyst | 0/5 |
| Requests that fell back to the kernel | 5/5 |
| Model calls (total / per request) | 9 / 1.8 |
| Wall clock (mean / max) | 160.6 s / 180.7 s |

An earlier smoke run with `qwen3-vl:2b-instruct` (3 requests, same budget)
behaved the same way: machine fields identical 3/3, every request fell back.

### 6.2 The uncomfortable finding

The agent loop ran on `llama3.2:3b`, a genuinely tool-capable chat model. It
explored — fetched context, projections, payment options — and one attempt even
produced a `propose_decision`, which the **schema validator rejected** because it
was missing required fields (`amount_safe_to_pay`, `confidence`, `rationale`,
`request_id`) and had an unexpected one (`spending_changes`). It then ran out of
its 6-turn budget. Four of the five requests timed out at 180 s of CPU inference
before finishing their plan at all.

That is a useful result, not a failure to hide: a 2–3B local model is a reader,
not a planner. It can say what a receipt contains; it cannot hold a multi-step
plan, choose which evidence to fetch, and converge on a typed proposal inside a
bounded budget on this hardware.

The honest options are (a) raise the timeout and accept the latency — attempted in
§6.4 and it did not change the conclusion, (b) use a larger tool-capable model, or
(c) keep the loop for explanation and let the kernel do the planning — which is what
the product does by default.

### 6.3 Why the fallback is a feature

When the agent fails, times out, or exceeds its budget, the request keeps the
kernel's row and the trace records why. A user sees a correct answer and an honest
note, not a hallucinated plan. The 50-call run-wide cap exists for the same reason:
an agent that can spend money without a ceiling is a liability.

### 6.4 Run 2: a bigger timeout, the same wall

The obvious objection to §6.2 is "the model ran out of time, not ability". So run 2
raised the per-call timeout from 180 s to 600 s and re-measured.

| Metric | Run 2 |
|---|---|
| Requests that actually ran the loop | 3 (`request_26`, `request_27`, `request_28`) |
| Machine fields identical to the kernel | **3/3** |
| Explanations rewritten | 0/3 |
| Requests that fell back | 3/3 |
| Wall clock per request | 55 s, 600.4 s (timeout), 454.1 s |

What the extra time bought: `request_28` reached six provider calls and six tool
calls in 454 s and still handed back a `propose_decision` the schema rejected for the
same missing fields as in run 1. `request_27` spent its entire 600 s on a single
provider call and timed out. Six times the patience produced zero additional valid
proposals. The 3B model does not fail for lack of time; it fails at producing the
typed artefact.

Two methodological corrections belong in the record, because both were bugs in our
own measurement harness rather than in the product:

1. The `--max-turns` / `--max-tool-calls` flags were accepted by the comparison
   script but never forwarded to the loop runner, so every "bigger budget" run was
   really the shipped 6/6 budget. The flags now reach the runner; run 2 is therefore
   described as *600 s timeout at the shipped budget*, not a 10-turn experiment.
2. The `--offset` flag was applied only to the reported slice, not to the run
   length, so a window like "requests 29–30" was reported with rows that were never
   actually executed (the loop always starts at the first request in dataset order).
   The runner now receives `offset + limit` and the slice is taken from rows that
   really ran. This is why this section reports three requests, not five: the last
   two were never measured, and an unmeasured row must not be printed as a result.

A third issue was found and fixed in the pipeline itself: a crashing agent
invocation was swallowed by a bare `except Exception`, which is exactly how the
unmeasured rows above first looked like "silent successes". A crash now prints the
request id and the error frame to stderr and is recorded in
`usage["agent_loop_errors"]`, so the comparison report has a `Crashed agent
invocations` section instead of a wall of blanks.

### 6.5 Run 3: a better model, and the bug that was our own

If the 3B model was failing at the *shape* of the answer rather than at the
judgement, then the right next experiment is a model that is better at tool calls,
not a model with more patience. `gemma4:e2b-it-qat` (2.3B dense, native function
calling, 3.3 GB) fits this laptop's 3.4 GB of usable VRAM and runs fully on the
GPU through Vulkan, so it was the natural candidate.

The first run on it was better and still useless:

| Metric | Gemma 4, nested schema |
|---|---|
| Machine fields identical to the kernel | **3/3** |
| Explanations rewritten | 0/3 |
| Requests that fell back | 3/3 |
| Model calls (total / per request) | 14 / 4.7 |
| Wall clock (mean / max) | 92.2 s / 103.7 s |

Two of the three fallbacks named the same shape failure: `propose_decision` was
missing `amount_safe_to_pay`, `confidence`, `rationale`, `request_id`, and carried
an unexpected `spending_changes`. The third (`request_28`) failed differently: it
returned neither a tool call nor valid JSON, so it never reached the validator at
all. Isolated probes of the same model produced perfectly valid vision JSON and a
valid tool call with complete arguments, so the model was not simply incompetent.
That contradiction was worth chasing instead of adding more timeout.

**What was actually wrong.** The tool definition asked the model to fill one
nested object: `propose_decision({"candidate": {…ten fields…}})`. Capturing the raw
arguments the model sent showed it filled exactly six fields on both attempts, and
the six it kept were the ones with an enum or a boolean behind them. The four it
dropped were the free-form ones held *one level inside* the wrapper. A control
probe posting the same tool schemas straight to Ollama, with no LangChain in
between, returned complete, correctly typed arguments — so the adapter was
innocent. The nesting was the loss point.

**The fix, and what it deliberately did not do.** The proposal is now passed as ten
flat top-level arguments (nested payloads still parse, so nothing regressed),
every field carries a description and a format hint, the rejection reply now names
the exact keys that were missing instead of repeating the same generic sentence,
and the local provider exposes sampling knobs (`BUY_WAIT_OLLAMA_TEMPERATURE`,
`NUM_CTX`, `TOP_P`, `TOP_K`, `REPEAT_PENALTY`, `NUM_PREDICT`) with reproducible
defaults. What did **not** change is the validator: it still demands all ten fields
with the exact types. Loosening it would have turned the one component that
protects the user into a rubber stamp.

| Metric | Gemma 4, flat schema |
|---|---|
| Machine fields identical to the kernel | **3/3** |
| Explanations rewritten | 0/3 |
| Requests that fell back | 3/3 — all *verifier disagreements*, zero schema rejections |
| Model calls (total / per request) | 11 / 3.7 |
| Wall clock (mean / max) | 71.5 s / 84.6 s |

**The honest reading.** The wall moved. Before the fix the model could not produce
the artefact at all; afterwards it produced a well-formed proposal on all three
requests, and the verifier overruled it on substance — disagreeing about
`payment_plan` and `earliest_date_for_full_payment` on two requests and about
`recommended_payment_method` on the third. That is a different and much healthier
failure: the loop is now doing agentic work end to end, and the deterministic judge
is catching an analyst that is confidently wrong about the schedule. Model calls
dropped from 14 to 11 and wall clock by 23 % simply because nothing is wasted
re-submitting a payload that will be rejected.

The lesson worth carrying: at 2–3B parameters, **tool-schema ergonomics matter more
than model size**. A wrapper key that looks tidy to a human is a lossy pipe for a
small model, and no amount of timeout compensates.

### 6.6 Run 4: giving the model somewhere legal to put its answer

Runs 1–3 shared a structural defect that I put there, not the models: the verifier
accepted a candidate only when it *agreed* with the kernel on every machine field,
so a model could change exactly one thing — `decision_explanation` — and only when
its reasoning resembled the kernel's own text. A model that agreed won nothing; a
model that disagreed was discarded. Under that contract, "the agent contributes
nothing" was guaranteed by design, and three different models proved it.

Run 4 changed the contract in three ways, all without moving a single machine value:

1. **Repair instead of rejection.** A failed proposal now comes back to the model as
   a structured rejection naming the field, the kernel value and the model value,
   bounded by `BUY_WAIT_AGENT_REPAIR_TURNS` (default 2).
2. **Claim-checked prose.** The model writes the explanation; `code/claims.py`
   extracts every number, date, status and method name from it and requires each one
   to be reproduced by the kernel row. Prose ships only when the candidate also
   agrees with the kernel on every machine field, and only then is it claim-checked;
   a disagreeing candidate gets a marked `Disagreements:` note on the agent path.
   Fabricated prose never ships.
3. **Intent first.** Every instruction is classified (question / analysis / action /
   compound / follow-up) before any tool runs, so the loop reads the request the
   user actually made.

| Metric | flat schema, no contribution (§6.5) | run 4, repair + claims |
|---|---|---|
| Machine fields identical to the kernel | 3/3 | **3/3** |
| Requests that fell back to the kernel row | **3/3** | **0/3** |
| Explanations written by the model | 0/3 | **2/3** |
| Model calls (total / per request) | 11 / 3.7 | 22 / 7.3 |
| Wall clock (mean / max) | 71.5 s / 84.6 s | 176.3 s / 245.7 s |

Three runs were measured end to end at 3 requests each. The explanation count went
0/3 → 1/3 → 2/3 and 2/3, and the fallback count went 3/3 → 0/3 and stayed there.
The per-request prose changes between runs — in one run `request_26` ships and
`request_27` does not, in the next the reverse — which is honest variation from a
2.3B model, not a number I should quote as stable.

**Cost.** Calls roughly doubled (3.7 → 7.3 per request) and wall clock roughly
doubled with them, because the loop now reads evidence, projects cash flow, submits,
reads the rejection and tries again. That is the price of an agent that finishes
rather than one that fails fast.

**Two over-rejection bugs found on the way, both mine.**

The first: the claim check only knew the numbers in the final decision row, so a
rationale citing the *request amount* or the user's *minimum balance* was treated as
fabrication. `request_28` was discarded for quoting `1048.86` and `1100` — its own
profile figures. The check now receives the request amount, both request dates and
the two balance figures the model was shown, because citing a fact you were given is
not inventing one.

The second was the same bug wearing a disguise: the facts fetch called
`tools.call("get_request_context", request_id)` positionally, while `Toolbox.call`
takes only keyword arguments. The `TypeError` was swallowed by the surrounding
`except`, so `facts` was silently empty and every citation stayed unsupported —
`request_27` was rejected for quoting `20500`, the user's actual minimum balance.
A test now pins the call to keyword form, and a failed fact read is documented as
"narrows what prose may cite" rather than a harmless detail.

Both are the same lesson: a trust boundary that fails closed on *my* plumbing error
looks exactly like a model hallucinating. Fail-closed is only honest when the path it
guards is correct.

---

## 6x. A second mind, checked: the JEV decision gate

A different experiment asks a different question: not "can a model plan", but
**"does an independent, differently-shaped model agree with the kernel?"** Jev
(TypeSafe's decision model, run through OpenCode Zen) is *not* a chat model. You
hand it a compact state and typed propositions, and it returns probabilities.
It cannot write prose, cannot plan, cannot call tools — which makes it a nearly
ideal auditor for a verifier-driven product.

How it is wired: for each of the 25 labelled sample requests, `tools/experiments/compare_jev.py`
sends one `noul`-proposition set per request — "the status is X" for all four
statuses, "the method is Y" for all five methods, "this decision keeps the balance
above the minimum after every commitment", "the facts given are sufficient" — and
takes the argmax as the model's answer. The kernel's row is never modified.

What came out of the live run (free tier, 25/25 answered, zero cost):

| Question | Result |
|---|---|
| Status: Jev matches the kernel | **25 / 25** |
| Method: Jev matches the kernel | 23 / 25 |
| Mean status probability | 0.83 |
| Mean method probability | 0.80 |
| Safety proposition (mean noul) | 0.57 |

Reading it like an auditor:

- **Perfect status agreement from an outsider is the strongest independent
  evidence so far** that the kernel's affordability calls are consistent with
  what a differently-trained model reads in the same facts — with the caveat
  below about who reads what.
- The two method disagreements are the same two requests where the kernel says
  `not_recommended` under its calibration and the sample answer later says the
  request is affordable (`request_01`, `request_02`). Jev prefers `wait`: it
  agrees the user should not pay *today*, but believes the request is payable
  eventually. That is the same calibration gap G2 shows, seen through a second
  model — which is exactly what a gate is for.
- The safety proposition is the least confident signal (mean 0.57), and its
  *lowest* values land on requests the kernel already declined. Wording matters:
  a declined purchase trivially preserves the balance, so a low plausibility
  here reads as Jev questioning the situation, not the decision. Treated as
  calibration data, not as a verdict.
- Engineering honesty: the free tier refuses typed `choice`/`score` questions
  (HTTP 422) — everything above is `noul`-only; Cloudflare blocks Python's
  default user agent (HTTP 403 code 1010) so the client declares an explicit
  one; the paid tier was not usable (insufficient funds), so nothing was spent.

The gate is fail-open by design: no key, a refused call, or a malformed answer
leaves the kernel exactly as it was, and 15 offline tests pin that behaviour.

---

## 7. Where the quality actually stands

Collected from `python -m evaluation.run --mode release` (the release run also
executes the live agent-robustness gate):

| Gate | Meaning | Result |
|---|---|---|
| G0 | Output shape and value domains | **PASS** (15/15 checks) |
| G1 | Safety: no status upgrades, no overclaimed safe amounts | **PASS** |
| G2 | Agreement with the 25 labelled sample answers | **FAIL** (see below) |
| G3 | Determinism: repeated runs byte-identical | **PASS** |
| G4 | Provenance: output tied to commit and usage report | **PASS** |
| G5 | Archive clean-room run | **NOT RUN** (no archive supplied; container build deferred — see §8) |
| G6 | Archive/history secret scan | **NOT RUN** (tree scan runs inside G0; archive scan needs G5) |
| G7 | Agent robustness: a crashing agent changes nothing | **PASS** (4 checks) |

G2 is the honest one. On the 25 public samples:

| Field | Exact matches |
|---|---|
| Whole row | 3 / 25 |
| `amount_safe_to_pay` | 3 / 25 |
| `affordability_status` | 14 / 25 |
| `recommended_payment_method` | 14 / 25 |
| `payment_plan` | 12 / 25 |
| `earliest_date_for_full_payment` | 11 / 25 |
| `spending_changes_needed` | 21 / 25 |

The sample set is small and its label style is not fully known to us, so treat
these as a directional signal, not a score. The pattern is consistent, though:
**status and method are roughly right half the time, amounts almost never match
exactly.** In a 90-day projection, a one-day error in an expected salary changes
the safe amount, so exact amount agreement is a very strict bar — but 3/25 also
says the spending-change reasoning is not yet good enough, because the missing
gold actions are mostly `stop:` / `reduce_to:` choices we did not propose.

That is the next real piece of work: better candidate generation for spending
changes, not more prompt engineering.

G7 is new in this release. It is not a paperwork slot any more: the gate breaks the
agent loop on purpose for two sample requests and requires that the pipeline still
finishes, that every machine field matches the deterministic baseline, that each
crash is recorded in `usage["agent_loop_errors"]`, and that the same crash is named
on stderr. A gate that only asserted "the loop exists" would have passed while the
silent-failure bug in §6.4 was still live; this one would have caught it.

---

## 8. What is still weak, stated plainly

1. **Spending-change generation.** We under-propose `stop:` and `reduce_to:`
   actions. One sample even emits an action where the reference says `none`.
2. **Exact amount calibration.** 3/25 exact matches is poor, even for a strict
   metric. The forecast is conservative, which is the right direction, but not
   well calibrated to the reference labels.
3. **Handwriting.** One unreadable receipt forces a whole request to
   `not_affordable`. A human-in-the-loop confirmation would fix it.
4. **Live model quality is mostly unmeasured.** The 50-call OpenRouter budget has
   not been spent. The local models now complete the loop and stop falling back
   (§6.6), but on three requests each, and what they contribute is narration plus the
   occasional guarded spending change — there is still no evidence that a strong
   remote model's proposals would beat the kernel's own row.
5. **Docker is unverified.** The image definition exists and is checked by tests,
   but it has not been built here because the development machine ran out of disk
   space. Treat the first build as unproven; G5/G6 stay NOT RUN until an archive
   exists.
6. **The agentic path is measured, not proven.** Thirteen requests have now run end
   to end (five with `llama3.2:3b`, eight with Gemma 4 across four runs). The early runs
   fell back every time — first because the model could not fill the schema, then
   because the verifier disagreed with its plan — and the last two runs fell back not
   at all, shipping the model's own explanation on two of three requests. That is a
   real contribution path working, still on three requests and one 2.3B model. The
   Kilo/Nemotron path is wired and documented but has never been run with real
   credentials, so no claim here rests on a frontier model.
7. **`output.csv` is generated, not golden.** It is now rebuilt from the release run
   (250 rows, sha256 `1f35043f…`), but it reflects this build only. Regenerate it
   after any forecast or policy change; G4 ties the file to the commit that made it.

---

## 9. How to run it

```powershell
python -m code.main                 # deterministic batch -> output.csv
python -m tools.tui                 # interactive dashboard
python -m evaluation.run --mode pr  # the gates above
python -m code.agent --provider ollama --model qwen3:4b-instruct   # agent demo
python -m tools.experiments.compare_ocr --backend both                          # OCR comparison
python -m tools.experiments.compare_agentic --limit 3                           # agent vs kernel
python -m tools.experiments.compare_jev                                        # JEV audit (needs OpenCode Zen)
python -m code.cache --stats --purge                               # cache hygiene
```

Everything above works offline. The optional remote deployment provider is
**Nemotron Ultra through the Kilo API**, and it requires the user to supply their
own credentials:

```powershell
$env:BUY_WAIT_LLM_PROVIDER = "kilo"
$env:BUY_WAIT_KILO_BASE_URL = "<your Kilo endpoints base URL>"
$env:BUY_WAIT_KILO_API_KEY  = "<your key>"
# BUY_WAIT_KILO_MODEL defaults to nemotron-3-ultra
```

No key is required to run, test, or evaluate the product. That is deliberate.

---

## 10. The release path, and what the reviews changed

The agent contribution reached `main` through a curated branch, not by merging the
competition tree. `agentic-overhaul` stays where it was (`d5fb592`); the release
branch replayed twenty-one files onto `main`, added a production `AGENTS.md`, and
then cut redundancy — the cuts belong on the release path, never on the experiment
branch.

### 10.1 What the cuts found

| Planned cut | Outcome |
|---|---|
| `tools/correctness_report.py` into `tools/experiments/` | **Refuted.** Gate G0 invokes that literal path (`evaluation/run.py`), so the script stays in `tools/`. |
| delete `tools/evaluation/` | **Refuted.** It is a load-bearing mirror of `evaluation/usage_report.md`: `evaluation/run.py` compares the two byte for byte and `tests/test_hardening.py` asserts the pair. Deleting it would have removed a provenance check, not duplicate weight. |
| four audit scripts to `tools/experiments/` | **Done.** `compare_agentic`, `compare_jev`, `compare_ocr` and `verify_samples` moved, with repo-root depth fixed and every documented path updated. |
| one Decimal coercion | **Done** for the six modules that had their own; two hand-written coercers were then found by review and folded in too. |
| three near-identical remote providers | **Done**: one `OpenAICompatibleProvider`, three thin declarative presets, plus a `paid` class for real user data. |
| prune the suite from 629 to "about 520" | **Refuted as stated.** Only one `def test_` name repeats across the whole suite, ~28 of the 637 cases come from parametrisation, and the `test_oracle_*` files are not duplicates: they pin distinct §6.3 rules (pending credit ignored, pending debit reserved, failed/cancelled/unrealised ignored, NaN and blank fail-closed, duplicate ids de-duplicated, cancellation before settlement). Reaching 520 would have deleted about 110 one-off invariant tests. Four assertions that looked redundant were cut first; the pre-push correctness review then proved three of the four were not duplicates (only the `test_jev` name repeated), so the derived coverage was restored before sign-off: the `.gitignore` negation that lets `.env.example` through, the check that every `.env.example` value is a placeholder (its absence had left `SAFE_ENV_VALUES` and `_assignments` as dead code), the upscale bound in `preprocess_image`, and the Jev redirect handler. Restoring the OCR bound also exposed a real gap: the cap applies to upscaling only, so a source wider than 2400px is passed through unchanged, and the test now pins that behaviour instead of a claim the code does not make. 654 passed, 1 skipped. |

### 10.2 The two pre-push reviews

Two independent reviews ran against the branch before the push, briefed
adversarially — one on correctness and determinism, one on security and privacy.
Both returned **BLOCK**, and both were right. What they found, and what changed:

| Finding | Response |
|---|---|
| The verifier shipped a candidate's prose whenever its claims checked out, so a model that disagreed with the kernel on every machine field could still author `decision_explanation` — and prose with nothing checkable always passed | Agreement is the first gate again; claim checking narrows it. Two tests that had pinned the looser rule were rewritten and one added. |
| `is_local_url` accepted any host starting with `127.`, which defeated the plaintext-transport refusal and silenced the free-tier warning for a remote host | The check parses the host as an IP address and asks `is_loopback`; `localhost` is the only accepted name. |
| `python -m code.main` sent every row's facts to a free tier with no notice, and `OPENROUTER_KEY` alone was enough to trigger it | The batch path and the Jev gate warn on stderr, once per process, exactly like the TUI. |
| `code.explanations` and `code.jev` used `urllib`, which follows a redirect and copies the `Authorization` header: a `302` could resend the key in cleartext | Both use an opener that refuses redirects and refuse a remote `http://` endpoint before the call. |
| The task trace wrote the typed instruction and every clause verbatim, and a provider error containing a key or a URL password went in unsanitised | The trace stores a sha256 of the instruction and no step text, and the record passes through `trace.sanitize`, which now also scrubs `user:password@`. |
| `_money_value` called `parse_amount` without its currency argument, so the `TypeError` fell into a bare `except` on every call and `1.050,00` was read as `1.05` | The locale parser is used, the money regex accepts either separator shape, and `to_decimal` is the last resort. |
| A month name resolved to a past date; a manual `afford` never set `current_request_id`; a no-op `apply` dropped every override; the TUI rebuilt its `Workspace` on every command | All four fixed and pinned. The TUI now keeps one workspace per session, so `ask and if I wait until the 20th` works as documented. |
| `README` called Kilo a `paid` provider while the code classifies it `free` | Table corrected. A reader who trusted it would have sent real text to a training tier expecting no warning. |

The branch-wide CodeRabbit deep review returned 12 findings, 5 of them the same
defects the two adversarial reviews had found independently — which is the useful
part, not the count. Reviewing the fix commits against the branch turned up five
more, four of which were in the code the first round had just written:

| Second-pass finding | Response |
|---|---|
| A task could persist a spending-change **amount**: `reduce_to:<event_id>:<amount>` reached `actions_applied`, and the trace writer copied it through | The trace keeps `reduce_to:<event_id>` and drops the figure |
| The trace writer emptied every step's structured `data`, losing the intent reading the TUI renders | An allowlist of intent fields (`intent`, `request_kind`, `source`, `confidence`, `ambiguity`) survives; nothing else does |
| `"29 February"` asked in a non-leap year resolved to `None` | A month name searches forward to the next leap year |
| A manual request id from `new` could collide with one `ask` had already created, and `row()` returns the first match — so a later question could answer with an earlier request's decision | `add_manual` skips ids already present in the dataset and in the session |
| `extract_date` searched only two years forward | Same fix as the leap day |

A third pass over the release branch found nine more, two of them major, both in
code this release had just written:

* the `Disagreements:` note shipped model prose with no claim check at all, so a
  disagreeing model could put an invented amount or date in the row. The note may
  now quote only figures that appear in the kernel row or in its own candidate —
  which is what makes it a debugging record rather than a claim — and falls back
  to the marked sentence otherwise.
* `parse_instruction` resolved "the 12th" against `date.today()`, so the same
  instruction decided differently on different days, and the request-relative
  fallback in the step handlers never ran. A relative date is now left unset at
  parse time and resolved against the target request.

The other seven: `redact_url` read `parts.port` and could raise `ValueError` from
a refusal message (and dropped IPv6 brackets); `_write_trace` copied `result.changed`
verbatim, which carries the `afford` safe amount and the before/after of every moved
decision field; `ask` and `new` could still collide on a manual id from opposite
directions; `agent.finish` backfilled a stale repair diff onto a run that had agreed;
and the README, `code/README` and this report described the pre-release rationale
rule, which the release itself changed.

A fourth pass, the security reviewer's second look at the tip, returned BLOCK on
three things that survived because each was a surface nobody had pointed at yet:

* `BUY_WAIT_ACCEPT_FREE_TIER` silenced the notice on the two surfaces §2 names by
  hand — the TUI and `python -m code.task_agent` — while `.env.example` promised the
  TUI still warned. Both now pass `honour_acceptance=False`; the switch can only
  quiet the batch pipeline, which §2 does not name.
* the SQLite trace summary was written raw while the JSON trace was sanitised, so a
  gateway URL with basic auth and a bearer key survived in `data/app.db` long after
  the JSON was gone. `record_trace` now sanitises its payload.
* the embeddings client reads `BUY_WAIT_OLLAMA_BASE_URL` too, and it neither refused
  a plaintext endpoint nor warned: one env var put every evidence chunk on the wire
  in cleartext, silently, on the same run that warned about the chat model. It now
  refuses a non-secure remote endpoint and emits the same notice.

The same review caught the duplicated loopback test in `code/vision.py`, which is
the exact drift the provider fix removed; it now calls the shared predicate.

### 10.3 The disagreement note, and why it is agent-only

The model's explanation used to be discarded whenever it disagreed with the computed
ledger, which is correct but silent — a reader could not tell "the kernel had nothing
to say" from "the model said something we threw away". On the agent path only, a
valid candidate that disagrees now ships `Disagreements: … (fields: …)`: the model's
own words when it names no field or names only fields that actually differ, and a
neutral sentence when it names a field that does not. Prose that mentions amounts
rather than field names is therefore kept — the note is a record of what the model
thought, and the test `test_a_disagreement_note_may_quote_the_candidate_s_own_figures`
pins that case. The field list comes from the verifier, not the model, so it cannot
be talked around.

`code/agent.py` opts in; `code/tools.py`'s mid-loop check and `code/main.py`'s batch
path do not, so `output.csv` is still the kernel's text byte for byte. `rationale_source`
gains a third value, `disagreement`, and `code/main.py` still only prefers
`candidate`, so the note can never reach a shipped row.

### 10.4 What is still open, stated plainly

1. **G2 is still the accepted baseline failure** (§7). Nothing in this release
   re-baselined or silenced it.
2. **Docker remains unverified.** No build was attempted here, and G5/G6 stay
   NOT RUN until an archive exists.
3. **`BUY_WAIT_ACCEPT_FREE_TIER` is still process-global for the batch pipeline.**
   It silences the notice for every free or unclassified endpoint in that process,
   not just the one the operator accepted. The TUI and `code.task_agent` now pass
   `honour_acceptance=False`, so a copied `.env` can no longer quiet the two
   surfaces where a person types free text; a per-provider allowlist would still
   be tighter for `code.main`.
4. **The TUI's privacy notice is once per session, not per provider.** Switching
   from one free tier to another mid-session does not re-print it.
5. **`python -m code.task_agent` ignores `BUY_WAIT_LLM_PROVIDER`.** It only builds a
   provider when `--provider` is passed. That fails safe (rules only, no model
   call), but `.env.example` presents the variable as the switch.
6. **An ambiguous single separator still needs a currency to read.** `1.050` is
   1050 in IDR and 1.05 in USD, and the parser is called without a home currency
   when the clause is parsed before the profile is loaded.
7. **Instruction-shaped prose can still reach `output.csv` on an `--agent` run.**
   The `Disagreements:` note is claim-checked for invented numbers and dates, not
   for imperatives, and on an agent run the loop row is the output row. The
   agreeing path has always admitted model prose, so this is not a new hole — but
   a span that dropped a sentence from `messages.csv` would be cheap to add and
   freely available to inject through, so it is the next thing to close.
8. **`is_loopback_host` trusts the name `localhost`.** A poisoned hosts entry
   would let a key leave the box over `http://localhost`. Resolving the name and
   testing the address would close it; the risk was judged below the cost of one
   more resolver call per provider build.

### 10.5 The numbers this release was verified at

- `pytest -q`: **654 passed, 1 skipped**.
- `output.csv`: 250 rows, sha256 `1f35043f…376486`, byte-identical to the committed
  file before and after every change in this release.
- `python -m code.main` on the offline path: no network call, no stderr noise.
- `evaluation.run --mode pr`: G0, G1, G3, G4 PASS; G2 FAIL at the recorded baseline.
- `tools.build_release`: 80 files, 291413 bytes, `evaluation/` present, no banned
  names, archive clean (`sha256 72e876ed…`).
