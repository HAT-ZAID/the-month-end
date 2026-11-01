# Handover state — where the work stands

Written to survive a context reset. Read this first, then `docs/g2-root-cause-plan.md` for the
deep G2 investigation, hypotheses and test programme.

**Date:** 2026-11-02 (session time).
**Branch:** `release-curate`. `main` untouched. Nothing pushed, nothing merged.

---

## 1. Start here

1. Read this file.
2. Read `docs/g2-root-cause-plan.md` §4 (established defects) and §5 (ruled-out register). Do not
   re-run anything in §5.
3. Pick the next action from `docs/g2-root-cause-plan.md` §9 (decision tree). The highest-value
   check not yet run is **H19/H20** — whether the labels are consistent with today's event data and
   with the output contract. If they are not, G2 is unreachable by any model change and that must be
   reported rather than engineered around.

---

## 2. What is committed (12 commits on top of `3370f07`)

```
fa8249f  test: prove the model menu and keep discovery out of the test path
1639969  feat: a model selector that lists what this machine can actually run
922c16b  docs: record the release-gate result next to the phase status
0ae3adf  fix: the correctness report runs as a script again, so gate G0 passes
0a85f5b  fix: every reader resolves the fixture through code.paths
9eee1c1  fix: the fallback TUI loads again, and its dashboard is labelled as mock data
2eabe29  test: drive every tab and a real purchase through the screens
d1d7e41  feat: the conversation chooses account tools, and says so when it has no model
2df2dde  docs: the fixture is calibration material and the product has its own entry point
d406151  refactor: the calibration fixture is mock_dataset, and the screens say so
536331c  feat: kernel position entry point, engine and Textual interface
d675abf  feat: account shape and SQLite store for named users
```

### What each area now contains

| Area | State |
|---|---|
| `code/account.py` | Contract vocabularies, account dict, `account_data()`, `problems()` |
| `code/accounts.py` | SQLite store (`data/accounts.db`, gitignored), create/load/save/list/delete |
| `code/service.py` | Engine: `position`, `decide`, `buy`, `add_fact`, `history`, `update_profile` |
| `code/solver.py` | Gained `position(...)` — the only place a position figure is computed |
| `code/chat.py` | Tool-calling conversation: `position`, `list_events`, `list_requests`, `decide`, `what_if`, `facts`; honest `NoModel` |
| `code/models.py` | Local model discovery (Ollama on loopback only), `provider_for`, free-tier labelling |
| `code/paths.py` | `mock_dataset/` first, `dataset/` fallback |
| `tools/app.py` | Textual interface: Onboarding, Position, Purchase, Ledger, Decisions, Report, Chat |
| `tools/tui.py` | Rich fallback, dashboard relabelled as the calibration report |
| `tests/review/test_general_use.py` | 24 focused tests: account, store, engine, screens, chat, model menu |
| `mock_dataset/` | Renamed from `dataset/`; readers resolve through `code/paths.py` |

### Verification at `fa8249f`

- `pytest -q` → **365 passed**.
- `pytest -q tests/review` → **499 passed, 1 skipped**.
- `python -m evaluation.run --mode pr` → G0 15/15 PASS, G1 PASS, G3 PASS, G4 PASS, **G2 FAIL**,
  OVERALL FAIL (G2 is the pre-existing failure; see the G2 plan).
- `output.csv` sha256 `1F35043F00ADDC8275D5879026FA83F6595A19712EE814AE89A194F353376486` unchanged.
- Working tree clean.

---

## 3. What is done but not reviewed

- **CodeRabbit:** not run on any of the 12 commits. CLI 0.9.0 is installed at
  `C:\Users\zaidk\AppData\Local\Programs\coderabbit\coderabbit.exe`.
  Suggested invocation: `coderabbit review --deep --committed --base main`, iterate until clean.
- **Two independent subagent reviews:** not started. Briefs planned:
  1. correctness/determinism — every path that can move a field, rounding, ordering, defaults;
  2. security/privacy — provider classes, secrets, trace content, the free-tier warning firing.
- **TUI visual check:** the alignment changes are tested headlessly (`Pilot`), not seen on screen.

---

## 4. G2 in one paragraph

G2 compares our 25 sample decisions against the organiser's labels and fails at 3/25 whole rows
(amount 3/25, status 14, method 14, plan 12, earliest 11). The investigation established two real
defects: bills already settled earlier in the month are charged again as a prorated remainder, and
the proration itself adds a day and divides by a fixed 30, with a clamp hiding the result. Fixing
those, excluding the partial request month and subtracting month-to-date spend only for recurring
bills, moves the median error from 81% to 13% but still matches no informative row exactly. About
90 tested configurations, including a held-out split, produced zero exact matches, so this is not a
parameter-tuning problem. The remaining possibilities are catalogued as hypotheses H1–H20 in
`docs/g2-root-cause-plan.md`; H19/H20 (label/data or label/contract inconsistency) decide whether
G2 is reachable at all.

---

## 5. Decisions the user has made

- The interface is **Textual** (not a web UI, not another language).
- `dataset/` is calibration material; it is now `mock_dataset/`.
- Fitting the forecast to the 25 labels is allowed **as a diagnostic only**, not as the shipped
  result.
- Models: local first (`gemma4:e2b-it-qat` is the default and the only local model that answered
  bill-semantics questions correctly); Kilo only if local models fail; no parallel model jobs.
- CodeRabbit passes, then two subagent reviews, then the gates — after the G2 work.

---

## 6. Known traps

- PowerShell inline `python -c` breaks on nested quotes and on `|`. Write a temp `.py` under
  `C:\Users\zaidk\AppData\Local\Temp\opencode\` or pipe a here-string into `python -`.
- `code/oracle.py` appends the forecast entries to `processed_events`; a rebuild must not add
  `forecast` a second time (this was the "ledger anomaly" — a harness bug, not a repository bug).
- The sample pipeline can be replayed without running it: raw events + five image-resolved amounts
  reproduce the recorded output 25/25. See the G2 plan Appendix A.
- A literal `event_<digits>` in a `code/` comment trips `tests/test_submission.py`.
- The Rich TUI's `_ensure_data` loads the fixture on first use; it is the fallback, not the product.
- `python -m tools.app` is the product interface; `python -m tools.tui` is the fallback.
- The evaluation PR run does not rewrite `output.csv`; a direct `python -m code.main` does (restore
  it afterwards with `git checkout -- output.csv`).
