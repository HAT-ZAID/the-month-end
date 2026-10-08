# Task 5 Report: Schema-checked LLM explanation contract

**Status:** DONE
**Commit:** `ef4a6d1` — `feat: enforce schema on structured LLM explanation responses` (branch `sol/p1-inputs-oracle`; staged only `code/explanations.py` and `tests/test_llm_explanations.py`)

## What was done

1. Added the two brief tests verbatim to `tests/test_llm_explanations.py`.
2. RED run: `test_provider_insufficient_evidence_is_accepted_when_grounded` failed (`successful == 0`); `test_provider_must_return_schema_fields_or_fallback` passed already, but only because grounding (not schema) rejected `{"note": ...}`.
3. Implemented in `code/explanations.py`:
   - `_REQUIRED_KEYS` / `_OPTIONAL_KEYS` / `_schema_ok` exactly per brief (required: `decision_explanation`, `evidence_sufficient`, `supporting_facts`; optional: `missing_evidence`; no other keys; bool/list/string types).
   - Structured path (raw is a dict, i.e. `_configured_call` or dict-returning callbacks): accepts only `_schema_ok` output or the canonical single-key `{"decision_explanation": str}`; any other key (e.g. `note`) → `rejected` + deterministic fallback. Arbitrary single-key leniency now exists only for injected string callbacks.
   - System prompt now names the exact keys, forbids others, and requires `evidence_sufficient: false` whenever `missing_evidence` is non-empty.
   - Root-cause fix for the second test: `_grounded` now also extracts number tokens from `supporting_facts` (the model cites `amount_safe_to_pay=0` there; the prose alone never contains a standalone `0`). Prose-level checks (length, method, warnings, contradiction) still run on the explanation text only.

## Evidence

- `python -m pytest tests/test_llm_explanations.py -q` → **17 passed** (15 pre-existing + 2 new).
- Same run with `$env:BUY_WAIT_LLM_MAX_CALLS='1'` → **17 passed**; zero live network calls (tests inject `call` or monkeypatch `urlopen`).

## Concerns

- Pre-existing tests `test_generate_explanation_records_provider_tokens_and_cost_metadata` and `test_missing_provider_usage_is_reported_without_inventing_tokens` inject dict callbacks whose content is `{"decision_explanation": "..."}` only, so full three-key strictness there would break them. Per the "pre-existing tests stay green" rule, the structured path accepts the canonical single key as a legacy carve-out; `{"note": ...}` and any other/malformed key set is rejected.
- No secrets written anywhere; API-key usage in tests remains the pre-existing placeholder `test-key-not-secret`.
- `log.txt` not appended, per instructions.
