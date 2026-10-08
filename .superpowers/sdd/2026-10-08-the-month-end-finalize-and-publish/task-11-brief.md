## Task 11: Build the squashed 3-commit release branch

**Split rule:** group by path; never split one file across commits.

| # | Message | Paths |
|---|---|---|
| 1 | `feat: implement Buy or Wait? financial decision agent` | `code/`, `tests/`, `evaluation/`, `tools/verify_samples.py`, `tools/evaluation/`, `dataset/`, `output.csv`, `docs/` |
| 2 | `build: add TUI, release archiver, correctness harness, and ignore generated artifacts` | `tools/tui.py`, `tools/build_release.py`, `tools/correctness_report.py`, `.gitignore` |
| 3 | `docs: competition context, edge-case policy, and final review record` | `README.md`, `plan_a.md`, `sessions_context.md`, `tests/open_issues.md`, `.superpowers/` |

- [ ] **Step 1: Create the branch**

```powershell
git checkout -b release origin/main
git status --short
```

- [ ] **Step 2: Commit group 1**

```powershell
git add code/ tests/ evaluation/ tools/verify_samples.py tools/evaluation/ dataset/ output.csv docs/
git status --short
git commit -m "feat: implement Buy or Wait? financial decision agent"
```

Confirm nothing forbidden is staged.

- [ ] **Step 3: Commit group 2**

```powershell
git add tools/tui.py tools/build_release.py tools/correctness_report.py .gitignore
git commit -m "build: add TUI, release archiver, correctness harness, and ignore generated artifacts"
```

- [ ] **Step 4: Commit group 3**

```powershell
git add README.md plan_a.md sessions_context.md tests/open_issues.md .superpowers/
git commit -m "docs: competition context, edge-case policy, and final review record"
```

- [ ] **Step 5: Verify exactly 3 commits**

```powershell
git log --oneline origin/main..release
git log --oneline origin/main..release | Measure-Object -Line
```

---

