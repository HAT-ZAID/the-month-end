# Handover state — where the work stands

Written to survive a context reset. Read this first, then
`docs/g2-hypothesis-report.md` for the G2 investigation and the ruled-out
register. `docs/architecture.md` is the module map and data flow; `README.md` is
the user-facing description.

**Date:** 2026-11-04.
**Branch:** `release-curate`. `main` untouched. Nothing pushed, nothing merged.
**Base at the time of this record:** `c667358` (the documentation commit sits on
top).

---

## 1. Start here

1. Read this file.
2. Read `docs/architecture.md` for the current module list and entry points.
3. Read `docs/g2-hypothesis-report.md` before touching the forecast for G2. Its
   ruled-out register is exhaustive; do not re-run anything in it.
4. Re-run the verification in §5 before claiming the tree is releasable. The
   counts there are a snapshot, not a certificate: for any later run, see the
   release record.

The product is **Buy or Wait?**: a local-first financial decision assistant. The
deterministic kernel owns every machine field; the agent layer may explain,
classify intent and propose — never compute. `AGENTS.md` is the repo contract;
this document records state, not rules.

---

## 2. What is committed

39 commits on top of `3370f07` at the base commit above (this documentation
change is the next one), grouped by area:

| Area | State |
|---|---|
| Calibration fixture | Renamed to `mock_dataset/`; every reader resolves through `code.paths` (`mock_dataset/` first, `dataset/` fallback); `mock_dataset/output.csv` stays blank |
| Named users | `code/account.py` (account contract), `code/accounts.py` (SQLite `data/accounts.db`), `code/service.py` (position/decide/buy/add_fact/history/update_profile) |
| Product UI | `tools/app.py` (Textual: Onboarding, Position, Purchase, Ledger, Decisions, Report, Chat); `tools/tui.py` is the Rich fallback and its dashboard is labelled as mock data |
| Chat | `code/chat.py` chooses among six kernel-backed tools and returns an honest `NoModel` when no provider is configured |
| Models | `code/models.py` discovers what this machine can run; loopback URLs are parsed, documented provider env keys are read, and cloud entries are labelled |
| Safety | Oracle fails closed on unknown direction/status/date, signed-negative amount, invalid reserve/balance, and FX/accumulation overflow; solver refuses a non-finite forecast; service refuses a position over an unknown balance |
| Forecast | Incomplete request month excluded (always on); the MTD and real-month corrected arithmetic was removed and deferred; salary projected on its calendar payday |
| Scenarios | `code/scenarios.py`: deep-copy `pay_on` / `delay_days` / `change_expense`, kernel re-decide, `persisted: False` |
| G2 investigation | `tools/g2_probe.py`, `tools/g2_model_mining.py`, `tools/g2_transfer_study.py`; findings in `docs/g2-hypothesis-report.md` and `docs/g2-root-cause-plan.md` |
| Onboarding | A blank balance or reserve stays unknown; nothing defaults to zero |

Recent commits, newest first, for orientation:

```text
c667358  fix(app): run chat in a cancellable worker and never guess the account
ec6a784  fix(solver): refuse a position over an unknown balance instead of reporting zero
074bef7  fix(onboarding): keep a blank balance unknown instead of zero
d769288  fix(oracle): fail closed when accumulation or the opening balance overflows
0adcc70  fix(app): ask for a fact's category and monthly pay in the Ledger form
7c88ed6  fix(task-agent): answer why-questions, refuse negated actions and invalid confidence
d9063cd  fix(oracle): fail closed when an amount overflows during FX conversion
876254e  fix(forecast): keep the conservative bucket charge as the default; gate the corrected arithmetic behind an opt-in flag
bef01a3  fix(service): map recorded facts onto categories the forecast actually projects
773c0d2  feat(scenarios): simulate a hypothesis on a throwaway account copy
a857195  fix(models): parse the loopback host and read the documented provider keys
f4d1d2b  fix(forecast): exclude the incomplete month, charge the unspent remainder, prorate on real days
07acae5  test: add a scripted provider double for the chat protocol tests
53d23d6  fix(solver): refuse a non-finite forecast instead of ranking on it
d453f29  fix(oracle): fail closed on unknown direction, signed amount, invalid reserve and undated active rows
```

---

## 3. Entry points

