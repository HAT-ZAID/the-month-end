## Task 9: Hygiene

- [ ] **Step 1: Remove duplicated artifacts**

```powershell
Get-ChildItem must_upload -Recurse -File | Select-Object FullName, Length
Remove-Item -Recurse -Force must_upload
```

- [ ] **Step 2: Audit `check_eval.py`**

```powershell
Get-Content check_eval.py
```

Delete if it duplicates `tools/evaluation/main.py` or `tools/evaluation/harness.py`; otherwise move under `tools/`.

- [ ] **Step 3: Set `.gitignore`**

```
log.txt
__pycache__/
*.pyc
.venv/
.pytest_cache/
code.zip
.ua/
```

- [ ] **Step 4: Verify nothing forbidden is tracked**

```powershell
git ls-files | Select-String -Pattern "__pycache__|\.pyc$|\.venv|log\.txt$|code\.zip|must_upload"
```

Expected: no matches.

- [ ] **Step 5: Verify suite green**

```powershell
python -m pytest tests/ -q
```

---

