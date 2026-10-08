# Task 8 report — Release build, correctness report, artifact regeneration

**Status:** COMPLETE with one correctness finding (14/15 checks PASS).
**Branch:** `sol/p1-inputs-oracle` · **Commit:** `41908f4`
**Rules honored:** no `log.txt` append (per instruction, overriding AGENTS §5 logging);
no pytest run; provider ceiling treated as absolute; key never printed, logged, or written.

---

## Step 1 — Regenerated artifacts (bounded provider batch)

Command (single run, from repo root): `OPENROUTER_KEY` parsed from `D:\Hackerrank_Orchestrate\.env`
into the process environment only, plus `BUY_WAIT_LLM_MAX_CALLS=20`,
`BUY_WAIT_LLM_TIMEOUT_SECONDS=15`, then `python -c "import code.main; code.main.main()"`
(executed via a temp runner in `%TEMP%\opencode\run_gen.py` to keep quoting and the key out of
the shell). Output: `Wrote 250 rows to output.csv`.

- **`spending_changes_needed != 'none'`: 4 rows — NONZERO ✓** (`request_78`, `request_99`,
  `request_117`, `request_135`) — identical to the zero-budget gate run, so no regression.
- **`affordability_status` distribution:** `affordable_now` 66, `affordable_with_plan` 68,
  `affordable_later` 40, `not_affordable` 76 (total 250).
- `recommended_payment_method`: `full_payment` 70, `installments` 56, `partial_payment` 8,
  `wait` 40, `not_recommended` 76.
- **`evaluation/usage_report.md` (== `tools/evaluation/usage_report.md`, byte-identical):**

  | Field | Value |
  |---|---|
  | Provider | openrouter |
  | Model | Ling 3.0 Flash Fin (`inclusionai/ling-3.0-flash-fin`) |
  | Model calls | **20** (budget cap respected; hard ceiling 50) |
  | Successful / Failed / Timeouts | 0 / 0 / 0 |
  | Rejected responses / Fallback explanations | 250 / 250 |
  | Input tokens | 9581 |
  | Output tokens | 14062 |
  | Total tokens / average per request | 23643 / 94.572 |
  | Cost status | unavailable (pricing table intentionally empty → never reported as a paid zero) |

- `output.csv` content is **identical row-for-row to the pre-run artifact** (0 rows differ):
  the LLM only writes explanations, and all 20 responses fell through to the deterministic
  fallback (see Concerns), so decisions and prose did not move.
- `evaluation/usage_report.md` and `tools/evaluation/usage_report.md` rewritten and identical.

## Step 2 — `tools/build_release.py`

Builds `code.zip` deterministically: includes `README.md`, `problem_statement.md`, and the
`code/` + `evaluation/` trees; entries written via `path.relative_to(REPO_ROOT).as_posix()`
(single-level paths); sorted by arcname; fixed timestamp `1980-01-01`, `create_system=3`,
fixed mode `0644`, deflate level 9. Excludes `__pycache__`, `.pytest_cache`, `.venv`, `.git`,
`.superpowers`, `*.pyc/*.pyo`, and hard-fails on secret-like names (`log.txt`, `.env`, `*.env`,
`credentials.json`, `id_rsa`, `id_ed25519`, `.netrc`). After writing it re-opens the archive and
asserts: no banned name/dir, no duplicate or non-relative paths, `testzip()` clean, and required
entries `README.md`, `problem_statement.md`, `evaluation/usage_report.md` present.

Run output:

```
built code.zip: 20 files, 48201 bytes
sha256 3953079bb6dd15371e4c701eb4e2754d487aade9984000fb0a36906ba93eb371
top-level entries: ['README.md', 'code', 'evaluation', 'problem_statement.md']
banned names inside archive: none
PASS archive clean: no banned names, required entries present
```

Determinism verified: a second build produced the **identical sha256**
(`3953079bb6dd15371e4c701eb4e2754d487aade9984000fb0a36906ba93eb371`).
Members: `README.md`, `problem_statement.md`, `evaluation/usage_report.md`, `code/README.md`,
and 16 `code/*.py` files.

## Step 3 — `tools/correctness_report.py` output (exit 1 — one FAIL)

