# Tasks 6 + 7 Report — Edge-case policy document & README rewrite

**Status:** COMPLETE
**Branch:** `sol/p1-inputs-oracle`
**Commit:** `25ad984` — `docs: add edge-case policy and rewrite README with competition context` (exactly 2 files: `README.md`, `docs/edge-case-policy.md`)

## Task 6 — `docs/edge-case-policy.md` (created)

Table with columns **Case | Policy | Rationale | Enforced at (`file:line`)** covering exactly the seven briefed cases: missing income; overlapping commitments; partial installment feasibility; ambiguous spending-adjustment candidates; spending change acts on recurrence; unresolved evidence; unknown FX. Plus a closing note restating controller ruling T2-c (in-window rows dated on/after `request_date` simulated on a copy; pre-`request_date` history handled by suppression only, with the solver re-evaluating under the same semantics).

### Citation verification

- **Method:** (1) the brief's prescribed `Select-String` over `code\oracle.py,code\solver.py,code\changes.py,code\main.py` for `_dedup_events|_installment_safe|_fail_closed_result|suppressed_categories` plus extended `Select-String` passes over `code\recurrence.py`, `code\fx.py`, `code\evidence.py` for every remaining symbol; (2) full-file reads of `oracle.py`, `changes.py`, `main.py`, `evidence.py`, `ranking.py`, `explanations.py`, `solver.py` (all lines relevant to the claims); (3) a scripted final pass extracting every `*.py:<line>` regex from the written document and printing the actual line text, asserting each resolves to non-empty content.
- **Count:** **32 unique `file:line` citations, 0 broken, 0 mismatched** (re-run fresh after the last edit, including the `oracle.py:117 → :118` precision fix).

## Task 7 — `README.md` (rewritten)

Sections: Competition context (24-hour hackathon, "Buy or Wait?" brief, 250 requests, five outcomes, 90-day / `minimum_balance_to_keep` constraint, output contract table) → Scoring axes (six, quoted verbatim from `problem_statement.md`) → Architecture (16 rows: 15 `code/*.py` + `tools/tui.py`) + "Why deterministic" paragraph → Running it (install, both batch commands, TUI, 8-row env-var table, 50-call ceiling, usage-report location, submission bundle) → link to `docs/edge-case-policy.md`.

### Command/claim verification (read from source, pipeline not run)

| Documented | Verified at |
|---|---|
| `python -m code.main` | `code/main.py:6` |
| `python code/main.py` | `code/main.py:12` |
| `python -m code.main --help` (no pipeline run) | `code/main.py:129-131` |
| `python -m tools.tui` | `tools/tui.py:5` |
| TUI session-only, never writes `output.csv`/`dataset/`, never calls provider | `tools/tui.py:10,85,986` + docstring |
| 50-call ceiling | `code/usage.py:7,52`; `code/explanations.py:141` |
| usage report paths | `code/main.py:121-122` |
| 8 env vars (name + purpose only, no values) | `code/explanations.py:41-43,79,92`; `code/usage.py:29-36`; `code/ocr.py:351` |
| 250 requests | `dataset/requests.csv` = 251 lines (header + 250) |
| deps: pandas (batch), rich (TUI), OCR optional/lazy | `code/main.py:27`; `tools/tui.py:25-29`; `code/ocr.py:278,367-377` |
| `log.txt` = chat transcript, gitignored | `.gitignore` |

`log.txt` was not appended to. pytest was not run. No knowledge-graph content. No secrets or env-var values in either file.

## Concerns

1. `code/oracle.py:53` `_is_countable` is **defined but never called** anywhere in `code/` or `tests/` (dead code). The brief names it as enforcement for case 2, so I cite it as the countability *predicate*, and cite the live enforcement (`oracle.py:289` pending-credit skip) alongside it. A reviewer grepping for call sites will find none — worth a follow-up cleanup.
2. `code/ranking.py:rank_options` is likewise not called by the solver (which sorts inline with the identical key); README states this explicitly.
3. No runtime `requirements.txt` exists (only `tests/requirements-test.txt`), so the install section documents `pip install pandas` / `pip install rich` per verified imports rather than a non-existent file.
4. The previous README (uncommitted working-tree content) contained stale module names (`data_loader.py`, `spending_changes.py`, `utils.py`, `constants.py` — none exist) and the claim `BUY_WAIT_LLM_MAX_CALLS=0`; both removed.

**Report file:** `D:\Hackerrank_Orchestrate\hackerrank-orchestrate-september26\.superpowers\sdd\2026-10-08-the-month-end-finalize-and-publish\task-6-7-report.md`
