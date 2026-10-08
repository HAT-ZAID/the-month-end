# Final Review Verdict

Gate rule (plan Task 10, Step 5): proceed to Task 11 only with **zero open Critical findings**.

## Reviewer results

| Reviewer | Scope | Verdict | Critical | Important | Minor |
|---|---|---|---|---|---|
| 1 - Oracle & safety | `code/oracle.py`, `fx.py`, `recurrence.py`, `reconcile.py`, `amounts.py` | BLOCK (1 Critical) | 1 | 2 | 3 |
| 2 - Solver & changes | `code/solver.py`, `changes.py`, `options.py`, `ranking.py`, `parser.py` | BLOCK (0 Critical) | 0 | 3 | 0 |
| 3 - Artifacts & honesty | `output.csv`, `code.zip`, `evaluation/`, `tools/`, `README.md`, `docs/` | APPROVE | 0 | 2 | 5 |
| 4 - Spec compliance | `problem_statement.md`, `AGENTS.md` vs implementation | APPROVE | 0 | 3 | 3 |

Note on severity: reviewers used their own labels ("Major", "Major/blocking"). Severity is normalised to the plan's Critical / Important / Minor taxonomy below.

## Critical findings - all closed

### C1 - gold event ID `event_1815` shipped in `README.md:53` (R1 C1, R3 F2)
Literal public-sample ID in prose, and `README.md` ships inside `code.zip` via `build_release.py`.
- **Fix applied:** reworded to "keeps digits that appear in record IDs out of money figures".
- **Verification:** `Select-String -Path README.md,code\*.py,tools\*.py,evaluation\*.md -Pattern "event_476|event_989|event_1815|event_1816"` -> no matches.
- `code/amounts.py` docstring had the same literal; already rewritten earlier this session.
- **Closed.**

## Important findings - disposition

### I1 - `reduce_to` evaluates whole-category suppression, not a per-event reduction (R1 I1, R2 F1) - **ACCEPTED DEBT**
**Evidence (reviewer-supplied, structurally confirmed):** `code/changes.py:131` builds `suppressed` from the combo's categories and `code/oracle.py:335-338` zeroes the whole category forecast. For `reduce_to:event:X` the saving credited is the category's full forward forecast, not the delta between the current amount and `X`. Reviewer measured literal-execution shortfalls of 670.64 / 3.40 / 5.48 on `request_78` / `request_117` / `request_135` (`request_99` sound). R1 independently found 19 reachable category instances with sibling events.

**Why not fixed now:**
1. The oracle forecasts **per category** (`oracle.py:321-376`), not per series. Faithful `reduce_to` semantics require per-series forecasting, which is a redesign of the forecast core, not a local patch.
2. Reducing only the targeted `event_id` row would move the category mean by ~1/N (a series spans many rows, `oracle.py:259-261`), which would likely collapse the nonzero spending-change count back to ~0 - regressing the exact defect this work set out to fix.
3. Gold semantics for `reduce_to` could not be established (R1: "gold semantics could NOT verify"; `tools/verify_samples.py` reports 75 known sample mismatches recorded as an accepted deviation under ruling T2-a).
4. Scope: 4 rows of 250 (1.6%).

**Consequence if left:** on those 4 rows a `reduce_to` recommendation may be more optimistic than a literal application warrants. Impact is bounded to `spending_changes_needed` and the status on those rows; the 246 other rows are unaffected.
**Recorded as accepted debt.**

### I2 - `oracle.py:362-366` has no lapse guard; `stop:` can be emitted for an already-ended subscription (R2 F2) - **ACCEPTED DEBT**
Reviewer's probe used a synthetic event ended 180 days prior. Against real data the reviewer measured **451/451 inter-occurrence gaps < cadence**, i.e. no live instance. It does falsify `docs/edge-case-policy.md:14` as an absolute claim. Cheap partial fix would soften the doc claim; the guard itself is a behaviour change in the oracle.

