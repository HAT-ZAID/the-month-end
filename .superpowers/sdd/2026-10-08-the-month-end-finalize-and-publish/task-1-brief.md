## Task 1: Teach the oracle to suppress forecasted spending changes

**Files:**
- Modify: `code/oracle.py`
- Test: `tests/test_oracle_changes.py` (new)

**Interfaces:**
- Consumes: existing `solve_safe_amount(user_id, request_date_str, requested_amount_str, profile, events, fx_rows, recurrence_flags)`.
- Produces: **new optional keyword** `suppressed_categories: set[str] | None = None`. Default `None` preserves every existing caller and test.

**Why category-level:** a stop/reduce must remove *future recurrence*, but the targeted events are historical and outside the forecast window. Per-event suppression cannot work.

> **Rulings carried into this task (from pre-flight):**
> - **T1-a** — `safe_amount = max(0, min(req_amt, min(daily_balances) - min_bal))` (`oracle.py:428`), so it is *clamped by the requested amount*. Any test comparing two results with `requested <= available` will see both sides equal `requested`. Tests must therefore use a `requested_amount` large enough that the clamp does not bind, or assert on `min(ledger)`.
> - **T1-b** — suppressing a category that is never forecast is a **no-op** (it is identical to not suppressing it), and protected categories cannot be user-changed. Therefore suppression alone can never move `safe_amount`. The baseline forecast must be widened to cover flexible recurring categories. This is forced, not a preference.

- [ ] **Step 1: Write the failing test**

Create `tests/test_oracle_changes.py`. Note the requested amount (`9000`) deliberately exceeds available balance minus floor (`10000 - 1000 = 9000`… use `9000` with balance `10000`/floor `1000` so the clamp binds at exactly the boundary only after suppression) — asserted via `min(ledger)` so the clamp cannot mask the effect:

```python
from code.oracle import solve_safe_amount


PROFILE = {
    "home_currency": "USD",
    "current_available_balance": "10000",
    "minimum_balance_to_keep": "1000",
    "expense_categories_to_protect": "rent",
    "expense_categories_user_is_willing_to_stop": "streaming",
    "expense_categories_user_is_willing_to_reduce": "",
    "max_installment_months": "",
}

# Recurrence evidence for a stoppable subscription. Production obtains this from
# code.recurrence.detect_recurrence; tests pin it so they do not depend on that
# detector's internals.
FLAGS = {("u1", "streaming", "subscription")}


def _streaming(month: str) -> dict:
    return {
        "event_id": f"stream_{month}",
        "user_id": "u1",
        "event_type": "subscription",
        "category": "streaming",
        "direction": "debit",
        "amount": "100",
        "currency": "USD",
        "event_date": f"{month}-05",
        "settlement_date": f"{month}-05",
        "status": "settled",
        "linked_event_id": "",
        "flexibility": "stoppable",
        "minimum_allowed_amount": "0",
    }


def _history():
    # Four monthly observations so the median interval is 30 days and at least
    # two forecast occurrences land inside [request_date, request_date+90].
    return [_streaming(m) for m in ("2025-10", "2025-11", "2025-12", "2026-01")]


def test_baseline_forecasts_flexible_recurring_category():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    # Without the fix the flexible subscription is never forecast, so the ledger
    # is flat at the starting balance.
    assert min(base["ledger"]) < 10000


def test_suppressing_flexible_category_raises_safe_amount():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    stopped = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"streaming"},
    )
    assert min(stopped["ledger"]) > min(base["ledger"])
    assert stopped["safe_amount"] >= base["safe_amount"]
    # Suppressing the category removes every forecast occurrence of it.
    assert min(stopped["ledger"]) == 10000


def test_suppression_preserves_ledger_shape_and_day_zero():
    events = _history()
    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    stopped = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"streaming"},
    )
    assert len(stopped["ledger"]) == 91
    assert len(base["ledger"]) == 91
    assert base["ledger"][0] == stopped["ledger"][0]


def test_no_suppression_matches_default_behavior():
    events = _history()
    explicit_none = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS, suppressed_categories=None
    )
    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    assert explicit_none["safe_amount"] == default["safe_amount"]
    assert explicit_none["ledger"] == default["ledger"]


def test_unknown_suppressed_category_is_ignored():
    events = _history()
    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
    ignored = solve_safe_amount(
        "u1", "2026-02-01", "9900", PROFILE, events, [],
        FLAGS, suppressed_categories={"no_such_category", ""},
    )
    assert ignored["ledger"] == default["ledger"]
```

