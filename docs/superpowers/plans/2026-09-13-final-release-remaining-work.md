# Final Release + Remaining-Work Plan (Buy or Wait?)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline (time-boxed). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the last gap between the verified code (`25fa78d` `431f53b` `45a766b`) and the submitted artifacts before the 18:00 IST deadline.

**Architecture:** No behavioral changes. Regenerate `output.csv` from the current solver (which now includes Tasks 1–3), rebuild `code.zip`, refresh both usage reports, and update `plan_a.md` so every artifact dates from the final run.

**Tech Stack:** Python 3.11, pytest, PowerShell `Compress-Archive`, pandas.

**Spec:** `problem_statement.md` (Submission section) + `AGENTS.md` §6.4/§6.5.

## Global Constraints

- Zero behavioral edits to `code/` unless a verification gate fails.
- `output.csv`: 250 rows, exact column order, LF, UTF-8, no BOM.
- `code.zip` must contain `evaluation/usage_report.md` describing the SAME run that produced `output.csv`.
- No secrets in any artifact; `log.txt` stays untracked and append-only.
- Timebox: all tasks complete by 17:00 IST; if anything fails, submit the current green artifacts instead of risking a broken archive.

## Verified current state (evidence)

| Item | State |
|---|---|
| pytest | 276 passed, 1 skipped (41.4s, run 15:56 IST) |
| output.csv | regenerated 15:53 — AFTER commit 25fa78d and Task 3 report |
| code.zip | built 14:39 — STALE (predates Task 1/3 solver+OCR changes) |
| evaluation/usage_report.md + tools/evaluation/usage_report.md | describe 1 failed provider call, 249 cap rejections, 250 fallbacks |
| image OCR | 15/16 resolved; image_14 (event_9421) unresolved, fail-closed |
| git branch | sol/p1-inputs-oracle, commits 431f53b→45a766b→25fa78d |

## Task 1: Final full run

**Files:** Modify `output.csv`, `evaluation/usage_report.md`, `tools/evaluation/usage_report.md`

- [ ] Run `python code/main.py` from repo root with `BUY_WAIT_LLM_MAX_CALLS=0` (deterministic, timeout-safe).
- [ ] Verify: `python -c "import pandas as pd; d=pd.read_csv('output.csv'); r=pd.read_csv('dataset/requests.csv'); assert len(d)==len(r) and set(d.request_id)==set(r.request_id); print(len(d))"` → prints `250`.
- [ ] Confirm both usage reports now name this run (calls/fallbacks/tokens/cost).

## Task 2: Rebuild code.zip

**Files:** Modify `code.zip`

- [ ] Ensure `evaluation/usage_report.md` (repo root copy used for packaging) equals the fresh report from Task 1.
- [ ] Rebuild: zip `code/` (excluding `__pycache__`), `README.md`, `evaluation/usage_report.md` via `Compress-Archive` or python `zipfile` with forward-slash names.
- [ ] Verify archive listing contains exactly the 3 required roots and no `.env`/`__pycache__`/keys.

## Task 3: Final verification gates

- [ ] `python -m pytest -q` → expect 276 passed, 1 skipped (or better).
- [ ] `python -m pytest tests/test_submission.py tests/test_invariants.py -q` → all green against the NEW artifacts.
- [ ] Re-run secret/path scans implicitly covered above; eyeball `output.csv` head/tail for plausible explanations.

## Task 4: Update plan_a.md + session handoff

- [ ] Edit `plan_a.md` §5/§6: mark Tasks 1–3 committed (25fa78d/431f53b/45a766b), full suite green, artifacts regenerated, remaining gaps = image_14 unresolved, zero successful live-provider calls (documented fallback), sample-exact optimization (Task 4 of prior plan) still deferred.
- [ ] Append final §5.2 log entry with artifact timestamps.

## Acceptance

All three submission artifacts exist, are mutually consistent with the final code commit set, all gates green, and `plan_a.md` states precisely what the submission does and does not do.
