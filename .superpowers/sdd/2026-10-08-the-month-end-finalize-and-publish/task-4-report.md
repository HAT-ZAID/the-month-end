# Task 4 report: Interactive TUI

**Status:** COMPLETE — committed `170fe20` on `sol/p1-inputs-oracle` (staged files only: `tools/tui.py`, `tests/test_tui.py`).

## What was built

`tools/tui.py` (1025 lines), entry point `python -m tools.tui`:

- **Pure helpers (tested):** `render_decision` (8 output fields as one rich panel captured to StringIO, money formatted, `spending_changes_needed` highlighted), `build_ledger_table` (91-day forecast, floor row, breach marking), `lookup_request` (exact-id), `apply_overrides` (returns new dict, never mutates), `diff_fields` ({field: (before, after)} over `DECISION_FIELDS`, NaN normalised), `Session` (in-memory overlay per request_id).
- **Spending-change helpers (tested):** `parse_action` (`stop:<id>` / `reduce_to:<id>:<n>`), `edit_events` (copy-edit amounts), `suppressed_categories`, `project_actions` (through `code.oracle.solve_safe_amount` with `suppressed_categories=` — no TUI-side decision logic).
- **REPL:** commands `list / show / whatif / changes / apply / diff / undo / explain / help / quit`; `run_tui(...) -> None`.
- **Plumbing:** `load_dataset` mirrors `code/main.py` (repo-root anchored), lazy — loaded on first data command, so `help`/`quit` start instantly. `_solve` re-solves every command through `code.solver.solve_request` with the same fail-closed gate as `code.main` (`_validated_amount` + evidence warnings → `_fail_closed_result`). Decisions cached per request keyed by that request's own overlay signature (`_cache`, `_originals` for `diff`); solver exceptions print a message and return to the prompt.

## Safety rules verified

- **Nothing written:** no `to_csv`/`open(write)`/`makedirs` anywhere in `tui.py` (grep verified); `quit` discards the Session; `git status -- dataset output.csv` clean after all runs.
- **No provider call:** `explain` prints the `decision_explanation` the solver already computed; no import of the explanations module.
- **Session-only edits + undo:** overrides/applications live in `Session.overrides`; `undo` restores the original decision because the original inputs are re-solved (fresh `Session()` in `cmd_diff` → `_originals` cache), not replayed.

## Tests — `python -m pytest tests/test_tui.py -q`

- RED first: `ImportError: cannot import name 'tui' from 'tools'`.
- Final: **6 passed in 0.09s** (run after every edit batch; last run post-final-edit).
- Brief's tests verbatim except fixture event id (see deviations).

## Smoke runs (all exit 0, no tracebacks, piped stdout)

- Required: `@('help','quit') | python -m tools.tui` — banner, help (fits 80 cols), `bye -- session discarded`, exit 0.
- `show request_27` — decision panel, 91-day ledger, floor row, breach count, options table.
- `whatif request_27 amount=999999` → `diff` → `undo` (original panel restored); `floor=500000` correctly flips to `not_affordable`; `amount=abc` rejected.
- `apply request_27 stop:<runtime-discovered id>` + `diff` + `undo` (event id from `changes` output only, never hard-coded).
- `changes request_27` — baseline via oracle, `search_changes` safe set, per-candidate projections.
- `list` (250 rows, cached decisions) and `list affordable_now` (solves all 250 with progress, ~45s cold, filtered correctly).

## Deviations from the brief

1. **Test fixture event id:** brief's test used `event_476` (a gold-sample id); substituted `event_55`. All four gold ids (`event_476`, `event_989`, `event_1815`, `event_1816`) grep-verified absent from both committed files.
2. **`list` without a status argument does not solve** (shows cached decisions + a hint). Solving all 250 requests takes ~45s cold; filtering requires `list <status>`, which solves first — matches the brief's intent (status filter needs statuses) while keeping bare `list` instant.
3. **README update (brief step 6)** not done — outside this task's job list, not staged.
4. Rich `Panel`/`Table` used directly (textual not installed; `rich` 14.3.4 present).

## Concerns

- `output.csv` is untracked with `LastWriteTime 05:46` today — from an earlier pipeline run, not this task (TUI has zero write calls; `dataset/` git status clean).
- `tools/__init__.py` is untracked; `tools` imports as a namespace package on Python 3.11 so `python -m tools.tui` works regardless (smoke-verified).
- `solve_all` cost: first `list <status>` re-solves all 250 requests each fresh session (~45s); per-request edits are cached by overlay signature afterwards.
- `log.txt` not appended (explicit dispatcher instruction overrides AGENTS.md §5).