All are documented in `README.md` and pinned by
`tests/test_docs_entrypoints.py`, which parses every `python -m ...` command out
of the README and asserts the module resolves.

| Command | Role |
|---|---|
| `python -m code.main` | Batch: decides every request and writes `output.csv` plus the usage report |
| `python -m evaluation.run --mode pr` | Gates G0–G4; writes `evaluation/report.{json,md}` and **does not rewrite `output.csv`** (`write_output=False`) |
| `python -m tools.app` | Textual product interface |
| `python -m tools.tui` | Rich fallback interface |
| `python -m code.task_agent "<instruction>"` | One plain-language task over the kernel |
| `python -m tools.trace_view --trace-dir DIR --out PAGE` | Static HTML trace viewer |
| `python -m tools.g2_probe` | Replay the 25 sample requests for G2 experiments |
| `python -m tools.g2_model_mining` | Leave-one-out model-mining lab over the labelled rows |
| `python -m tools.g2_transfer_study` | Fit on the 25 labelled rows, apply to the 250 real requests |
| `python -m code.agent --request-id ID --provider KIND` | Single-request agent debug run |
| `python -m code.cache --stats` | Inspect/sweep the OCR/VLM cache |
| `python -m code.store --rebuild` | Rebuild `data/app.db` |

`code.main` rewrites `output.csv`; the gate does not. If a verification run needs
the deliverable untouched, use the gate and restore the file with
`git checkout -- output.csv` only if something else changed it.

---

## 4. Contracts that are now enforced

### Safety

The oracle fails closed instead of guessing. The named reasons are
`DIRECTION_UNKNOWN`, `STATUS_UNKNOWN`, `DATE_UNKNOWN`, `AMOUNT_INVALID`,
`FX_UNKNOWN`, `RESERVE_INVALID` and `BALANCE_INVALID`; `tests/test_oracle_contract.py`
pins each one. A fail-closed ledger becomes a conservative row with
`amount_safe_to_pay = 0`, `not_affordable`, `not_recommended`, `payment_plan = none`
and an explanation that names the reason. The checks cover a finite `Decimal`
that overflows only after FX conversion, and the opening balance or a same-day
sum overflowing the projection.

### Forecast

The corrected first-bucket arithmetic (charge the unspent remainder; prorate on
the real month length excluding the request day) lowers the day-0 charge and
raises the safe amount; measured against the labelled rows it produced 2 status
upgrades and 15 safe overclaims. It was **removed from the code** and deferred,
not left behind a default-off environment switch, because a switch inside the
shipped decision path made `output.csv` bytes depend on the shell. The shipped
arithmetic keeps G1 at **0 status upgrades / 0 safe overclaims / 0 unparsed
amounts**. The incomplete-request-month rule is always on because it measured
G1-neutral. Do not restore the corrected arithmetic before a safety-margin
design resolves that conflict.

### Scenarios

`code/scenarios.py` deep-copies the account, applies one hypothesis and calls
`code.service.decide` on the copy. The result reports `"persisted": False`; the
caller's account is unchanged. An unsupported kind, a field the kind cannot
apply, a missing request/event, an unparsable date or amount, and a re-pricing of
an income row are refused with `ValueError`. It is a library API today; the TUI's
`whatif` is a separate session overlay and no interface persists a scenario.

---

## 5. Verification record

Observed on 2026-11-04, on `c667358` plus this documentation change, with the
repository venv:

| Command | Result |
|---|---|
| `pytest -q` | **529 passed** |
| `pytest tests/review -q` | **499 passed, 1 skipped** |
| `pytest -q tests/test_docs_entrypoints.py` | **4 passed** |

Counts move as tests are added; for any later run, see the release record.

The last recorded full PR gate run (during the forecast work, before the later
app/solver hardening commits) reported G0 PASS 15/15, G1 PASS 0/0/0, **G2 FAIL**,
G3 PASS and G4 PASS. That is historical, not a current certificate: re-run
`python -m evaluation.run --mode pr` before release and record the actual
verdicts. G2 stays reported as FAIL by design; it is an unresolved external
contract, not a gate to silence.

`output.csv` is the 250-row release output with sha256
`1F35043F00ADDC8275D5879026FA83F6595A19712EE814AE89A194F353376486`
(git blob `aaec7ac5bfda290f24a2646c211177a96c8bca4d`). The documentation change
does not touch it.

