# General-use plan — make The Month-End usable on a real person's money

**Read this first after any context reset.** It is written to be self-contained: the state, the
contract, the API surfaces, the phases and the acceptance criteria are all here, so no prior
conversation is needed.

> **Newer documents, read them too.**
> `docs/handover-state.md` — the branch, the 12 commits, what is verified, what is unreviewed.
> `docs/g2-root-cause-plan.md` — the deep G2 investigation: established defects, the ruled-out
> register, open hypotheses H1–H20, the agentic probe programme, and the build sequence.

---

## 0. The instruction this plan serves

> "now make room for new user requests and their predictions, it an agent for real purpose use
> not just dataset that was just for calibration and testing, what use of dataset i have now when
> the competetion is long over as i told you ? what's the purpose to make it agentic when nobody
> can use it ?"

> "also more things to add: use database to store users with name cause no one's is gonna log all
> the future and logs in one turn so"

In one sentence: **`dataset/` is calibration and test material only. The product is an agent a
person points at their own money, one recorded fact at a time, across many sessions.**

Consequences that are not negotiable in this phase:

- No screen opens on the fixture file. `dashboard` is a report, not a home screen.
- A person is identified by name and resumes where they left off.
- Facts are recorded one at a time. There is no "enter your whole financial future" form.
- Every figure the product states about someone's money comes from the kernel, never from
  arithmetic in the view layer (`AGENTS.md` §0), and it travels with its assumptions.
- The interface is **Textual** (see Phase 1c). Decided 2026-11-01. Not a web front end and not
  another language: the ledger must exist once, in one place, and a renderer in a second
  language would either become a client of Python or a second implementation of the money.

---

## 1. Where the work currently stands

Branch `release-curate`, tip `3370f07`, `main` untouched at `8160d0b`, nothing merged, nothing
pushed. Venv interpreter: `D:\Hackerrank_Orchestrate\hackerrank-orchestrate-september26\.venv\Scripts\python.exe`.

Baselines: `pytest -q` → **365 passed**; `pytest -q tests/review` → **475 passed, 1 skipped**;
`output.csv` sha256 `1F35043F00ADDC8275D5879026FA83F6595A19712EE814AE89A194F353376486`.

| File | State | Purpose |
|---|---|---|
| `code/account.py` | **new, untracked** | Shape of one person's account + contract vocabularies + `account_data()` + `problems()` |
| `code/accounts.py` | **new, untracked** | SQLite store for named users |
| `docs/general-use-plan.md` | **new, untracked** | This file |
| `docs/improvement-report-2.md` | **new, untracked** | The findings behind this phase |
| `code/service.py` | committed | The engine: users, facts, position, decide, buy, history |
| `code/chat.py` | committed | The conversation: model chooses tools, kernel answers, no-provider says so |
| `code/paths.py` | committed | Resolves `mock_dataset/` first, falls back to `dataset/` |
| `code/solver.py` | committed | Gained `position(...)`: the only place a position figure is computed |
| `tools/app.py` | committed | The Textual interface: Position, Purchase, Ledger, Decisions, Report, Chat |
| `tests/review/test_general_use.py` | committed | 19 focused tests: account, store, engine, screens, conversation |

