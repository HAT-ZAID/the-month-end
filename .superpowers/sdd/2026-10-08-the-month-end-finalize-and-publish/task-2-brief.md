## Task 2: Make `search_changes` evaluate real forecast impact

**Files:**
- Modify: `code/changes.py`
- Test: `tests/test_changes.py`

**Interfaces:**
- Consumes: `solve_safe_amount(...)` with the new `suppressed_categories` keyword.
- Produces: unchanged `search_changes(user_id, request_date, requested_amount, profile, events, fx_rows, recurrence_flags) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_changes.py`:

```python
def test_search_changes_stops_a_subscription_that_only_exists_in_history():
    """Gold samples prove subscriptions are valid change targets even when every
    historical row predates request_date."""
    from decimal import Decimal
    from code.changes import search_changes

    profile = {
        "home_currency": "USD",
        "current_available_balance": "1500",
        "minimum_balance_to_keep": "4000",
        "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_stop": "streaming",
        "expense_categories_user_is_willing_to_reduce": "",
        "max_installment_months": "",
    }
    events = []
    for i, month in enumerate(("2026-01", "2026-02", "2026-03")):
        events.append({
            "event_id": f"s{i}",
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
        })
    result = search_changes(
        "u1", "2026-04-01", Decimal("600"), profile, events, [],
        {("u1", "streaming", "subscription")},
    )
    assert result, "expected a stop action for a stoppable recurring subscription"
    assert result[0].startswith("stop:")
```

- [ ] **Step 2: Run to verify RED**

```powershell
python -m pytest tests/test_changes.py::test_search_changes_stops_a_subscription_that_only_exists_in_history -v
```

Expected: FAIL — `result` is `[]`.

- [ ] **Step 3: Allow subscription event types**

Replace `code/changes.py:92`:

```python
        if event_user != str(user_id) or event_type not in ("expense", "subscription"):
            continue
```

- [ ] **Step 4: Drop the in-window date filter, keep recurrence requirement**

Remove the `req_d <= ed <= win_end` rejection at `code/changes.py:108-115`. A change acts on the category's forward recurrence, not one historical row, so a past-dated flexible event is a valid target. Keep every other guard: user match, category in `stop_ok`/`reduce_ok`, category not protected, recurrence flag present, status in `{"settled","scheduled","pending"}`, direction `debit`, and stop/reduce mutual exclusion.

Keep `get_candidates` emitting `reduce_to` only when `min_amt < amt` — gold confirms `reduce_to` equals `minimum_allowed_amount`.

- [ ] **Step 5: Evaluate combos via category suppression**

Replace the amount-mutation block (`code/changes.py:126-142`):

```python
        for combo in combinations(candidates, size):
            if len({c[0] for c in combo}) != len(combo):
                continue
            # Map each targeted event to its category so the oracle drops that
            # category's forward forecast instead of editing settled history.
            cat_by_eid = {
                str(e.get("event_id", "")): str(e.get("category", "") or "").strip().lower()
                for e in events
            }
            suppressed = {cat_by_eid.get(eid, "") for eid, _action, _val in combo}
            suppressed.discard("")
            flags_to_use = recurrence_flags if recurrence_flags is not None else set()
            res = solve_safe_amount(
                user_id, request_date, str(requested_amount),
                profile, events, fx_rows, flags_to_use,
                suppressed_categories=suppressed,
            )
            if res["safe_amount"] >= requested_amount:
                ...
```

Keep the savings calculation and the "fewest changes, then max savings" preference.

- [ ] **Step 6: Verify GREEN**

```powershell
python -m pytest tests/test_changes.py -q
python -m pytest tests/ -q
```

Expected: zero failures.

- [ ] **Step 7: Verify the mechanism reaches real data (hard stop)**

Three gates, all required. Run the sample verifier as `python -m tools.verify_samples` — the bare script form resolves stdlib `code` and raises `ModuleNotFoundError` (ruling T4-a).

```powershell
python -m tools.verify_samples
```

1. At least one of `request_06`, `request_11`, `request_21` must emit a non-`none` `spending_changes_needed`. If all three are still `none`, the mechanism did not reach the data.
2. `python -m pytest tests/test_changes.py -q` stays green.
3. Every remaining sample mismatch must be attributable to forecast semantics (safe-amount/earliest precision), never to the change mechanism.

If all three gold change cases still emit `none`, **stop and re-diagnose** — do not continue to Task 3. Record the gap in the ledger (ruling T2-a: sample exactness itself is the deferred optimization; the mechanism firing is not).

---

