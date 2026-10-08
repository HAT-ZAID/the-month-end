## Task 8: Rebuild artifacts and correctness harness

**Files:**
- Create: `tools/build_release.py`, `tools/correctness_report.py`
- Regenerate: `code.zip`, `output.csv`, `evaluation/usage_report.md`, `tools/evaluation/usage_report.md`

- [ ] **Step 1: Write the release builder**

Include `README.md`, `problem_statement.md`, and the `code/` + `evaluation/` trees; exclude `__pycache__`, `.pytest_cache`, `.venv`, `.pyc`; write entries via `path.relative_to(REPO_ROOT).as_posix()` so `code/` appears at exactly one level.

- [ ] **Step 2: Write the correctness harness**

The judge said there was no visible way to measure recommendation correctness. `tools/correctness_report.py` prints deterministic checks, not provider usage:

- exactly 250 rows; `request_id` covers `dataset/requests.csv` exactly
- exact column order
- `0 <= amount_safe_to_pay <= requested_amount` on every row
- statuses and methods within the allowed sets
- `affordable_now` implies `earliest_date_for_full_payment == request_date`
- `partial_payment` plans have exactly two legs summing to `requested_amount`, leg 1 on `request_date`
- installment plans exactly match a supplied option
- every spending-change action targets a `settled`/`scheduled`/`pending` debit event, non-protected, in the user's stop/reduce list, at most three actions, stop/reduce mutually exclusive
- `decision_explanation` non-empty and under 400 characters
- counts per status and per method for human review

PASS/FAIL per check; exit non-zero on any failure.

- [ ] **Step 3: Run the harness on current output**

```powershell
python tools/correctness_report.py
```

Expected: all structural checks PASS; spending-change count reported as informational.

- [ ] **Step 4: Regenerate with a bounded provider batch**

```powershell
$env:BUY_WAIT_LLM_MAX_CALLS='20'; $env:BUY_WAIT_LLM_TIMEOUT_SECONDS='15'
python -c "import os,pathlib; p=pathlib.Path(r'D:\Hackerrank_Orchestrate\.env'); [os.environ.__setitem__(*(l.split('=',1)[0].strip(), l.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))) for l in p.read_text().splitlines() if '=' in l and not l.strip().startswith('#')]; import code.main; code.main.main()"
```

Expected: `Wrote 250 rows to output.csv`, `Model calls` ≤ 20.

- [ ] **Step 5: Re-run harness, rebuild archive**

```powershell
python tools/correctness_report.py
python tools/build_release.py
python -c "import zipfile; n=zipfile.ZipFile('code.zip').namelist(); print(len(n)); print([x for x in n if 'usage_report' in x])"
```

Expected: all structural checks PASS; nonzero spending-change count; archive has `evaluation/usage_report.md` at one nesting level.

- [ ] **Step 6: Scan for the leaked key**

```powershell
python -c "from pathlib import Path; k=[l.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)) for l in Path(r'D:\Hackerrank_Orchestrate\.env').read_text().splitlines() if l.strip().startswith('OPENROUTER_KEY=')][0]; fs=['output.csv','evaluation/usage_report.md','tools/evaluation/usage_report.md','code/explanations.py','code/usage.py','tools/build_release.py','tools/correctness_report.py','tools/tui.py','README.md','docs/edge-case-policy.md']; print({f:(k in Path(f).read_text(errors='ignore')) for f in fs})"
```

Expected: every value `False`.

---

