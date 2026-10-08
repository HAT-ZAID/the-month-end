# Task 2: Account provider tokens, costs, and bounded calls

## Requirements

Modify `code/explanations.py`, `code/usage.py`, and, only if needed for the report contract, `code/main.py` and `tests/test_submission.py`. Modify `tests/test_llm_explanations.py` for regression coverage.

Preserve these interfaces:

- `_configured_call(packet) -> provider response metadata`
- `generate_explanation(packet, call=None) -> str`, backward compatible and updating optional `_usage`
- `write_usage_report(path, usage, request_count)`

Use TDD. Add RED tests for a provider response containing `choices[0].message.content` plus `usage.prompt_tokens`, `usage.completion_tokens`, and `usage.total_tokens`; missing usage; HTTP failure; timeout; malformed JSON; and call-cap exhaustion. Verify the tests fail because current provider content drops usage and token fields remain zero.

Return structured internal metadata containing explanation content, provider, model, input tokens, output tokens, total tokens, and cost metadata without exposing the API key. Add provider/model pricing configuration. If no price is configured, report `unavailable` or an explicitly documented zero estimate rather than silently implying a paid call cost of zero. Count actual provider calls, successful calls, fallback calls, cache hits, and rejected responses. Enforce `max_calls <= 50` before issuing a request. Bound timeout/retry behavior so a stalled provider immediately falls back to deterministic prose.

Run one provider-enabled smoke test and a capped production batch without exceeding 50 calls. Preserve exact final-run usage metadata in `evaluation/usage_report.md` and `tools/evaluation/usage_report.md`. Run full pytest and commit the task.

Global constraints: LLM output may generate only `decision_explanation`; it must never alter machine decision fields. Read credentials only from environment/parent `.env`; never log/package them. Keep reports truthful about actual calls, tokens, costs, and fallback counts. Keep `log.txt` append-only.

## Acceptance

A successful live call produces nonzero provider usage fields in the report; failed calls are counted separately; final report has per-model and overall rows; hard cap is enforced; no credential appears in logs, output, report, or archive.
