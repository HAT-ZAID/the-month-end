## Task 4: Interactive TUI

**Why:** makes the tool usable instead of a CSV-only artifact. The user drives it: pick a request, change the inputs, re-solve, compare, and try candidate spending changes to see what each one buys. Every interaction is computed fresh by the existing solver — the TUI never holds its own decision logic, and nothing it does can alter the submitted `output.csv`.

**Files:**
- Create: `tools/tui.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `solve_request(...)`, `get_valid_options(...)` from `code/solver.py`; `search_changes(...)` from `code/changes.py`; the `load` helpers in `code/main.py`.
- Produces: `run_tui(...) -> None` plus three pure helpers the tests target: `apply_overrides`, `diff_fields`, `Session.undo`.

**Interaction model:** a REPL over the loaded dataset. The user's edits live in an in-session overlay — `Session.overrides[request_id] = {...}` — and every command re-solves through `solve_request` with the overlaid row. The original dataset and `output.csv` are never touched.

Commands:

| Command | Effect |
|---|---|
| `list [status]` | table of requests, optional filter by `affordability_status` |
| `show <id>` | decision panel + 91-day ledger table + floor breach markers |
| `whatif <id> amount=<N>` / `date=<D>` / `floor=<N>` | set an override, re-solve, print before/after diff |
| `changes <id>` | candidate `stop:`/`reduce_to:` actions with each one's projected effect |
| `apply <id> <action>` | try one candidate in-session, re-solve, show the delta |
| `diff <id>` | original decision vs current overlaid decision |
| `undo [<id>]` | drop overrides for one request, or all |
| `explain <id>` | print the existing `decision_explanation` |
| `help`, `quit` | — |

**Dependency note:** `rich` 14.3.4 is already installed. `textual` is **not** installed — do not add it. Use `rich` only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui.py` targeting the pure helpers, never a live terminal (so CI never blocks on stdin):

```python
from tools import tui


ROW = {
    "request_id": "request_06",
    "amount_safe_to_pay": "603.3",
    "affordability_status": "affordable_with_plan",
    "recommended_payment_method": "full_payment",
    "payment_plan": "2026-01-15:603.3",
    "earliest_date_for_full_payment": "2026-01-15",
    "spending_changes_needed": "stop:event_476",
    "decision_explanation": "Stop streaming to protect the minimum balance.",
}


def test_render_decision_row_formats_money_and_status():
    text = tui.render_decision(ROW)
    assert "request_06" in text
    assert "affordable_with_plan" in text
    assert "stop:event_476" in text


def test_build_ledger_table_has_rows():
    ledger = [{"date": f"2026-01-{d:02d}", "balance": "100"} for d in range(1, 32)]
    table = tui.build_ledger_table(ledger, minimum_balance="50")
    assert table is not None
    assert table.row_count >= len(ledger)


def test_lookup_request_returns_none_for_unknown_id():
    assert tui.lookup_request([ROW], "request_99") is None
    assert tui.lookup_request([ROW], "request_06")["request_id"] == "request_06"


def test_apply_overrides_does_not_mutate_original():
    out = tui.apply_overrides(ROW, {"requested_amount": "900"})
    assert out["requested_amount"] == "900"
    assert "requested_amount" not in ROW


def test_diff_fields_reports_changed_fields_only():
    current = tui.apply_overrides(
        dict(ROW, affordability_status="not_affordable"), {"requested_amount": "900"}
    )
    changed = dict(tui.diff_fields(ROW, current))
    assert set(changed) == {"affordability_status"}
    assert changed["affordability_status"] == ("affordable_with_plan", "not_affordable")


def test_session_undo_clears_overrides():
    s = tui.Session()
    s.set("request_06", {"requested_amount": "900"})
    assert s.effective(ROW)["requested_amount"] == "900"
    s.undo("request_06")
    assert s.effective(ROW) is ROW
    s.set("request_06", {"requested_amount": "900"})
    s.undo()
    assert s.effective(ROW) is ROW
```

- [ ] **Step 2: Run to verify RED**

```powershell
python -m pytest tests/test_tui.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tools.tui'`.

- [ ] **Step 3: Implement the interactive TUI**

Create `tools/tui.py` with:

- `render_decision(row: dict) -> str` — one `rich.panel` holding the 8 output fields, styling `spending_changes_needed` when it is not `none`.
- `build_ledger_table(ledger, minimum_balance) -> rich.table.Table` — the 91-day forecast, the floor row drawn, any day below the floor marked as a breach.
- `lookup_request(rows, request_id) -> dict | None` — exact-id lookup, no fuzzy matching.
- `apply_overrides(row, overrides) -> dict` — returns a **new** dict; never mutates the argument.
- `diff_fields(original, current) -> dict[str, tuple[str, str]]` — only the decision fields that changed, `(before, after)`.
- `Session` — holds `overrides: dict[str, dict]`; `set(request_id, kv)`, `effective(row) -> row` (returns the original object when no override applies), `undo(request_id=None)` clears one or all.
- `run_tui(...)` — the REPL implementing the command table above. Each interactive command builds the effective row, calls `solve_request`, and renders the result. `apply`/`changes` call `search_changes`.

**Rules inside `run_tui`:**
- every override is an in-memory copy; `quit` writes nothing to disk
- never write `output.csv` and never modify anything under `dataset/`
- never call the provider — `explain` prints the explanation already present in the computed decision (the 50-call ceiling belongs to the batch run)
- wrap every solver call so an exception prints a message and returns to the prompt instead of crashing
- `undo` must fully restore the original decision for that request

- [ ] **Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_tui.py -v
python -m pytest tests/ -q
```

Expected: all pass, zero failures.

- [ ] **Step 5: Smoke the TUI non-interactively**

```powershell
python -c "import pandas as pd; from tools import tui; d=pd.read_csv('output.csv',dtype=str).to_dict('records'); print(tui.render_decision(d[0]))"
"help`nquit" | python -m tools.tui
```

Expected: a rendered panel, then the REPL starts, prints its help, and exits — no traceback.

- [ ] **Step 6: Document it in the README**

One short section: how to launch it, the command table, and an explicit note that edits are session-only — the TUI never writes `output.csv` or `dataset/`, so a play session cannot change the submission.

---

