# The agent that does things

The batch path answers one question per request: *can this user pay this?* This
page is about the other shape of work — a person typing an instruction — and
about what the model is allowed to contribute there.

Everything below is session-only. `output.csv` and `dataset/` are never written,
every edit is reversible with `undo`, and each task writes one JSON trace to
`traces/tasks/`.

## How it works

1. **Parse.** The instruction is split into ordered clauses and each clause is
   classified (`afford`, `explain`, `changes`, `apply`, `whatif`, `undo`).
2. **Understand.** Every clause carries an intent reading: `question`,
   `analysis`, `action`, `compound` or `follow_up`, plus the purchase kind and
   any missing parameter. The rules always answer; when a provider is configured,
   clauses the rules cannot read are sent to the model's classifier and the trace
   records which reading won.
3. **Act.** Each step runs through the same solver and oracle as the batch path.
   No step computes its own cash flow.
4. **Report.** The reply names what it did, what changed, what it refused, and
   where the trace went.

## Transcript (rules only, no model)

```text
tui> ask why was request_101 refused
[1/1] explain  (intent: question, ambiguous via deterministic_fallback): why was request_101 refused
request_101: not_affordable via not_recommended, safe 0, earliest no safe date.
Unresolved linked image evidence; no payment is recommended.
nothing was changed in this session

tui> ask where am I leaking money for request_26
[1/1] changes  (intent: analysis, ambiguous via deterministic_fallback): where am I leaking money for request_26
request_26: already affordable (safe 15,656,000.00 covers the 15,656,000.00
request), so no spending change is needed. Cheapest candidates would be
stop:event_2322.
nothing was changed in this session

tui> ask can I afford 40000 on 2025-08-05
[1/1] afford  (intent: question, purchase via deterministic_fallback): can I afford 40000 on 2025-08-05
Yes. 40,000.00 on 2025-08-05 is affordable now; the most you can pay today is
40,000.00. Recommended full_payment (affordable_now).
nothing was changed in this session

tui> ask undo
[1/1] undo  (intent: action, ambiguous via deterministic_fallback): undo
cleared every session change
```

Note the second answer: it refuses to sell a spending change the user does not
need. A tool that always finds something to cut is a tool that lies.

## Transcript (local model classifies)

`gemma4:e2b-it-qat` on Ollama, one clause the rules cannot read:

```text
$ python -m code.task_agent "is it wise for me to commit to that new phone right now" --provider ollama --model gemma4:e2b-it-qat --no-trace
[1/1] clarify (refused) intent=analysis/purchase via model
  I need one more thing before I can act: Current financial situation and phone details.
```

The model read it as a purchase question and reported what it still needs; the
engine asked instead of inventing an amount. That is the whole point of intent
detection here — it changes *which step runs*, never the numbers.

## What the model may change in the batch path

| Field | Path | Guard |
|---|---|---|
| `decision_explanation` | rationale ships when every claim is reproduced by the kernel | `code/claims.py` |
| `spending_changes_needed` | `propose_spending_change` | kernel re-solve must raise the safe amount; event must be permitted and unprotected; the shipped row keeps the kernel's value, so an accepted proposal is advice |
| nothing else | — | kernel row, always |

Claim checking catches the two failure modes that matter:

* `request_27`: the model wrote "the safe amount is 6670.00 …" and also cited
  `20500`, which the decision did not support → prose discarded, kernel text kept.
* `request_28`: it cited `1048.86` and `1100` (real profile figures) and called
  the request `not_affordable`; the amount and status did not match the row the
  check knew about → discarded.

The fix for the second case is in `code/agent.py`: the check is now given the
request amount, both request dates and the two balance figures the model was
actually shown, because citing a fact you were given is not fabrication.

## Guarded spending changes

Verified against the real dataset (`request_26`):

```text
propose_spending_change(event_2322, stop)  -> accepted: False
  reason: the kernel re-solved and this change does not raise the safe amount
propose_spending_change(event_1, stop)     -> refused
  event_1 is not a permitted spending change for this user (protected category,
  fixed cost, or outside the categories the user will change)
```

A change is accepted only when the re-solved safe amount actually rises. The
model proposes; the ledger decides. The shipped row still carries the kernel's
own `spending_changes_needed`, so an accepted proposal is a tested
recommendation rather than an edit to the answer.

## Concerns

`raise_concern(kind, detail)` and `reconcile_receipts(user_id)` exist because the
most important thing an assistant can say is "I could not read this". On the real
dataset `reconcile_receipts("user_101")` returns exactly one unresolved event:

```text
[{'event_id': 'event_9421', 'category': 'healthcare', 'date': '2025-11-02'}]
```

That is the handwritten pharmacy invoice that fail-closes `request_101`. The
concern is visible in the trace and in the TUI instead of being buried in a
warning list.
