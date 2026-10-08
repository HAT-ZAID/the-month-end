# W2 Fix Round 1/5 Report

Status: DONE
Scope: TOUCH ONLY code/solver.py, code/parser.py, code/changes.py; tests/test_solver.py, tests/test_changes.py; code/main.py + tools/verify_samples.py caller-pin lines only. No commits. No subagents.
Method: TDD (test first, RED verified, minimal fix, GREEN verified) + verification-before-completion (fresh pytest evidence below).

## 1. Parser over-broad (code/parser.py)
Change: gate salary detection — (a) exclude texts matching bonus|commission|refund|lottery|investment-gain; (b) require \b(salary|gaji)\b subject word (drops bare pay/payment/payroll); (c) require raise/increase/naik/effective language (rais|increas|naik|effect|appl|berlaku|mulai) OR EMP-0001 template ref; amount regex narrowed from (salary|gaji|pay) to (salary|gaji).
Tests: ADD tests/test_solver.py::test_parser_bonus_with_amount_yields_no_salary_update, ::test_parser_commission_with_amount_yields_no_salary_update.
RED (pre-fix):
- bonus test FAILED — got salary_updates [{'effective_date':'2026-02-01','amount':Decimal('2026'),'currency':'ATE'}] vs expected []
- commission test FAILED — got salary_updates [{'effective_date':'2026-02-01','amount':Decimal('3000'),'currency':'USD'}] vs expected []
GREEN (post-fix): both PASSED.
Positives kept green: test_parser_emp0001_and_emp0002_patterns, test_parser_indonesian_grouping_via_amounts, test_salary_raise_before_req_shifts_safe_up all pass. Manual probe: salary+increased+EMP-0001 => 1; gaji+naik+berlaku+EMP-0001 => 1; bonus/commission/refund/lottery/investment-gain/payment/payroll/salary-without-raise => 0.
Command: python -m pytest tests/test_solver.py::test_parser_bonus_with_amount_yields_no_salary_update tests/test_solver.py::test_parser_commission_with_amount_yields_no_salary_update -v

## 2. _installment_safe fail-open (code/solver.py)
Change: fail-closed — unparsable req_date/floor return False; ledger None/unparsable/len!=91 return False; explicit is_nan() check on every ledger balance (False, not exception-reliant); NaN leg amounts return False.
Tests: ADD tests/test_solver.py::test_installment_safe_fail_closed (empty, None, short-10, bad-date, bad-floor, bad-entries, NaN ledger).
RED (pre-fix): FAILED at first assert — _installment_safe([], ...) returned True, expected False. Also verified pre-fix: None=>True, bad-date=>True, bad-floor=>True, short-10=>True.
GREEN (post-fix): PASSED. Manual probe: safe small legs=>True; 9000 overdraft=>False; empty legs=>True (no payment, no overdraft).
Command: python -m pytest tests/test_solver.py::test_installment_safe_fail_closed -v

## 3. Beyond-90d legs ignored (code/solver.py::_installment_safe)
Change: conservative horizon gate — horizon = req_d + (len(ledger)-1) days (req+90 for 91-point ledger); any leg date > horizon returns False. Function is only called in strict mode, so strict rejects, lenient (deadline-only) unaffected.
Tests: ADD tests/test_solver.py::test_installment_tail_leg_beyond_window_strict_rejected_lenient_accepted (req 2026-01-01, legs 2026-01-05 + 2026-04-15 with req+90=2026-04-01, deadline 2026-06-01, flat 10000 ledger).
RED (pre-fix): FAILED — strict returned installments, expected rejection.
GREEN (post-fix): PASSED (strict != installments, lenient == installments).
Command: python -m pytest tests/test_solver.py::test_installment_tail_leg_beyond_window_strict_rejected_lenient_accepted -v

## 4. Recurrence-vs-injection (code/solver.py::solve_request)
Change: detect recurrence on PRE-injection copy — event amendments applied, then recurrence_flags = detect_recurrence(deepcopy(events_for_oracle)), then salary injection loop; trailing unconditional detect replaced with else-branch (only when messages_df is None). Injection kept for amounts.
Tests: ADD tests/test_solver.py::test_salary_flag_survives_large_raise_pre_injection (3x3000 history + 5x 15000 raise; spy wraps real solve_safe_amount to capture flags; asserts ("u1","salary","income") in flags and raised safe > base safe).
RED (pre-fix): FAILED — flags set() (CV 1.0 post-injection drops flag; pre-injection CV 0.0 keeps it).
GREEN (post-fix): PASSED.
Command: python -m pytest tests/test_solver.py::test_salary_flag_survives_large_raise_pre_injection -v

## 5. Caller pins (code/main.py, tools/verify_samples.py)
Change (only lines touched in each file, each with one-line comment):
- code/main.py solve_request call += installment_safety="strict",  # pin strict leg safety for production runs
- tools/verify_samples.py solve_request call += installment_safety="strict",  # pin strict leg safety for verification runs
No other edits to those two files. Verified by reading both call sites.

## 6. Dead first-solve block (tests/test_solver.py::test_spending_fallback_earliest_is_original)
Change: removed unused res first solve (deadline 2026-03-01), duplicate monkeypatch.setattr + calls reset, and confusing comments. Kept single solve with deadline 2026-01-10 + asserts (affordable_with_plan, stop:event_x, earliest 2026-02-15).
GREEN: test passes in suite runs below.

## Verification (fresh, post-fix)
Command: python -m pytest tests/test_solver.py tests/test_changes.py tests/test_options.py tests/test_ranking.py -q
Output: 51 passed in 0.17s
Command: python -m pytest tests/ -q
Output: 203 passed, 2 skipped in 6.22s (zero failures)
Combined RED run (5 new tests pre-fix): 5 failed (bonus, commission, fail-closed, tail-leg, salary-flag) — each failed for the expected reason, not typos.
Combined GREEN run (5 new tests post-fix): 5 passed.

## Files touched
- code/parser.py (finding 1)
- code/solver.py (findings 2, 3, 4)
- tests/test_solver.py (new tests 1-4 + finding 6 cleanup)
- code/main.py (finding 5 pin only)
- tools/verify_samples.py (finding 5 pin only)
- code/changes.py, tests/test_changes.py: no change needed (allowed but untouched)
- NEVER touched: code/oracle.py, recurrence/fx/amounts/reconcile.py, ranking.py, options.py, other test files. Did not relitigate ranking split, options duration gate, oracle rewrite attribution.

## Concerns
- Parser template ref pinned to EMP-0001 specifically. If other EMP-* refs denote genuine raises, they will need raise-words to pass; generic payroll+EMP-* without raise language is intentionally rejected.
- _installment_safe empty-legs plan returns True (no payment to verify). If an unparsable/empty payment_plan should instead be unsafe, that is a follow-up decision, not in this round's findings.
- Pre-existing repo git state is dirty outside this task's scope (parent checkout shows unrelated M entries); this round made no edits outside the allowed set and no commits.
