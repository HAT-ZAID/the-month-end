## Task 3: Recompute the plan from the adjusted cashflow

**Files:**
- Modify: `code/solver.py`
- Test: `tests/test_solver.py`

**Interfaces:**
- Consumes: `search_changes(...)` returning concrete actions.
- Produces: unchanged `solve_request(...)` return shape.

**Why:** gold shows the intended outcome is `affordable_with_plan` + `full_payment`. After a change is chosen, `amount_safe_to_pay`, `affordability_status`, `earliest_date_for_full_payment`, and `payment_plan` must all come from the recomputed result.

- [ ] **Step 1: Read the current change branch**

Read `code/solver.py` lines 405-470. Confirm the recompute at ~line 429 and note exactly which fields are taken from `res2` versus the original `res_now`.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_solver.py`:

```python
def test_spending_change_unlocks_full_payment_status():
    """Gold samples show a spending change yields affordable_with_plan + full_payment."""
```

Build a user whose balance covers the request only after a stoppable recurring subscription is stopped. Assert `spending_changes_needed` is non-`none`, `affordability_status == "affordable_with_plan"`, `recommended_payment_method == "full_payment"`, and `amount_safe_to_pay` is strictly greater than the pre-change safe amount.

- [ ] **Step 3: Run to verify RED**

```powershell
python -m pytest tests/test_solver.py::test_spending_change_unlocks_full_payment_status -v
```

Expected: FAIL.

- [ ] **Step 4: Recompute from the suppressed ledger**

Switch from rewriting event amounts to passing the suppressed-category set, and take **all** downstream decision fields from that recomputed result — `safe_amount`, `earliest_date`, status, method, plan. Preserve the existing rule that `earliest_date_for_full_payment` reflects the pre-change safe position when no full payment is safe without the change.

- [ ] **Step 5: Verify GREEN and full suite**

```powershell
python -m pytest tests/test_solver.py -q
python -m pytest tests/ -q
```

Expected: zero failures.

- [ ] **Step 6: Confirm the fix reaches the 250-row dataset**

`output.csv` is only regenerated in Task 8, so reading it here would be stale and would trip the hard stop falsely (ruling from pre-flight). Regenerate first with provider spend disabled:

```powershell
$env:BUY_WAIT_LLM_MAX_CALLS='0'
python -c "import code.main; code.main.main()"
python -c "import pandas as pd; d=pd.read_csv('output.csv',dtype=str); print('changes emitted:',(d['spending_changes_needed']!='none').sum()); print(d['affordability_status'].value_counts().to_dict())"
```

Expected: `changes emitted` is **nonzero**. If zero, stop and re-diagnose before continuing. The status distribution shifts are expected and are not themselves a failure — record them in the ledger for Task 8's reviewer.

---

