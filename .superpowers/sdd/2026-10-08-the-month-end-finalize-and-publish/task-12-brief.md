## Task 12: Publish to the standalone repository

- [ ] **Step 1: Confirm clean, green, and review-gated**

```powershell
git status --short
python -m pytest tests/ -q
python tools/correctness_report.py
Get-Content .superpowers\sdd\final-review\verdict.md
```

- [ ] **Step 2: Create the repo** — name **`the-month-end`**, public.

```powershell
gh repo create the-month-end --public --description "Buy or Wait? AI financial decision agent - 90-day cash forecast, minimum-balance safety, and concrete spending-change recommendations" --add-readme=false
git remote add standalone https://github.com/HAT-ZAID/the-month-end.git
```

- [ ] **Step 3: Push as `main`**

```powershell
git push -u standalone release:main
gh repo edit the-month-end --default-branch main
```

- [ ] **Step 4: Verify the fork is untouched**

```powershell
gh repo view HAT-ZAID/hackerrank-orchestrate-september26 --json name,isFork,parent
```

- [ ] **Step 5: Verify contents and history**

```powershell
gh api repos/HAT-ZAID/the-month-end/contents --jq '.[].name'
gh api repos/HAT-ZAID/the-month-end/commits --jq '.[].commit.message'
```

Expected: 3 commits; no `log.txt`, no `code.zip`.

---