Phases 0, 1, 1b, 1c, 2 and 3 are done and committed; phase 4 is done except the release
gates, which run only after every phase is complete (the user's instruction). The Phase 1
command surface ships as screens in `tools/app.py` rather than as `user`/`add`/`buy` REPL
commands, because the chosen interface is Textual; `tools/tui.py` keeps its dataset commands as
the fallback and now prints a pointer to the new app.

Focused tests only, per the user's instruction: `pytest -q tests/review/test_general_use.py` ->
19 passed, `pytest -q tests/test_submission.py tests/test_hardening.py` -> 18 passed,
`pytest -q tests/test_submission.py tests/test_invariants.py tests/test_packaging.py
tests/test_hardening.py tests/test_evaluation.py` -> 50 passed. No full-suite run yet, and no
review rounds yet: both are gated on all phases being complete.

Release gates, now that the phases are complete: `python -m evaluation.run --mode pr` reports
PASS on G0 (15/15 structural checks), G1 (0 status upgrades, 0 safe overclaims), G3
(determinism over 25 rows) and G4 (provenance), and FAIL on G2 (sample quality, 3/25 full
rows) - the same G2 failure the accepted baseline has carried since `db364ba`.

One pre-existing fact worth recording: at `3370f07` the pipeline re-run produces
`BFF8447F...`, not the committed `1F35043F...`. The drift is in the batch explanation text and
exists without this branch's changes; the committed `output.csv` is restored and untouched.

---

## 2. The kernel contract (this is what makes the work additive)

`code/solver.solve_request(rid, user_id, req_date, req_amt, profile, events, fx_rows, options_df,
messages_df=None, desired_completion_date=None, allows_partial_payment=None,
installment_safety="strict")` — plain Python structures. **No kernel change is needed** to decide
a stranger's request.

The `data` dict the TUI and kernel share:

```python
{"requests": [dict, ...],                 # list of records, every value a str
 "profiles": {user_id: dict},
 "events_by_user": {user_id: [dict, ...]},
 "fx": [dict, ...],
 "options": <pandas DataFrame>,
 "messages": <pandas DataFrame>,
 "warnings": [...]}
```

Column headers, copied from the CSVs so the store cannot drift:

- `financial_profiles.csv` — `user_id, home_currency, current_available_balance,
  minimum_balance_to_keep, financial_priorities, expense_categories_to_protect,
  expense_categories_user_is_willing_to_reduce, expense_categories_user_is_willing_to_stop,
  payment_methods_user_will_consider, max_installment_months`
- `financial_events.csv` — `event_id, user_id, event_type, description, category, direction,
  amount, currency, event_date, settlement_date, status, linked_event_id, flexibility,
  minimum_allowed_amount`
- `requests.csv` — `request_id, user_id, request_date, request_type, requested_amount,
  desired_completion_date, allows_partial_payment, request_text`
- `request_payment_options.csv` — `payment_option_id, request_id, payment_method, payment_amount,
  number_of_payments, first_payment_date, payment_frequency_days, financing_fee,
  total_payable_amount`

### Vocabularies — a wrong word fails SILENTLY

| Column | Legal values |
|---|---|
| `direction` | `credit`, `debit`, `non_cash` — **not** inflow/outflow |
| `event_type` | `income`, `expense`, `subscription`, `debt_payment`, `investment_purchase`, `investment_sale`, `investment_valuation`, `refund` |
| `status` | `settled`, `pending`, `scheduled`, `failed`, `cancelled`, `unrealized` |
| `flexibility` | `fixed`, `reducible`, `reducible_or_stoppable`, `stoppable` |

Multi-value profile columns are pipe-separated: `rent|education|groceries`,
`full_payment|partial_payment|installments`.

Evidence of why this table is here: with `direction="inflow"` the solver returned
`amount_safe_to_pay: '900'` beside `affordability_status: 'not_affordable'` — the salary was not
counted, so the floor dominated, and nothing raised an error. With `credit`/`debit` the same
account returned `affordable_now` / `full_payment` / `900` / `2026-11-01`.

---

## 3. The API surfaces already built

### `code/account.py`

Constants: `ACCOUNT_VERSION`, `PROFILE_FIELDS`, `MULTI_VALUE_PROFILE_FIELDS`, `EVENT_FIELDS`,
`REQUEST_FIELDS`, `OPTION_FIELDS`, `DIRECTIONS`, `EVENT_STATUSES`, `EVENT_TYPES`, `FLEXIBILITY`,
`MULTI_VALUE_SEPARATOR = "|"`.

```python
default_path(repo_root=None) -> Path            # <repo>/data/account.json
blank_account(user_id="me", home_currency="") -> dict
load_account(path=None, repo_root=None) -> dict | None
save_account(account, path=None, repo_root=None) -> Path
as_list(value) -> list[str]                     # accepts commas, returns a list
as_contract_list(value) -> str                  # returns pipe-separated
_next_id(existing, prefix) -> str               # <prefix>_<n>, no collisions
set_profile(account, **fields) -> dict          # KeyError on an unknown field
add_event(account, **fields) -> dict
add_request(account, **fields) -> dict
add_option(account, request_id, **fields) -> dict
account_data(account) -> dict                   # the `data` shape in §2
problems(account) -> list[str]                  # missing/invalid facts, plain English
```

`problems()` on a blank account returns `current available balance is not set`,
`minimum balance to keep is not set`, `no money movements recorded, so nothing can be forecast`;
a negative balance returns `... cannot be negative`.

### `code/accounts.py` — SQLite

```python
default_db_path(repo_root=None) -> Path         # <repo>/data/accounts.db  (gitignored)
connect(path=None, repo_root=None) -> sqlite3.Connection
list_users(path=None, repo_root=None) -> list[str]
has_user(name, path=None, repo_root=None) -> bool
load(name, path=None, repo_root=None) -> dict | None    # None = unknown name
save(account, name=None, path=None, repo_root=None) -> str
create(name, home_currency="", path=None, repo_root=None) -> dict
load_or_create(name, home_currency="", path=None, repo_root=None) -> dict
delete(name, path=None, repo_root=None) -> bool
```

Tables: `meta(name, value)` holding `schema_version`; `profiles(user_name PRIMARY KEY, <profile
fields>, created_at, updated_at)`; `events`, `requests`, `payment_options` each
`(user_name, <entity fields>, seq)`. `save()` upserts the profile (preserving `created_at`) and
replaces the entity rows wholesale inside a transaction, so a crash cannot leave a half-written
ledger. `load()` returns `None` for an unknown name so callers can tell "new person" from "person
with nothing recorded".

Proved by execution: a ZAR account with a 12,000 balance, a floor, a settled salary, a streaming
subscription, one request and one payment option round-trips with `problems() == []` and decides
`affordable_now` / `full_payment` / `900` / `2026-11-01`; `delete` removes the user and leaves the
others; `load("missing")` is `None`.

---

## 4. Phases

### Phase 0 — land the foundation (smallest, do first)

1. Write `tests/review/test_accounts.py`: create/list/has/delete, unknown name → `None`,
   round-trip of profile + event + request + option, `save` preserving `created_at`,
   `save` replacing rather than duplicating rows, name normalisation rejecting empty/`>80`.
2. Write `tests/review/test_account.py`: contract-form storage (pipes, not commas), `problems()`
   on blank/filled/negative, the `account_data()` shape, unknown-field rejection, non-colliding
   ids, `save_account`/`load_account` round-trip, missing file → `None`.
3. Run the gate and the net; commit both modules and both tests together.

**Acceptance:** `pytest -q` still 365 passed; `pytest -q tests/review` grows by the new tests and
passes; `output.csv` byte-identical; `git status` clean.

### Phase 1 — the interface stops opening on the dataset

- `user <name>` — load or create, print what is known and what is missing (`problems()`).
- `users` — list stored names.
- With nobody stored, ask for a name, then one figure at a time: currency, balance, floor. Save
  after each answer so nothing is lost if the session ends.
- `add` — one fact per command: income, bill, subscription, refund, with a **default date of
  today** and the vocabulary validated before it is stored.
- `buy <amount> [--on DATE] [--by DATE]` — record the request, run the kernel, print the decision
  and the explanation.
- `options <id>` — the payment options already read by `show`, promoted to its own command.
- `dashboard` demoted: rename its output as the deliverable report and stop it being the default.

**Acceptance:** a fresh session with an empty database can go from nothing to a decision without
`dataset/` being read — prove it by pointing the loader at a temporary database and a temporary
repo root.

### Phase 1b — make the conversation actually agentic

This phase exists because `ask` is currently a lie. It calls `code.task_agent.run_task`, a
**rule-based classifier over six fixed families** (affordability, explain, list spending changes,
apply one, what-if, undo). With no provider it prints *"no model configured (...); classifying
with the rules"* and answers from those rules; outside the six shapes it says *"I did not
understand that clause."* No model reads the person's ledger, nothing is chosen by reasoning, and
there is no tool for it to choose. That is an FAQ bot with an agent's vocabulary, and calling the
product agentic while shipping it is the same defect as opening on the fixture file — the shape of
the thing contradicts the claim.

The real loop already exists and is unused here: `code/agent.py` has `run_agent_loop`, a bounded
tool-calling loop with timeouts, `record_tool_call`, trace recording and a call cap
(`MAX_PROVIDER_CALLS = 50`). **No new reasoning machinery is needed — `ask` has to be wired to
it.**

What that means concretely:

1. **Tools over the account, not over strings.** Give the loop a small tool set whose arguments
   are validated by `code/account.py` before anything is stored:
   `position()` → the kernel's position figures; `list_requests()`, `list_events()`,
   `add_event(...)`, `add_request(...)`, `decide(request_id)` → the kernel decision,
   `options(request_id)`, `what_if(request_id, **changes)` → a re-solve on a copy,
   `apply(request_id, action)`, `undo()`.
2. **The model chooses, the kernel decides.** Every machine field a tool returns comes from
   `solve_request`. `propose_decision` and `enforce_decision` keep their current role: the model
   may propose, and the verifier owns the column values. No tool returns a number the kernel did
   not compute.
3. **Say what is happening.** When a tool runs, print which one and why, in one line. When no
   provider is configured, say so plainly — "no model is configured, so I can only run the
   commands; set a provider to talk to me" — and do **not** present rule output as if it were a
   conversation. Honest silence beats a confident FAQ bot.
4. **The rules stay as a fallback, labelled as one.** `task_agent.run_task` remains for the
   no-provider path and for the tests that pin its behaviour, but the interface routes to the loop
   whenever a provider exists.
5. **A test that proves reasoning, not matching.** Pin one conversation that cannot be answered by
   the six families — for example "if I buy this now, what does that do to my rent next week?" —
   and assert the loop called `position`, then `what_if`, and that the answer cites a kernel
   figure. A test that only checks a phrase reaches a handler proves nothing about agency.

**Acceptance:** with a provider configured, a question outside the six families is answered from
the ledger through tools, with the tool calls visible; with no provider, the interface says it has
no model instead of guessing; and no tool can move a machine field (the existing
`enforce_decision` tests must still pass unchanged).

### Phase 1c — the Textual interface (the chosen stack)

The current screen is Rich panels printed to a terminal. It is correct and it looks stale, and
"stale" was the complaint. The answer is **Textual**, not a rewrite in another language.

**Why Textual, and why not the alternatives.** Every figure about a person's money comes from
`solve_request` and is gated by `verify_claims`/`enforce_decision`. A UI in another language would
either speak to Python anyway - so the new language is only a renderer, and you pay for two stacks
to get what Textual gives in one - or reimplement the kernel, which would give the product two
sources of truth for someone's balance. That is the single worst outcome available, worse than a
stale-looking dashboard. A local web UI was the other serious candidate: it looks the best, but it
needs a bound HTTP surface and shipped assets, and §6.4 requires the offline path to work with no
network dependency. Textual stays in one process, in one language, and still gives real widgets,
layout, mouse, modals and CSS-like styling.

**Dependency discipline.** `textual` is a new dependency and it must stay optional:

- `tools/tui.py` (Rich) keeps working and becomes the fallback when `textual` cannot be imported,
  printing one line that says so rather than failing.
- No test may require a real terminal. Textual ships `App.run_test()` / `Pilot`, which drives the
  app headlessly; that is how the screens get tested.
- `python -m code.main` and the release gate never import the UI module.

**Structure: one engine, one view.** `code/service.py` is the engine — `position()`, `decide()`,
`add_event()`, `add_request()`, `buy()`, `list_users()`, `what_if()`, `apply()`, `undo()` — returning
kernel values with their assumptions attached. The Textual app renders it and computes nothing.
Phase 2 is the same work seen from the other side: the `position` entry point in `code/solver.py`
lands first, `code/service.py` wraps it, and the Position screen is a view of that.

**Screens.**

1. **Onboarding** — ask for a name, then currency, balance and floor, one question at a time,
   saving each answer immediately. Resuming an existing name goes straight to Position.
2. **Position** (the home screen, replacing the `output.csv` census) — available balance, floor,
   what is already committed before the next confirmed income, and the largest one-off payment that
   stays above the floor, each with its assumptions and the date it is computed as of.
3. **Purchase** — `buy <amount> [--on DATE] [--by DATE]` as a form, then the decision with the plan,
   the earliest safe date and the explanation.
4. **Ledger** — the recorded facts, editable one at a time; adding an income, bill, subscription or
   refund writes to the store immediately, no batch entry.
5. **Decisions** — the requests and their outcomes for this person.
6. **Report** — the old `dashboard`, demoted and labelled as the fixture/deliverable report.
7. **Chat** — the agentic loop from Phase 1b, with each tool call shown as it happens.

**Acceptance criteria.**

- The app opens on Position for the current user, and on Onboarding when no user is stored. It never
  opens on `dataset/`.
- A headless `Pilot` test drives onboarding with a name and three figures, then asserts the Position
  screen shows the figures the kernel returns for that account — not values computed in the test.
- A second `Pilot` test buys something affordable and asserts the decision text and plan match
  `solve_request`.
- The TUI module contains no arithmetic over money (the `position` numbers arrive already computed).
- With `textual` absent, `python -m tools.tui` still runs the Rich interface and says it is the
  fallback.
- `pytest -q` stays green at its current count, and the new UI tests live under `tests/` (they are
  part of the gate) or `tests/review/` if they need a terminal adapter — decide when writing them,
  and record which.

### Phase 2 — `position`, with the figures from the kernel

Add an entry point to `code/solver.py` (not to `tools/tui.py`) that returns the position for a
user and a date: balance, floor, committed spending before the next confirmed income, and the
largest one-off payment that stays above the floor. **Test it directly.**

`position` prints those figures with their assumptions: conservative, before optional spending
changes, over the projection window, as of the stated date.

**Acceptance:** a unit test pins each figure on a hand-built account; the TUI contains no
arithmetic over money.

### Phase 3 — the dataset becomes a fixture

`python -m code.main` keeps reading `dataset/` for the release gate and stays byte-identical. The
TUI stops reading it. `tools/tui.py`'s `_ensure_data()` should resolve an account first and fall
back to the fixture only when explicitly asked.

**Acceptance:** `output.csv` unchanged; the gate unchanged; a TUI session never loads `dataset/`
unless requested.

### Phase 4 — documentation and gates

- `AGENTS.md` §1: say plainly that `dataset/` is calibration material.
- `README.md`: a "Use it on your own money" section with the real commands.
- `evaluation.run --mode pr`, `tools.build_release`, one CodeRabbit deep pass.
- `docs/improvement-report-2.md` §3 updated from "untracked" to committed, with the counts.

---

## 5. Rules that apply throughout

- Money figures only from the kernel. No summing in the view layer.
- Every figure carries its assumptions.
- Reject a bad vocabulary word at the boundary, next to the person who typed it — never let it
  reach the solver, where it becomes a wrong number instead of an error.
- One commit per change, each revertible; `pytest -q` before claiming anything works.
- `dataset/` is never edited; `dataset/output.csv` stays the blank template.

## 6. Tooling facts worth keeping

PowerShell inline `python -c` breaks on nested quotes and on `|` (a shell operator) — write a
temp `.py` under `C:\Users\zaidk\AppData\Local\Temp\opencode\` and run it with the venv
interpreter. CRLF-normalise edited files before committing via
`[System.IO.File]::WriteAllText($f,$s,(New-Object System.Text.UTF8Encoding($false)))`. A literal
`event_<digits>` in a `code/` comment trips `tests/test_submission.py`. The G2 gate fails at the
unchanged accepted baseline since `db364ba`, so `evaluation.run --mode pr` reports `OVERALL: FAIL`
while G0/G1/G3/G4 PASS — pre-existing, not caused by this branch.
