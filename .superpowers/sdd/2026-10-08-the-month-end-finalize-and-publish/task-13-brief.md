## Task 13: Prepare the submission bundle

The bundle is `code.zip`, `output.csv`, and `chat_transcript` where the transcript **is** the gitignored `log.txt`.

- [ ] **Step 1: Confirm all three exist**

```powershell
Get-Item code.zip, output.csv, log.txt | Select-Object Name, Length, LastWriteTime
```

- [ ] **Step 2: Final sweep**

```powershell
python -m pytest tests/ -q
python tools/correctness_report.py
python tools/build_release.py
```

- [ ] **Step 3: Upload** `code.zip`, `output.csv`, and `log.txt` at:

https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission

---

## Remaining-Work Statement

Verified this session:

- Full suite **278 passed, 1 skipped** — green
- **Root cause proven against gold samples**: 3 of 25 gold rows emit spending changes, all `affordable_with_plan` + `full_payment`; 3 of 4 target events are `subscription`, which `changes.py:92` discards
- Both `reduce_to` amounts equal `minimum_allowed_amount` exactly, confirming `get_candidates` logic
- Provider accounting fixed — nonzero calls and tokens
- `log.txt` confirmed by the official README as the correct `chat_transcript`
- Fork still attached upstream

| # | Item | Task |
|---|---|---|
| 1 | Oracle never forecasts flexible categories, so spending changes cannot move `safe_amount` | 1 |
| 2 | `changes.py` drops all 1,699 subscriptions and requires an impossible in-window date | 2 |
| 3 | Downstream status/date/safe-amount not recomputed from adjusted cashflow | 3 |
| 4 | No usable interface — CSV only | 4 |
| 5 | No schema-checked LLM contract | 5 |
| 6 | No explicit edge-case policy | 6 |
| 7 | README lacks competition context | 7 |
| 8 | No correctness harness; stale `code.zip`; `must_upload` nesting defect | 8, 9 |
| 9 | 28 noisy commits would hit the profile | 11 |
| 10 | No independent review gate | 10 |
| 11 | Not published standalone | 12 |
| 12 | No submission bundle staged | 13 |

Deliberately **excluded**:

- **Understand skill / knowledge graph** — dropped by user instruction.
- **Model-driven tool-calling loop** — the judge suggested exposing computations as model-callable tools. It does not affect `output.csv` correctness, and the 50-call ceiling makes iterative tool loops impractical. Future work.
- **Ollama fallback** — withdrawn; no working local chat model exists.
- **Resume logging** — user instruction.
- **Pricing configuration** — no verified price; report truthfully says `unavailable`.
- **SDD Task 4 (sample/ranking optimization)** — deferred; sample-exact-match passes 71/71.
- **Gold sample IDs in production code** — explicitly forbidden; Reviewer 4 treats any literal as Critical.
