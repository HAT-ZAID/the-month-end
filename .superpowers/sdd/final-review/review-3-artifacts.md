# Reviewer 3 of 4 — Artifact Verification Report

**Scope:** `output.csv`, `code.zip`, `evaluation/usage_report.md`, `tools/evaluation/usage_report.md`, `README.md`, `docs/edge-case-policy.md`, `tools/`
**Mode:** read-only. Only this report was written. Scratch extraction lived under `$env:TEMP\review3_zip_extract`.
**Date:** 2026-10-08, session time ~08:31–08:45 (all mtimes below are local).

## Verdict: APPROVE

No Critical findings. Gold-sample IDs do not appear in `output.csv` or `code/`; no credentials anywhere in the scanned surface; `output.csv` contract is exact; `code.zip` is clean and byte-identical to the working tree; both usage reports are byte-identical, internally consistent, and exactly reproducible from current code. Findings below are documentation-accuracy and packaging issues to fix before/soon after publish; none affects graded output or security.

---

## Findings table

| # | Severity | Location | Problem | Exact proof (command + raw output) | Suggested fix |
|---|---|---|---|---|---|
| F1 | **Important** | `docs/edge-case-policy.md` rows "Partial feasibility for installments" and "Unresolved evidence" | 2 of 35 `file:line` citations are stale: `code/main.py:90` (claimed: "strict mode pinned for production") and `code/main.py:83` (claimed: "any warning or invalid amount trips fail-closed"). Both lines now sit inside the `_money_plan()` helper. Actual enforcement is `code/main.py:137` (`installment_safety="strict", # pin strict leg safety for production runs`) and `code/main.py:130` (`if user_warnings or not all_amounts_validated:`). Root cause proven by mtimes: `docs/edge-case-policy.md` written 10/8 06:43:48, `code/main.py` modified 10/8 07:55:24 (the `_money`/`_money_plan` block at lines 54–98 shifted cited lines by exactly 47: 130−47=83, 137−47=90). README.md:163-164 advertises these as "verified `file:line` enforcement citations". The two stale lines ship in the submitted tree. | `python <scratch check_cites.py>` printing `src[line-1]` for all 35 citations → raw: `90: f"\|{legs[1].split(':', 1)[0]}:{_money(requested - safe)}"` and `83: if partial and len(legs) == 2 and ":" in legs[0] and ":" in legs[1]:`; vs `Get-Item code\main.py` → `LastWriteTime: 10/8/2026 7:55:24 AM` while doc is `10/8/2026 6:43:48 AM`; and grep of main.py → `130: if user_warnings or not all_amounts_validated:` / `137: installment_safety="strict",  # pin strict leg safety for production runs` | Update the two citations to `code/main.py:137` and `code/main.py:130`, re-scan all 35 against the post-7:55 tree. (Doc's own preamble already says code wins and the doc should be fixed.) |
| F2 | Minor | `README.md:53` (and therefore `code.zip` → `README.md`) | Literal public-sample gold ID `event_1815` appears in README prose: "(keeps digits in IDs such as `event_1815` out of money figures)". Not in `code/`, not in `output.csv`, and `code/amounts.py` itself contains no such literal (its docstring now says "after stripping ID citations"), so per the gate rule this is **not** Critical and carries no special-casing behavior — but it is a gold ID inside a submitted artifact. | `Select-String output.csv, code\*.{py,md} -Pattern 'event_476\|event_989\|event_1815\|event_1816'` → raw: (no matches in either). Same grep over submission surface → `README.md:53: ... (keeps digits in IDs such as `event_1815` out of money figures)...` | Replace with a generic example, e.g. "IDs such as `event_1234`" or drop the literal, to keep zero gold-ID literals across the whole submitted surface. |
| F3 | Minor | `evaluation/usage_report.md` + `tools/evaluation/usage_report.md` line 9 (`Rejected responses: 242`) | Label overstates: of 242, only **17** were responses actually rejected (schema/grounding, `code/explanations.py:257`); the other **225** were requests turned away by the call-budget cap with **no response ever received** (`code/explanations.py:164-167`). Arithmetic that proves it: calls=25 ⇒ configured max=25 (calls increments only after the budget check); 25 attempts = 8 successful + 17 rejected; 250−25 = 225 budget-rejected; 17+225 = 242 = `Rejected responses` and = `Fallback explanations`, and 8+242 = 250. The number matches the code counter — it is a mislabeled count, not a fabricated one. | Code read: `explanations.py:164-167` (`if calls >= configured_max: rejected += 1; fallbacks += 1; return _fallback`) and `:256-259` (`rejected += 1; fallbacks += 1` after parse/grounding failure); report shows `Model calls: 25`, `Successful calls: 8`, `Rejected responses: 242`. | Split into "Rejected responses: 17 / Rejected by call budget: 225" in `write_usage_report`, or rename to "Requests without accepted LLM explanation: 242". |
| F4 | Minor | `code.zip` packaging vs its own `README.md` | The README inside the archive references two things not in the archive: `./docs/edge-case-policy.md` (link at README:164) and the TUI command `python -m tools.tui` / `tools/tui.py` (README:68,110) — `docs/` and `tools/` are not zip entries. A zip-only consumer gets a dead link and `ModuleNotFoundError` for the documented TUI command. (`dataset/` is also absent, which is expected — the platform supplies it.) | Zip entry list (raw, below §6) contains only: `README.md`, `problem_statement.md`, `code/*` (17 files), `evaluation/usage_report.md` — no `docs/`, no `tools/`. | Either add `docs/edge-case-policy.md` (+ `tools/`) to the zip, or annotate those README sections with "(repository checkout only)". |
| F5 | Minor | `tools/verify_samples.py` invocation | Plain `python tools/verify_samples.py` (the invocation named in this gate) crashes because repo root is not on `sys.path` and stdlib module `code` shadows the local package. Works with `PYTHONPATH=.` or `python -m tools.verify_samples`. README does not document this tool, so no README claim is violated; reported because the raw run differs from the expected report. | Raw: `Traceback ... from code.solver import solve_request / ModuleNotFoundError: No module named 'code.solver'; 'code' is not a package` (exit 1). With `$env:PYTHONPATH="$PWD"` it runs (output in §A.2). | Add `sys.path.insert(0, repo_root)` bootstrap (as `code/main.py:13-14` already does) or document the `-m` form. |
| F6 | Minor (context) | `log.txt` vs final artifacts | The published artifacts were produced **after** the last log entry: `log.txt` LastWriteTime 10/7 23:06:04; `output.csv` + both usage reports 10/8 08:06:28; `code.zip` 10/8 08:07:02. The last logged LLM batch says `BUY_WAIT_LLM_MAX_CALLS=5` (log line 2233), while the published report is only self-consistent with a run at `BUY_WAIT_LLM_MAX_CALLS=25`. So the final full-dataset run is not recorded in the development log, and the usage numbers have no independent record beyond internal consistency (see §4). Not in my file scope to fix; flagged because it is the provenance gap behind item 4. | `Get-Item log.txt, output.csv, code.zip, evaluation\usage_report.md` → mtimes above; `Select-String log.txt -Pattern MAX_CALLS` → lines 2159 (`=0`), 2233 (`=5`), none (`=25`). | Append the missing final-run log entry (AGENTS.md §5.2) noting the exact env of the 08:06 run. |

**Context, not counted as a finding (outside scope files):** the in-repo recorded sample-mismatch deviation is **71** (`tests/open_issues.md:8`, `sessions_context.md:21`, "started 78"); my run observed **75**. Accepted deviation either way per gate instructions; the recorded count is stale by 4.

---

## A. Raw script outputs

### A.1 `python tools/correctness_report.py` — PASS (15/15)

```text
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
PASS decision_explanation: non-empty and < 400 chars; length 102-306
PASS no_sample_event_ids: no sample event IDs in output.csv or code/ (18 files scanned)
PASS no_secrets: submission surface clean (26 files scanned)
INFO status counts: {'affordable_later': 40, 'affordable_now': 66, 'affordable_with_plan': 68, 'not_affordable': 76}
INFO method counts: {'full_payment': 70, 'installments': 56, 'not_recommended': 76, 'partial_payment': 8, 'wait': 40}
RESULT: PASS (15/15 checks)
```

### A.2 `python tools/verify_samples.py`

Plain invocation (raw, as named in the gate):

```text
Traceback (most recent call last):
  File "D:\...\tools\verify_samples.py", line 10, in <module>
    from code.solver import solve_request
ModuleNotFoundError: No module named 'code.solver'; 'code' is not a package
```

With `$env:PYTHONPATH="$PWD"` (read-only re-run; 25 `Solving ...` lines + 75 mismatch lines = 101 output lines, exit 0):

```text
Audit completed. Found 75 mismatches.
```

**Number to record: 75 known sample mismatches** (accepted, recorded deviation — not treated as Critical per gate instructions; sample of the raw mismatch lines, e.g. `[request_21] spending_changes_needed: expected stop:event_1815|reduce_to:event_1816:23.50, got none`).

---

## Item-by-item answers

### 1. `output.csv` — VERIFIED (counted myself)

Command: Python `csv` module over `output.csv` cross-checked against `dataset/requests.csv`.

```text
header: ['request_id', 'amount_safe_to_pay', 'affordability_status', 'recommended_payment_method', 'payment_plan', 'earliest_date_for_full_payment', 'spending_changes_needed', 'decision_explanation']
n_cols: 8
data_rows: 250
rows_with_wrong_field_count: []
unique_ids: 250
dupes: []
requests_rows: 250
missing_from_output: []
extra_in_output: []
order_same: True
```

Also: line endings `CRLF count: 0; BOM: False` (LF, as README:104 claims). Column order matches AGENTS.md §6.2 and the README table exactly. Bounds/vocabulary invariants independently re-confirmed by A.1.

### 2. `README.md` — VERIFIED, with one flag

Checked every command, env var, path, and number:

- **Numbers:** 250 requests/rows ✓ (counted); 50-call ceiling ✓ (`MAX_PROVIDER_CALLS = 50`, `code/usage.py:7`; re-clamp `code/explanations.py:162`); 90-day forecast / 91-point ledger ✓ (`code/oracle.py:430,434` use `91`, `code/solver.py:70` requires `len != 91`); six scoring axes ✓ (quoted in `problem_statement.md:87-93`); seven edge cases ✓ (7 rows in the doc, names match README:160-162); "up to three" spending changes ✓ (`code/changes.py:105 range(1,4)`); "both entrypoints equivalent" ✓ (`code/main.py:12-25` handles package/script); "LF line endings" ✓ (0 CRLFs); "zero provider calls with no key" ✓ (`code/explanations.py:169-173`).
- **Commands:** `python -m code.main --help` and `python code/main.py --help` both executed → both print the docstring, `EXIT=0`, and `output.csv` mtime stayed `8:06:28` (no pipeline ran) ✓. `pip install pandas` / `pip install rich` ✓ (both importable: `pandas: True`, `rich: True`). `python -m tools.tui` → module resolves (`tools.tui: True`) — **not executed** (interactive); static read confirms session-only/no-LLM claims (`tools/tui.py` never imports `generate_explanation`, never writes `output.csv`/`dataset/` — lines 10, 85, 986, 1021 are all "never written" assertions).
- **Env vars:** all 8 exist in source — `BUY_WAIT_LLM_ENDPOINT` (explanations.py:41,170), `BUY_WAIT_LLM_API_KEY` (42,171), `OPENROUTER_KEY` (42,170,171 + usage.py:29), `BUY_WAIT_LLM_MODEL` (43, usage.py:36), `BUY_WAIT_LLM_PROVIDER` (96, usage.py:35), `BUY_WAIT_LLM_TIMEOUT_SECONDS` (81), `BUY_WAIT_LLM_MAX_CALLS` (usage.py:31), `TESSERACT_CMD` (ocr.py:351,374).
- **Paths/claims:** all 16 architecture-table modules exist; docstring claims spot-checked and true (recurrence "≥3 observations + CV gate" → `recurrence.py:1,14,62`; fx "exact → nearest prior → None + FX_UNKNOWN" → `fx.py:1-7,22`; reconcile 4-step precedence → `reconcile.py:1-9`; parser salary/amendment extraction → `parser.py:1-8`; amounts ID-digit stripping → `amounts.py:5-6` and imported by `ocr.py`, `parser.py`, `reconcile.py`; ranking 6-rank order → `ranking.py` sort key; `_schema_ok`/`_grounded` → `explanations.py:15,115`; TUI `explain` prints solver-computed explanation → `solver.py:453,477`). `log.txt` gitignored ✓ (`.gitignore` line 1) and never committed ✓ (`git log --all --oneline -- log.txt` → empty). `evaluation/usage_report.md` mirrored ✓, `code.zip` includes it ✓.
- **Flagged (cannot confirm / false):** README:164's "verified `file:line` enforcement citations" — 2 of 35 do not resolve today (F1). README inside `code.zip` links `./docs/edge-case-policy.md` and documents `python -m tools.tui`, neither shipped in the archive (F4). `README.md:53` carries gold ID `event_1815` (F2).

### 3. `docs/edge-case-policy.md` citations — 35 checked, 33 resolved

Enumerated every `file:line` in the 7 table rows + the T2-c note: **35 citation instances, 32 unique**. Printed each cited line from the working tree and judged against the parenthesized claim.

- **33/35 resolve correctly (30/32 unique).** Includes anchors at def/comment granularity, e.g. `oracle.py:53` (`def _is_countable`), `oracle.py:90` (`def _dedup_events`), `oracle.py:289` (comment "Ignore pending credits, reserve pending debits" + `continue`), `oracle.py:118` (cancellation-status guard that drops the linked parent), `solver.py:70` (`if len(bal_list) != 91:`), `changes.py:105` (`for size in range(1, 4):`), `changes.py:109` (per-event dedupe ⇒ stop/reduce exclusive), `fx.py:22` (`return {"rate": None, "flag": "FX_UNKNOWN", ...}`), `oracle.py:426-431` (fail-closed returns `0 / None / 91-point ledger`), note-section `solver.py:417` (comment "Re-evaluate under the SAME semantics"), etc. One borderline-but-accepted: `solver.py:83` computes the horizon inside the exact "legs beyond the horizon" block (actual `return False` at line 88).
- **2/35 do NOT resolve:** `code/main.py:90` (claim: strict mode pinned) and `code/main.py:83` (claim: fail-closed trip) → **F1**. Both claims are true of the code at `main.py:137` / `main.py:130`; only the line numbers are stale.

### 4. Usage reports — agree with each other and with everything checkable

- **Byte-identical:** `Get-FileHash` both = `D156A7B348223286AA71E0FE44F1D51F11D7E63032C231178A2D40A87B8AD55A`; `(Get-Content a) -ceq (Get-Content b)` → `True`. Both mtime `10/8/2026 8:06:28` = same mtime as `output.csv` (same run).
- **Actual numbers:** Provider `openrouter`; Model header `Ling 3.0 Flash Fin`, per-model row `inclusionai/ling-3.0-flash-fin` (by design: header = display name from `new_usage()`, row = API id returned by `_configured_call`, `explanations.py:48-49,105`); **Model calls 25**; Successful 8; Failed 0; Timeouts 0; Rejected 242; Cache hits 0; Fallback explanations 242; **Input tokens 11,898; Output 19,190; Total 31,088; Average/request 124.352**; **Cost status: unavailable** (estimated total and per-request both `unavailable`; unpriced estimate `0.000000` explicitly labeled not-a-paid-zero).
- **Arithmetic verified:** 11898+19190 = 31088 ✓; 31088/250 = 124.352 ✓; 8 successful + 242 fallback = 250 ✓; 25 calls ≤ 50 ceiling ✓.
- **Reproducibility verified:** I reconstructed a `usage` dict matching the report and called the current `code.usage.write_usage_report` → output `IDENTICAL_TO_PUBLISHED: True`. The report is exactly what current code emits for those counters (this also proved the header/row model-name difference is generated, not hand-edited).
- **Consistency with the code's semantics:** internally consistent only if the run used `BUY_WAIT_LLM_MAX_CALLS=25` (25 attempts then budget cap; see F3 arithmetic). Label issue at F3; provenance gap at F6.

### 5. Credentials scan — CLEAN

**Scope:** 36 files — root `README.md`; all `*.md,*.py,*.txt,*.csv` under `docs/`, `evaluation/`, `tools/` (16 files); every text entry extracted from `code.zip` (20 files). Patterns: `sk-[A-Za-z0-9_-]{6,}`, `AKIA[0-9A-Z]{16}`, `ghp_[A-Za-z0-9]{20,}`, `Bearer\s+\S{8,}`, `OPENROUTER_KEY=<value>`, `(api_key|API_KEY)=<literal>`, `sk-or-…`, plus `.env` filename search in root/docs/evaluation/tools/zip.

**Raw result:**

```text
PATTERN [sk-]: 1 hits
   docs\superpowers\plans\2026-09-13-buy-or-wait-fix-cycle.md:3: sk-by-task
PATTERN [AKIA]: 0 hits
PATTERN [ghp_]: 0 hits
PATTERN [Bearer+token]: 0 hits
PATTERN [OPENROUTER_KEY=value]: 0 hits
PATTERN [hardcoded api_key]: 0 hits
PATTERN [sk-or-]: 0 hits
=== .env files anywhere in scope === (none)
```

The single `sk-` hit is a **false positive**: line 3 is `... implement this plan task-by-task.` (regex matched the substring `sk-by-task`). Supplementary plain `Bearer` scan → only `code/explanations.py:77` header template `f"Bearer {api_key}"` and `tools/correctness_report.py:67`'s own scan regex; `OPENROUTER_KEY` appears 8×, always as an environment-variable *name* (`.get(...)` reads), never with a value. No `.env` exists outside `.venv` anywhere in the repo.

### 6. `code.zip` — VERIFIED

**Entry list (raw, `Size<TAB>Path`, 20 entries, 0 directory entries):**

```text
9712    README.md
10154   code/README.md
76      code/__init__.py
3678    code/amounts.py
6869    code/changes.py
4707    code/evidence.py
12310   code/explanations.py
1270    code/fx.py
7895    code/main.py
17213   code/ocr.py
4529    code/options.py
18858   code/oracle.py
4138    code/parser.py
962     code/ranking.py
3345    code/reconcile.py
2714    code/recurrence.py
21764   code/solver.py
5063    code/usage.py
972     evaluation/usage_report.md
13052   problem_statement.md
COUNT=20
```

- **Single-level paths:** all entries are root files or one directory deep (`code/*`, `evaluation/*`); no `./` prefix, no `\` or drive-letter absolute paths, no `..`; 0 explicit directory entries. ✓
- **Contains `evaluation/usage_report.md`:** ✓ (and byte-identical to the repo copy: `IDENTICAL`).
- **Forbidden entries:** no `.env`, `log.txt`, `.git`, `__pycache__`, `.superpowers` anywhere in the list. ✓
- **`code/` vs working tree (extracted to `$env:TEMP\review3_zip_extract`, diffed):**

```text
PATH LISTS IDENTICAL
(no CONTENT_DIFF lines — every code/* file hash-matched)
README.md: IDENTICAL
problem_statement.md: IDENTICAL
evaluation\usage_report.md: IDENTICAL
```

  The diff is empty: same 17 `code/` files (16 `.py` + `README.md`), zero byte differences, zero missing/extra files. (Packaging gaps F4 noted separately: no `docs/`, no `tools/`.)

---

## Mandatory question — gold reproduction risk

**Question:** *does anything in the artifacts risk special-casing the public sample IDs `event_476`, `event_989`, `event_1815`, `event_1816`?*

**Answer: No risk of special-casing. No occurrence in `code/` (0), no occurrence in `output.csv` (0). Per the gate rule ("any occurrence in `code/` is Critical"), there is no Critical finding.**

Raw output:

```text
=== grep output.csv ===
=== grep code/ ===
=== done ===
```

(`Select-String -Path output.csv` and all `code\*.py,*.md` for `event_476|event_989|event_1815|event_1816` — zero matches. Independently corroborated by `correctness_report.py` → `PASS no_sample_event_ids ... (18 files scanned)`.)

Honest disclosures of the same literals elsewhere on the wider surface (none of it executable, none submitted as logic):

- `README.md:53` — prose example only → **F2** (Minor; and because README ships inside `code.zip`, the literal is present in the archive's README, still not under `code/`).
- `.superpowers/sdd/2026-10-08-*/...` internal review/brief files — process artifacts, **not** in `code.zip`, not in scope.
- `dataset/sample_requests.csv` and `tests/` fixtures — public samples/expected-value checks by design, not submitted logic.

`code/amounts.py` contains no gold literal; its docstring was already rewritten to "after stripping ID citations" (the earlier leak is recorded as fixed in `.superpowers/.../task-8-report.md`).

---

## What I could NOT verify (and why)

1. **That the LLM run behind the usage report actually happened as counted.** Reproducing it would require paid API calls and would regenerate artifacts — forbidden. I verified everything short of that: byte-level reproduction of the report from current code, internal arithmetic, shared mtime with `output.csv`, and counter semantics in `explanations.py`. The run's env (`BUY_WAIT_LLM_MAX_CALLS=25`) is inferred from arithmetic; no log entry records it (**F6**).
2. **`tools/tui.py` interactive behavior** — not launched (interactive session; out of the two sanctioned scripts). Verified statically only.
3. **Full pytest suite** — not run; the gate authorized only `correctness_report.py` and `verify_samples.py`.
4. **`verify_samples` against the gold-count record** — observed 75 vs recorded 71 (stale record in `tests/open_issues.md`, out of scope); both are the same accepted deviation class.
5. **Whether `dataset/` matches the grader's copy** — outside my scope; I only used `dataset/requests.csv` as the ID reference per instructions.
6. **Pre-7:55 history of `code/main.py`** — repo has a `.git`, but I verified the stale-citation root cause by mtimes and by arithmetic (exact 47-line offset) rather than by rewriting history; `git log`/`git status` used read-only.