- [ ] **Step 2: Run to verify RED**

```powershell
python -m pytest tests/test_oracle_changes.py -v
```

Expected: FAIL with `TypeError: solve_safe_amount() got an unexpected keyword argument 'suppressed_categories'`.

- [ ] **Step 3: Add the parameter and suppression logic**

Extend the signature in `code/oracle.py`:

```python
def solve_safe_amount(
    user_id: str,
    request_date_str: str,
    requested_amount_str: str,
    profile: dict,
    events: list[dict],
    fx_rows: list[dict],
    recurrence_flags: set[tuple[str, str, str]],
    suppressed_categories: set[str] | None = None,
) -> dict:
```

Normalize after protected categories are parsed:

```python
    suppressed = {
        str(c or "").strip().lower()
        for c in (suppressed_categories or set())
        if str(c or "").strip()
    }
```

**Widen the forecast set (ruling T1-b).** Rename `protected_recurring_cats` to `recurring_expense_cats` and build it from *every* category carrying an expense **or** subscription recurrence flag, not only protected ones:

```python
    recurring_expense_cats = set()
    for e in user_events:
        cat = e.get("category")
        et = str(e.get("event_type", "") or "").strip().lower()
        if et not in ("expense", "subscription"):
            continue
        key = (str(e.get("user_id", "") or ""), cat, str(e.get("event_type", "") or ""))
        if key in recurrence_flags:
            recurring_expense_cats.add(cat)
```

Then let subscriptions count as expense history. `_history_eligible(e, for_expense=True)` currently requires `event_type == "expense"`, which silently drops all 1,699 flexible subscription rows from both frequency and amount averaging. Accept `subscription` alongside `expense` for the expense path, keeping every other rule (`non_cash` rejected, status in `settled/scheduled/pending`, pending requires `debit`) identical.

**Suppression must not widen history eligibility.** Only the amount used for the forecast delta changes:

```python
        cat_events = [e for e in user_events
                      if e.get("category") == cat and _history_eligible(e, for_expense=True)]
        if cat in suppressed:
            # A spending change stops this category going forward. Historical
            # amounts stay in the ledger; only future forecast occurrences vanish.
            avg_amount = Decimal("0")
        else:
            cat_amounts: list[Decimal] = []
            for ce in cat_events:
                d = _amount_abs(ce)
                if d is None:
                    continue
                s_hist = ce.get("settlement_date") or ce.get("event_date")
                if not s_hist or not isinstance(s_hist, str):
                    continue
                fx_hist = resolve_rate(fx_rows, ce.get("currency"), home_ccy, s_hist)
                if fx_hist["rate"] is None:
                    continue
                cat_amounts.append(-d * fx_hist["rate"])
            if not cat_amounts:
                continue
            avg_amount = sum(cat_amounts, Decimal("0")) / Decimal(len(cat_amounts))
```

Leave the next-date rolling and `ESSENTIAL_FORECAST` emission untouched so a suppressed category still yields zero-valued forecast rows and the 91-point ledger shape holds. Income forecasting (`recurring_income_cats`) is **not** part of this change — suppression must never remove income.

- [ ] **Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_oracle_changes.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Verify no regression, and account for intentional behavior change**

```powershell
python -m pytest tests/test_oracle_cashstates.py tests/test_oracle_timing.py tests/test_oracle_fx.py -q
python -m pytest tests/ -q
```

The `suppressed_categories=None` path must still be identical to before **for protected and income categories**. However, widening the forecast set (ruling T1-b) intentionally changes `safe_amount` for any user with unprotected recurring categories — some existing tests encode the old, narrower baseline.

When a test fails here:

- **Fails because a forecast debits now appear where none did before** → update the expected value and record in the report which assertion changed and why the new value is correct (more future debits must lower or hold `min(ledger)`).
- **Fails for any other reason** → treat as a real regression, fix the implementation, do not edit the test.

Never edit a test to make a `None`-path mismatch disappear; `suppressed_categories=None` must remain byte-identical for protected/income forecasting.

---