---

## 6. G2 in one paragraph

G2 compares the 25 sample decisions against the organiser's labels and fails at
3/25 whole rows. Two real forecast defects were fixed on the way (income payday
drift and a payday cap below the number of paydays in a 90-day window) and three
more were confirmed (month-to-date double count, off-by-one proration behind a
fixed `/30` clamp, and the partial request month counted as history). The fixes
move the median amount error from about 81% to about 13%, but across roughly 250
scored configurations — including a held-out split and every structural,
estimator, horizon, window-start, status and income variant tested — **no sample
row whose gold amount is strictly between zero and the requested amount was ever
reproduced**. That is why G2 is treated as an unresolved external contract: the
gold amounts are not generated by this forecast family, and the organiser's
reference arithmetic is not in the repository.

---

## 7. Decisions the user has made

- The interface is **Textual** (not a web UI, not another language); `tools.tui`
  stays as the Rich fallback.
- The fixture is calibration material and is now `mock_dataset/`.
- Fitting the forecast to the 25 labels is allowed **as a diagnostic only**, never
  as a shipped result.
- Models: `gemma4:31b-cloud` is the primary reasoner for agentic work;
  `gpt-oss:20b-cloud` is demoted because its tool selection was unreliable;
  `gemma4:e2b-it-qat` is the local default; `llama3.2:3b` and `qwen3-vl` are
  unreliable for accounting; Kilo only if all fail; no parallel model jobs.
- Cloud models are non-local: fixtures only, never an account from
  `data/accounts.db`.
- The release gate G1 is non-negotiable: the corrected forecast arithmetic stays
  behind its flag until a safety margin is designed.

---

## 8. Known traps

- PowerShell inline `python -c` breaks on nested quotes and on `|`. Write a temp
  `.py` under `C:\Users\zaidk\AppData\Local\Temp\opencode\`, or pipe a here-string
  into `python -`.
- `code/oracle.py` appends forecast entries to `processed_events`; a rebuild must
  not add `forecast` a second time (the old "ledger anomaly" was a harness bug,
  not a repository bug).
- A literal `event_<digits>` in a `code/` comment trips `tests/test_submission.py`.
- The Rich TUI's `_ensure_data` loads the fixture on first use; it is the fallback,
  not the product. `python -m tools.app` is the product interface.
- The evaluation PR run does not rewrite `output.csv`; a direct
  `python -m code.main` does.
- `.env` is not loaded by any code. It may contain operator values for the local
  probes; never print or commit them.
- Cloud model entries (`gemma4:31b-cloud`, `gpt-oss:20b-cloud`) are absent from
  `/api/tags`; they must be added to the model menu explicitly.
- `gpt-oss:20b-cloud` tool selection is unreliable — do not use it as the tool
  chooser. If it is used at all, `think=False` keeps the turn cheap.
- The G2 labs are investigation tools: they are not imported by production code,
  and they can leave an untracked `catboost_info/` behind. Delete it; never commit
  it.
- `tests/review/test_general_use.py` has a Textual timing test that occasionally
  flakes. Re-run once before calling it a failure.

---

## 9. Remaining work

1. **Release verification.** A fresh `pytest -q`, `pytest tests/review -q` and
   `python -m evaluation.run --mode pr` on the final tree; record every gate
   verdict, including the expected G2 FAIL, with the link to
   `docs/g2-hypothesis-report.md`. Re-run the review's adverse inputs and record
   that each fails closed.
2. **Review process.** CodeRabbit deep review on the branch until clean, then the
   two independent reviews the repo requires (correctness/determinism and
   security/privacy), then `git merge --no-ff` and push.
3. **Not yet on this branch.** The remaining review-remediation work (chat
   answer verification and typed run state, agent timeouts, account validation,
   scenario guards and baseline comparison, FX persistence, and the later chat
   hardening items) is still outstanding. The `chat.py` chain and the
   `service.py` / `tools/app.py` chain share files and must stay serial.
4. **Forecast flag.** The corrected arithmetic needs a safety margin or an
   explicit reconciliation with the reference before it can become a product
   default.
5. **G2.** Keep it reported as FAIL. Do not fit the 25 labels, do not special-case
   a row, and do not add a new gate that hides the failure.
