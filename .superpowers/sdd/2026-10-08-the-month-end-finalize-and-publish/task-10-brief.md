## Task 10: Four independent review agents (gate before publish)

**Why:** the judge critiqued the last submission. Four sharp, independent reviewers catch what a single pass misses. This is the **gate** — nothing publishes until they report.

**Files:**
- Create: `.superpowers/sdd/final-review/review-1-oracle.md`, `review-2-solver.md`, `review-3-artifacts.md`, `review-4-spec-compliance.md`, `.superpowers/sdd/final-review/verdict.md`

**Method:** dispatch four `task` subagents in parallel. Each is **read-only** — no writes to `code/` or `tests/`. Each returns findings with `file:line`, a severity (Critical / Important / Minor), and the exact command output proving it.

- [ ] **Step 1: Dispatch the four reviewers in parallel**

- **Reviewer 1 — Oracle & safety.** Scope `code/oracle.py`, `code/fx.py`, `code/recurrence.py`, `code/reconcile.py`, `code/amounts.py`. Verify `suppressed_categories` cannot weaken the minimum-balance floor, cannot be abused to suppress a protected or income category, preserves the 91-point ledger, and leaves `None` behavior byte-identical. Attack it adversarially: can a suppressed category ever *increase* safe amount beyond what stopping the recurrence justifies?
- **Reviewer 2 — Solver & changes.** Scope `code/solver.py`, `code/changes.py`, `code/options.py`, `code/ranking.py`, `code/parser.py`. Verify the `expense`→`expense|subscription` widening cannot target a protected category, income, or a one-time event; that savings are never claimed for an already-ended subscription; that recomputed `earliest_date_for_full_payment` obeys the pre-change rule; that `partial_payment` two-leg math still sums exactly.
- **Reviewer 3 — Artifacts & honesty.** Scope `output.csv`, `code.zip`, `evaluation/usage_report.md`, `tools/`. Run `tools/correctness_report.py` and `tools/verify_samples.py`. Verify every count in every report matches reality, no stale numbers, no credentials, `code.zip` has single-level paths and includes `evaluation/`, and the archive matches the current tree.
- **Reviewer 4 — Spec compliance.** Read `problem_statement.md` and `AGENTS.md` against the implementation. Verify the output contract column-by-column, all enum values, `spending_changes_needed` format and the ≤3 cap, `decision_explanation` grounding, the 50-call ceiling, and that no organizer-only file or hardcoded label (including the gold sample event IDs) is used.

- [ ] **Step 2: Require a specific finding on the gold-reproduction risk**

Instruct every reviewer to answer: *does the new spending-change path risk special-casing the public sample IDs (`event_476`, `event_989`, `event_1815`, `event_1816`)?* Any literal occurrence outside `tests/` and `docs/` is a **Critical** finding.

- [ ] **Step 3: Triage findings**

For each Critical finding: write a RED test that reproduces it, apply the minimal fix, verify GREEN and the full suite. Important findings are fixed only if cheap and low-risk; record the rest as accepted debt in `verdict.md` with rationale. Minor findings are logged, not fixed.

- [ ] **Step 4: Write the verdict**

`.superpowers/sdd/final-review/verdict.md` records each reviewer's Critical/Important/Minor counts, the disposition of every finding, the final suite result, and `python tools/correctness_report.py` output.

- [ ] **Step 5: Publish gate**

Proceed to Task 11 only if zero open Criticals. If any Critical remains, stop, fix it, and re-run the responsible reviewer.

---

