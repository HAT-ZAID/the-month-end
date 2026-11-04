# Improvement Report 2 — from a competition deliverable to an agent somebody can use

This is the second improvement report. The first (`docs/improvement-report.md`) explains the
overhaul of the decision engine: what the kernel owns, what the agent may contribute, and the
ten review passes that hardened the explanation gate. This one is about the question that came
after it, and the answer was uncomfortable:

> the product was built to be scored, not to be used.

Every screen it had was a view of `dataset/`. A stranger opening the interface could create a
*request*, but the balance, the commitments, the payment preferences and the exchange rates all
still came from a 250-row fixture file. Nothing in the product asked the person what they
actually have. That is the finding this report is organised around.

---

## 1. What the first phase did well, in one paragraph

The kernel is worth keeping and was never the problem. `solve_request` takes plain Python
structures — a profile dict, a list of event dicts, fx rows, a payment-options frame — and
returns the decision. It has no knowledge of `dataset/` at all; the path dependency lived
entirely in the loader. That is why the general-use work could be additive: the missing piece
was an input and a store, not a solver rewrite. Everything below is built on that fact.

---

## 2. Findings

### 2.1 The home screen was a census of a fixture file, not a view of the user's money

`cmd_dashboard` reads `REPO_ROOT/output.csv` through `read_output_snapshot()` and prints
`dashboard_summary(baseline, rows)`, labelled *"output.csv snapshot (250 rows) — not recomputed
in this session"*. It counts statuses and methods across the fixture and lists which rows need
attention. For the person who has just opened the app it answers a question nobody asked: there
is no balance, no floor, no committed spending, no figure for what is safe to spend today.

**Fix:** a `position` screen, and `dashboard` demoted to the deliverable report. The position
figures must come from the kernel, not from arithmetic in `tools/tui.py` — summing a balance in
the view layer is precisely what `AGENTS.md` §0 forbids, and it is how the two prose writers
drifted apart in the first phase.

### 2.2 The contract vocabularies fail silently when guessed

The most expensive finding of this phase, because it produces a confidently wrong answer rather
than an error. Writing `direction="inflow"` on an income event returned:

```
amount_safe_to_pay: '900'      affordability_status: 'not_affordable'
```

— a safe amount of 900 beside a decision that nothing is affordable. The ledger had not counted
the salary, so the floor dominated. Replacing `inflow`/`outflow` with the contract's own
`credit`/`debit` returned `affordable_now` / `full_payment` / `900` / `2026-11-01` on the same
account.

The vocabularies, recorded here because there is no schema to enforce them:

| Column | Values |
|---|---|
| `direction` | `credit`, `debit`, `non_cash` |
| `event_type` | `income`, `expense`, `subscription`, `debt_payment`, `investment_purchase`, `investment_sale`, `investment_valuation`, `refund` |
| `status` | `settled`, `pending`, `scheduled`, `failed`, `cancelled`, `unrealized` |
| `flexibility` | `fixed`, `reducible`, `reducible_or_stoppable`, `stoppable` |

Multi-value profile columns are pipe-separated (`rent|education|groceries`), for example
`payment_methods_user_will_consider = full_payment|partial_payment|installments`.

**Fix:** `code/account.py` owns these as tuples and rejects an unknown field or value at the
boundary, so a bad word arrives as an error next to the person who typed it instead of as a
wrong number in a decision.

### 2.3 One file per person is the wrong shape for a ledger that grows

A single JSON account is fine for a demo and wrong for the product: nobody enters their whole
financial future in one sitting. The account has to be added to, across sessions, and survive a
process dying mid-write.

**Fix:** SQLite, one file (`data/accounts.db`, gitignored), one row per fact. `save` replaces a
user's rows inside a transaction, so a crash cannot leave a half-written ledger. `load` returns
`None` for an unknown name, so "new person" and "person with nothing recorded" are distinguishable
by the caller.

### 2.4 The explanation gate could be talked out of the decision it was explaining

Carried over from the review rounds, recorded here because it was found by the same method —
attack the thing, measure, fix the shape rather than the wording.

- A sentence that named the recommended method and denied it shipped on **244 of 250** rows:
  *"Do not pay in full now."* contains `pay in full`, and the phrase table is polarity-blind.
- The batch writer's guard (`_checked_explanation`) asked only whether every figure was
  supported. A sentence that named the right method and the right amount while telling the user
  not to buy shipped on **97 of 97** rows where it was false.
