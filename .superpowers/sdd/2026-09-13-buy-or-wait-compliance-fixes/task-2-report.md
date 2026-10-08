# Task 2 Report: Account Provider Tokens, Costs, and Bounded Calls

Date: 2026-09-13
Scope: `code/explanations.py`, `code/usage.py`, `tests/test_llm_explanations.py`, and the two required usage-report artifacts.

## RED evidence

Added regression tests before production changes for:

- structured provider content plus `prompt_tokens`, `completion_tokens`, and `total_tokens`;
- missing provider usage;
- HTTP/provider failure and timeout fallback;
- malformed provider JSON/content rejection;
- pre-request call-cap exhaustion;
- unknown pricing semantics and per-model/overall report rows.

Command:

```text
python -m pytest tests/test_llm_explanations.py -q
```

Expected RED result: `5 passed, 8 failed`.

The failures were the intended current gaps: `_configured_call()` returned a string instead of metadata, token counters were absent/zero, failure/rejection counters did not exist, the cap did not apply to injected calls, and `None` pricing raised while formatting the report.

## GREEN evidence

Command:

```text
python -m pytest tests/test_llm_explanations.py -q
```

Result: `13 passed`.

Command:

```text
python -m pytest tests/test_llm_explanations.py tests/test_submission.py -q -k "not documented_entrypoint_runs"
```

Result: `23 passed, 1 deselected`.

## Implementation and usage metadata

- `_configured_call(packet)` now returns content plus provider, model, input/output/total token counts, usage-presence state, and cost metadata. API keys remain request-header-only and are never returned or logged.
- `generate_explanation(packet, call=None)` remains string-returning and backward compatible. It accepts legacy string callbacks and structured metadata, updates optional `_usage`, and leaves all machine decision fields untouched.
- Usage tracks actual calls, successful calls, failed calls, timeouts, rejected responses, fallbacks, cache hits, missing provider usage, token totals, per-model rows, and capped calls.
- Unknown provider/model pricing is explicitly `unavailable`; it is not silently reported as a paid zero. A documented zero line is emitted only to make the no-price state unambiguous.
- Pricing is configured through `PRICING_USD_PER_MILLION_TOKENS`; the default is intentionally empty until a verified provider/model price is supplied.
- Provider timeout is read from `BUY_WAIT_LLM_TIMEOUT_SECONDS` and clamped to 1–15 seconds, with no retry loop. Timeout/provider errors immediately use deterministic fallback prose.
- `max_calls` is clamped to `0..50` in `new_usage()` and again before a request in `generate_explanation()`.

## Bounded provider smoke and batch

The parent `.env` was used without printing its value. The configured model was not changed. A one-call provider-enabled smoke was attempted and printed only sanitized counters:

```text
{"calls": 1, "cost_status": "unavailable", "failed": 1, "input_tokens": 0, "model": "Ling 3.0 Flash Fin", "output_tokens": 0, "provider": "openrouter", "rejected": 0, "successful": 0, "total_tokens": 0}
```

The provider failed fast; no key or response body was exposed. A capped production batch then ran through `python code/main.py` with `BUY_WAIT_LLM_MAX_CALLS=1` and produced 250 rows. Both reports record the exact batch:

- actual provider calls: `1`;
- successful calls: `0`;
- failed calls: `1`;
- cap rejections: `249`;
- fallbacks: `250`;
- input/output/total tokens: `0/0/0`;
- pricing: `unavailable`;
- configured ceiling used: `1` (never above the required maximum of `50`).

Reports updated:

- `evaluation/usage_report.md`
- `tools/evaluation/usage_report.md`

No credential appears in either report or the generated output surface.

## Full test output

Final full-suite command, run with the same one-call cap and no model change:

```text
python -m pytest -q
```

Result: `267 passed, 1 skipped, 9 failed`.

The nine failures are pre-existing out-of-scope worktree issues in spending-change behavior (`tests/test_changes.py`), OCR/evidence inventory (`tests/test_hybrid_evidence.py`), generated-output invariants (`tests/test_invariants.py`), and solver spending fallback (`tests/test_solver.py`). No Task 2 test failed in this run; the focused Task 2 and submission-contract checks are green.

## Changed files

- `code/explanations.py`
- `code/usage.py`
- `tests/test_llm_explanations.py`
- `evaluation/usage_report.md`
- `tools/evaluation/usage_report.md`
- this report file

`code/main.py` and `tests/test_submission.py` were not modified. The required production batch regenerated the pre-existing root `output.csv`, but it was not staged as a Task 2 source change.

## Self-review

- No provider credential is stored in source, metadata, reports, output, or the task report.
- The configured model remains the existing `Ling 3.0 Flash Fin` setting; the existing provider-specific request mapping remains intact.
- The hard ceiling is enforced before invoking both configured and injected provider calls.
- Successful structured responses preserve provider token fields; missing usage remains zero and is counted as missing rather than invented.
- Machine decision fields are never read from or overwritten by provider output; only grounded `decision_explanation` is accepted.
- Failure and timeout paths fall back deterministically and keep actual-call counts distinct from cap rejections.

## Concerns

The bounded provider call failed, so the final artifact does not contain a successful live response with nonzero tokens. The implementation and regression coverage support that path, but a successful provider-backed final report requires a reachable provider response with usage metadata. The full repository suite remains red for the unrelated pre-existing Task 1/Task 3 worktree issues listed above.
