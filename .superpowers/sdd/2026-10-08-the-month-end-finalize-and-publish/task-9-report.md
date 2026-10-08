# Task 9 Report: Hygiene

Status: COMPLETE
Branch: `sol/p1-inputs-oracle`
Commit: `ac2f1d8` — `chore: repo hygiene - drop must_upload, move check_eval under tools, pin gitignore/gitattributes`

## Step 1 — must_upload/ removed

Recursive listing (taken before deletion, 40 files):

| File | Bytes |
|---|---|
| must_upload\code.zip | 41705 |
| must_upload\log.txt | 115950 |
| must_upload\output.csv | 52996 |
| must_upload\code\README.md | 15025 |
| must_upload\code\evaluation\usage_report.md | 875 |
| must_upload\code\code\__init__.py | 76 |
| must_upload\code\code\amounts.py | 3672 |
| must_upload\code\code\changes.py | 6525 |
| must_upload\code\code\evidence.py | 4707 |
| must_upload\code\code\explanations.py | 9440 |
| must_upload\code\code\fx.py | 1270 |
| must_upload\code\code\main.py | 5567 |
| must_upload\code\code\ocr.py | 17213 |
| must_upload\code\code\options.py | 4529 |
| must_upload\code\code\oracle.py | 18115 |
| must_upload\code\code\parser.py | 4138 |
| must_upload\code\code\ranking.py | 962 |
| must_upload\code\code\README.md | 15025 |
| must_upload\code\code\reconcile.py | 3345 |
| must_upload\code\code\recurrence.py | 2714 |
| must_upload\code\code\solver.py | 21182 |
| must_upload\code\code\usage.py | 5063 |
| must_upload\code\code\evaluation\usage_report.md | 875 |
| must_upload\code\code\code\__init__.py | 76 |
| must_upload\code\code\code\amounts.py | 3672 |
| must_upload\code\code\code\changes.py | 6525 |
| must_upload\code\code\code\evidence.py | 4707 |
| must_upload\code\code\code\explanations.py | 9440 |
| must_upload\code\code\code\fx.py | 1270 |
| must_upload\code\code\code\main.py | 5567 |
| must_upload\code\code\code\ocr.py | 17213 |
| must_upload\code\code\code\options.py | 4529 |
| must_upload\code\code\code\oracle.py | 18115 |
| must_upload\code\code\code\parser.py | 4138 |
| must_upload\code\code\code\ranking.py | 962 |
| must_upload\code\code\code\reconcile.py | 3345 |
| must_upload\code\code\code\recurrence.py | 2714 |
| must_upload\code\code\code\solver.py | 21182 |
| must_upload\code\code\code\usage.py | 5063 |

Finding: duplicated content — the same `code/` tree nested three deep (`code/code/code/`), plus a nested `code.zip`, `log.txt` copy, and `output.csv` copy. Directory was untracked (never in `git ls-files`). Deleted with `Remove-Item -Recurse -Force`; `Test-Path must_upload` → `False`. Root `output.csv`, `code.zip`, `dataset/`, `.superpowers/` untouched.

## Step 2 — check_eval.py audit

**Verdict: does NOT duplicate `tools/evaluation/main.py` or `tools/evaluation/harness.py` → moved to `tools/check_eval.py`.**

Evidence:
- `tools/evaluation/main.py` is empty (0 bytes / 0 lines) — no overlap possible.
- `tools/evaluation/harness.py` defines `normalize_decimal()` (harness.py:13), `audit_field()` (harness.py:24), `run_audit()` (harness.py:32): a per-field gold-vs-solver comparison over 6 fields.
- `check_eval.py` is a top-level script with no functions, 11 sections: completeness, column order, vocabulary, amount bounds, earliest-date consistency, plan consistency, status/method consistency, distributions, **sample-vs-output comparison (§10)**, spending-change format.
- Only overlap: §10 (per-field gold comparison of `affordability_status`, `recommended_payment_method`, `payment_plan`, `spending_changes_needed`) partially mirrors `harness.run_audit()`. The other 10 sections (completeness, column order, vocab, bounds, distributions, format checks) have no counterpart in either `main.py` or `harness.py`, so it is not a duplicate. Not staged as a delete; added as new file `tools/check_eval.py`.
- Note (out of scope): `tools/correctness_report.py` overlaps heavily with check_eval's generic checks, but the brief's deletion criterion is only main.py/harness.py.

## Step 3 — .gitignore

Set to exactly:
```
log.txt
__pycache__/
*.pyc
.venv/
.pytest_cache/
code.zip
.ua/
```

`code.zip` untracked but present on disk:
- `git rm --cached code.zip` (no `-f`, file not deleted) → staged `D  code.zip`.
- Post-commit: `Test-Path code.zip` → `True`; `git status` shows no pending change for it.
- `git show HEAD:code.zip` → fatal (not in HEAD) after commit.

## Step 4 — Line-ending safety

Added `.gitattributes`:
```
* text=auto
*.csv text eol=lf
*.md text eol=lf
```

Byte check of the committed blob (raw bytes via `cmd /c git show HEAD:output.csv > file`, then scan for `0x0D`):
- **`HEAD:output.csv`: 50870 bytes, CR(0x0D) count = 0 → blob is pure LF.**
- Worktree `output.csv`: 50870 bytes, CR = 0 (already LF).
- `git check-attr text eol -- output.csv` → `text: set`, `eol: lf` (previously unspecified).

Would `.gitattributes` rewrite existing files? **No blob rewrite needed:** `git ls-files --eol | Select-String "i/crlf"` returns nothing — every tracked file is already `i/lf` in the index, so no `git add --renormalize` is required and none was run. Worktree copies of some tracked files are currently `w/crlf` (legacy `autocrlf=true` checkout); the new attributes mean a future checkout writes LF to those files, which is the intended effect, not a destructive change. Git emits only the informational "LF will be replaced by CRLF" warning for `* text=auto` files without eol rules (.py/.gitignore/.gitattributes) — index content stays LF either way.

## Step 5 — Forbidden-tracked check

Command: `git ls-files | Select-String -Pattern "__pycache__|\.pyc$|\.venv|log\.txt$|code\.zip|must_upload|\.env$"`

Actual output: **(empty — no matches)** — run both before the commit (after `git rm --cached`) and against the committed tree. Pass.

## Step 6 — Verification

`python tools/correctness_report.py` → `RESULT: PASS (15/15 checks)`, exit 0.
Full pytest suite not run (controller runs it), per instructions. `log.txt` untouched (not appended to).

## Files committed (ac2f1d8)

- `.gitignore` (modified)
- `.gitattributes` (new)
- `tools/check_eval.py` (new; root `check_eval.py` was untracked, so no git mv/delete entry)
- `code.zip` (untracked from index)

Nothing else staged; `.superpowers/`, `.env`, `log.txt`, `output.csv`, `dataset/` not staged or modified.

## Concerns

- `tools/evaluation/main.py` remains an empty placeholder (0 bytes); left as-is — outside this task's scope.
- Pre-existing unrelated worktree changes (`code/ranking.py`, `tests/*`, deleted `code/evaluation/*`, untracked `.superpowers/**`) were deliberately left uncommitted.
- `must_upload/` contained a `log.txt` copy (115950 bytes); the repo-root `log.txt` was not touched.
