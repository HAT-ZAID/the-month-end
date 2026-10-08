## Task 7: README with competition context

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Competition section** — the HackerRank Orchestrate 24-hour hackathon, the "Buy or Wait?" brief, and the core question: for each of 250 requests, decide full payment, partial payment, installments, wait, or decline without breaching the minimum balance across a 90-day forecast.

- [ ] **Step 2: Scoring axes** — quote the six: accuracy of `amount_safe_to_pay`; correctness of `affordability_status`; correctness of `recommended_payment_method` and `payment_plan`; accuracy of `earliest_date_for_full_payment`; validity of `spending_changes_needed`; usefulness and consistency of `decision_explanation`.

- [ ] **Step 3: Architecture table** — one line each for `amounts.py`, `fx.py`, `recurrence.py`, `reconcile.py`, `oracle.py`, `changes.py`, `options.py`, `ranking.py`, `parser.py`, `solver.py`, `ocr.py`, `evidence.py`, `explanations.py`, `usage.py`, `main.py`, plus `tools/tui.py`. Add a "why deterministic" paragraph: safety arithmetic stays in code; the model writes prose only, schema-validated and grounded before acceptance.

- [ ] **Step 4: Running it** — install, `python -m code.main`, `python code/main.py`, the TUI launch command, the env-var table, the **50-call ceiling**, where `evaluation/usage_report.md` is written, and the submission bundle (`code.zip`, `output.csv`, `log.txt` as chat transcript).

- [ ] **Step 5: Link `docs/edge-case-policy.md`.** Do not reference a knowledge graph.

---