- On the six `partial_payment` rows the claim set and the printed plan disagreed by one cent, so
  a correct citation of the second payment was refused on **6 of 6**.
- The denial rule's scope excuse leaked through its own 40-character window: *"you don't need to
  stop it, and no other changes are needed"* shipped, because one match held two denials and the
  later scope word excused the earlier one.

Each was closed by asking a structural question instead of adding a word: the guard now asks the
same three questions `enforce_decision` asks (figures supported, every required action cited,
decision stated), and a segment that negates a change while the clause names the required event
vetoes any excuse.

### 2.5 The trace scrubber was quadratic on a realistic error body

A 65 KB hyphen run with a separator cost **22 seconds**; the userinfo branch's value class
allowed `:` and started at every offset of a run. Bounded and re-measured: 0.11 s, and 320 KB of
`a:1,` went from 190 s to 0.22 s. Worth recording because the regression test that was supposed
to catch it passed for eleven rounds — its fixtures contained no `=`, `:` or `@`, so the branch
it was measuring was never entered.

**Lesson:** a performance test whose input never reaches the code under test is worse than no
test, because it reports green.

### 2.6 The conversation was a router, not an agent — and that is the sharpest finding here

`ask "what are the options i have?"` answered *"I did not understand that clause."* That is the
visible symptom. The cause is that `cmd_ask` calls `code.task_agent.run_task`, a **rule-based
classifier over six fixed families** — affordability, explain, list spending changes, apply one,
what-if, undo — with no tool the model can choose and no reasoning about the person's ledger. With
no provider it prints *"no model configured (...); classifying with the rules"* and answers from
those rules.

Measured honestly, the product claimed to be agentic and shipped a 2019-era FAQ bot: the agent
vocabulary was in the README, the router was in the code, and the two never met. Worse, the real
loop was already in the repository — `code/agent.py`'s `run_agent_loop` has a bounded tool-calling
loop with timeouts, tool-call recording, tracing and a call cap. The interface simply never called
it. This was not a missing capability; it was an unmade wiring decision, which is the kind of
defect a review of the *code* will never surface because every individual file is correct.

**Fix:** route the interface through `run_agent_loop` with tools over the account store
(`position`, `add_event`, `decide`, `what_if`, `apply`, `undo`), keep `enforce_decision` as the
owner of every machine field, print which tool ran and why, and say plainly when no provider is
configured instead of presenting rule output as conversation. The rules remain as a labelled
fallback for the offline path and for the tests that pin them. Phase 1b of
`docs/general-use-plan.md` carries the acceptance criteria, including a test that asks a question
the six families cannot answer and asserts the loop called `position` then `what_if`. A test that
merely checks a phrase reaches a handler proves nothing about agency.

**The general lesson, worth more than the fix:** an "agentic" claim has to be checkable from
outside. If no user-visible behaviour would change when the model were removed, the model is
decoration.

---

## 3. What was built

| File | State | What it is |
|---|---|---|
| `code/account.py` | new, untracked | The shape of one person's account: profile, events, requests, payment options; contract vocabularies; `problems()`; `account_data()` producing the exact `data` dict the kernel and TUI already consume |
| `code/accounts.py` | new, untracked | SQLite store for named users: `connect`, `list_users`, `has_user`, `load`, `save`, `create`, `load_or_create`, `delete` |
| `docs/general-use-plan.md` | new, untracked | The phased plan, written to be readable after a context reset |

Proved by execution: an account with a ZAR balance of 12,000, a floor, a settled salary, a
streaming subscription, one request and one payment option round-trips through SQLite with
`problems() == []` and produces a kernel decision of `affordable_now` / `full_payment` / `900` /
`2026-11-01`. `pytest -q tests/test_submission.py tests/test_hardening.py` → 18 passed, so the new
modules do not trip the secret-pattern, hardcoded-label or absolute-path scans.

Not yet done, and the reason this report exists: none of it is reachable from the interface, not
one line of it is committed, and `tests/review/test_account.py` does not exist.

---

## 4. The rule this phase established

`dataset/` is calibration and test material. It is how the engine was tuned and how the release
gate proves it still behaves; it is not the product, and no screen should open on it.

The product is: a person names themselves, records what they have a few facts at a time, and
asks whether they can buy something. Everything the product says about their money comes from the
kernel, carrying its assumptions — conservative, before optional spending changes, over the
projection window — because a number with no stated assumptions is the most confidently wrong
thing a product can print.
