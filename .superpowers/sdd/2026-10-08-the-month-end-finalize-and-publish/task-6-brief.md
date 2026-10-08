## Task 6: Edge-case policy document

**Files:**
- Create: `docs/edge-case-policy.md`

- [ ] **Step 1: Write the policy**

A table with columns Case | Policy | Rationale | Enforced at (`file:line`). Cover:

1. **Missing income** — never forecast income from a pending credit; forecast only settled/scheduled salary on its settlement date. `code/oracle.py` income loop, `code/recurrence.py`.
2. **Overlapping commitments** — pending debits reserve first; linked refund pairs preserved, not collapsed; duplicate IDs collapse to one. `code/oracle.py` `_dedup_events`, `_is_countable`.
3. **Partial feasibility for installments** — strict mode fails closed on a leg beyond the 90-day horizon, on NaN ledger values, and on unparsable dates or floors. `code/solver.py` `_installment_safe`.
4. **Ambiguous spending-adjustment candidates** — valid only when the category is non-protected, present in the user's stop/reduce list, and flagged recurring; at most three actions; stop and reduce mutually exclusive per event. `code/changes.py`.
5. **Spending change acts on recurrence** — a stop/reduce suppresses the category's *future* forecast; settled history is never rewritten and savings are never claimed for an already-ended subscription. `code/oracle.py` `suppressed_categories`, `code/changes.py`.
6. **Unresolved evidence** — a blank amount OCR cannot resolve yields `not_affordable` with `safe_amount = 0`. `code/main.py` `_fail_closed_result`.
7. **Unknown FX** — fail closed to `safe_amount = 0`, `earliest = None`, preserving a 91-point ledger. `code/oracle.py`.

- [ ] **Step 2: Verify every citation**

```powershell
Select-String -Path code\oracle.py,code\solver.py,code\changes.py,code\main.py -Pattern "_dedup_events|_installment_safe|_fail_closed_result|suppressed_categories"
```

Confirm each line exists and matches the claim.

---