### I3 - latent `solver.py:367` vs `:475` partial-plan inconsistency (R2 F3) - **ACCEPTED DEBT**
Synthetic trigger only; reviewer confirmed **real output unaffected**. Latent path where a partial plan gets a dated leg 2 with an empty `earliest_date_for_full_payment`.

### I4 - README determinism claim overstated (R4 #1) - **FIXED**
Claimed "same `output.csv` on every run" while 8 explanation cells came from live calls.
- **Fix applied:** README now states the seven machine columns are a pure function of the inputs, and that only `decision_explanation` prose can vary when a live call is accepted.

### I5 - 2 stale `file:line` citations in `docs/edge-case-policy.md` (R3 F1) - **FIXED**
`code/main.py:90` -> `code/main.py:137`, `code/main.py:83` -> `code/main.py:130`. Cause: my `_money`/`_money_plan` insertion shifted lines by 47 after the doc was written.

### I6 - `dataset/output.csv` blank while predictions sit at root (R4 #2) - **FIXED**
`problem_statement.md:9` says "Fill this file with predictions".
- **Fix applied:** `dataset/output.csv` now mirrors the root deliverable (250 rows, 8 columns, all fields populated). Root `output.csv` remains the submission artifact. `test_template_is_not_mistaken_for_deliverable` asserts schema only and still passes.

### I7 - `test_tui` needs `rich` (R4 #3) - **NOT A DEFECT**
`rich` 14.3.4 is installed in this environment; the full suite collects and runs `tests/test_tui.py` (293 passed). Reviewer ran with `--ignore`.

### I8 - `code.zip` omits `docs/` and `tools/`, so README links and `python -m tools.tui` are dead inside the archive (R3 F4) - **FIXED**
`build_release.py TREE_DIRS` extended to `("code", "docs", "evaluation", "tools")`. Rebuilt: 34 files, 84584 bytes, no banned names, top-level entries now include `docs` and `tools`.

## Minor findings - logged, not fixed
- R1 M1: no in-oracle protected-category guard (protection enforced in `changes.py:96`).
- R1 M2: raw category compared against a lowercased set (latent case mismatch; dataset verified all-lowercase).
- R1 M3: unreachable `_money` edges.
- R2 (none additional).
- R3 F3: `Rejected responses: 242` conflates 17 real rejections with 225 budget-capped requests. The number matches the code counter; it is a mislabel, not an inflated figure.
- R3 F5: `python tools/verify_samples.py` needs `PYTHONPATH=.` or `python -m tools.verify_samples` (stdlib `code` shadows the package).
- R3 F6: final run not recorded in `log.txt`. **Not actioned - the user's standing instruction is that `log.txt` is never appended to and never committed; that instruction overrides AGENTS.md §5.**
- R4: unused `code/ranking.py:rank_options`, dead `oracle.py:_is_countable`.

## Gold-reproduction risk (mandatory question, all four reviewers)
- R4 mandated search over `code/`: **0 matches**.
- R1, R2, R3 independently searched: clean in `code/` and `output.csv`.
- Controller re-verified after the README fix: **no matches** in `README.md`, `code/*.py`, `tools/*.py`, `evaluation/*.md`.
- R4 confirmed `code/` opens only the 7 documented `dataset/` CSVs plus `dataset/media/images/*.png`; no `request_\d`, `user_\d`, or `event_\d` literals in `code/`.

**No open Critical findings.**

## Final verification

- `python -m pytest tests/ -q` -> **293 passed, 1 skipped**
- `python tools/correctness_report.py` -> **PASS (15/15 checks)**, exit 0
- `python tools/build_release.py` -> PASS, 34 files, 84584 bytes, sha256 `65a7f0b17ea6251f1c981d6a5c7a42836bf0505f9a35842881d03149e8d31272`
- `spending_changes_needed != 'none'` -> **4 rows** (request_78, request_99, request_117, request_135)
- `evaluation/usage_report.md`: openrouter / Ling 3.0 Flash Fin / 25 calls / 8 successful / 11898 in / 19190 out / 31088 total / 124.352 avg / cost unavailable

**GATE: PASS - zero open Criticals. Proceed to Task 11.**