```
== Buy or Wait? correctness report ==
PASS row_count: 250 data rows (expected 250; dataset/requests.csv has 250)
PASS column_order: columns=['request_id', 'amount_safe_to_pay', 'affordability_status', 'recommended_payment_method', 'payment_plan', 'earliest_date_for_full_payment', 'spending_changes_needed', 'decision_explanation']
PASS request_id_set: 250 unique IDs; missing=[] extra=[] dupes=[]
PASS amount_safe_to_pay_bounds: all rows within [0, requested_amount]
PASS affordability_status_vocabulary: allowed=['affordable_later', 'affordable_now', 'affordable_with_plan', 'not_affordable']
PASS recommended_payment_method_vocabulary: allowed=['full_payment', 'installments', 'not_recommended', 'partial_payment', 'wait']
PASS payment_plan_grammar: all plans are 'none' or chronological YYYY-MM-DD:amount legs
PASS earliest_date_for_full_payment: ISO-dated or empty; affordable_now implies request_date
PASS partial_payment_legs: two legs, leg 1 on request_date == amount_safe_to_pay, sum == requested_amount
PASS installment_plan_matches_option: every installment plan equals a supplied option schedule
PASS spending_change_grammar: 'none' or <=3 stop:/reduce_to: actions with unique event IDs
PASS spending_change_targets: all targets are non-protected flexible debits in the user's stop/reduce lists
INFO spending_changes_needed != 'none': 4 rows (request_78, request_99, request_117, request_135)
PASS decision_explanation: non-empty and < 400 chars; length 107-144
FAIL no_sample_event_ids: leaked: ['code/amounts.py:event_1815']
PASS no_secrets: submission surface clean (26 files scanned)
INFO status counts: {'affordable_later': 40, 'affordable_now': 66, 'affordable_with_plan': 68, 'not_affordable': 76}
INFO method counts: {'full_payment': 70, 'installments': 56, 'not_recommended': 76, 'partial_payment': 8, 'wait': 40}
RESULT: FAIL (1/15 checks failed: no_sample_event_ids)
```

Scope: shape/columns/IDs, amount bounds, both vocabularies, plan grammar + chronology,
`affordable_now ⇒ earliest == request_date`, partial-payment leg arithmetic, installment plans
recomputed from `dataset/request_payment_options.csv`, spending-change grammar + full target
validity (ownership, debit, settled/scheduled/pending, expense/subscription, flexible,
non-protected, in the user's stop/reduce lists, reduce amount < original), explanation limits,
sample-ID gate, secret gate. Exits nonzero on any FAIL.

**The single FAIL is real and pre-existing:** the module docstring of `code/amounts.py:5`
quotes the sample ID (`...after stripping ID citations, so 'event_1815' can never surface 1815
as a monetary figure`). It is present at `HEAD` too (verified `git show HEAD:code/amounts.py`),
and `tests/test_submission.py` only exempts that file from its hardcoded-label gate via the
`_REF = re.compile` guard — this harness' literal sample-ID gate deliberately has no exemption.
The check itself is correct; the remediation (reword the docstring to drop the literal sample
ID) is a one-line edit to `code/amounts.py`, outside this task's stage list, so it was **not**
touched. Left for the controller to dispatch.

## Step 4 — Key scan

- **Scope A (literal containment, brief Step 6):** `output.csv`, `evaluation/usage_report.md`,
  `tools/evaluation/usage_report.md`, `code/explanations.py`, `code/usage.py`,
  `tools/build_release.py`, `tools/correctness_report.py`, `tools/tui.py`, `README.md`,
  `docs/edge-case-policy.md` → **all `False`** (key read only in-memory, never printed).
- **Scope B (pattern scan per `tests/test_submission.py`):** every text member of `code.zip`
  (20) + `README.md` + `docs/` tree (4 files) = **25 files**; patterns `sk-…`, `AKIA…`, `ghp_…`,
  `xox…`, `AIza…`, private-key header, `aws_secret`, `Bearer …`, plus the
  `os.getenv(..., "sk-…")` fallback trap and banned filenames → **hits: none**.
  (The correctness harness' `no_secrets` check covers the same surface: 26 files clean.)

## Step 5 — Commit

`41908f4 build: add release archiver and correctness harness; regenerate artifacts`
— staged exactly: `tools/build_release.py`, `tools/correctness_report.py`, `code.zip`,
`output.csv`, `evaluation/usage_report.md`, `tools/evaluation/usage_report.md`.
Not staged: `.env`, `.superpowers/`, `log.txt` (and `log.txt` was never appended to).

## Concerns

1. **`no_sample_event_ids` FAIL** — one literal in `code/amounts.py:5` (above); needs a
   one-line docstring reword + zip rebuild before the report can read PASS/PASS.
2. **0 of 20 provider responses were accepted** by the grounding/schema gate
   (`Successful calls: 0`, `Rejected: 250` incl. the 20 attempted; HTTP itself succeeded —
   tokens 9581/14062 were recorded, 0 failures/0 timeouts). All 250 explanations are therefore
   the deterministic fallback prose; decisions unaffected (output identical to pre-run).
   Diagnosing which grounding clause rejects the model would require calls beyond the
   20-call budget → left for a follow-up.
3. **Task 9 interaction:** this task's stage list commits `code.zip`, but Task 9 Step 4 expects
   `git ls-files | Select-String code\.zip` to be empty after gitignoring it — that task will
   need `git rm --cached code.zip` (not in its brief).
4. **CRLF latent:** repo-local `core.autocrlf=true`; a fresh checkout would give `output.csv`
   CRLF, which `tests/test_submission.py::test_output_csv_encoding_clean` rejects. Consider
   `.gitattributes` with `output.csv text eol=lf` at release time.
5. Pre-existing unstaged changes (`code/amounts.py`, `code/ranking.py`, `code/reconcile.py`,
   7 test files, deleted `code/evaluation/*`) were left untouched; `code.zip` was built from
   the current working tree, so the archive matches disk (Task 10 reviewer 3's check) but not
   yet `HEAD` for those files.
