# Agentic vs kernel, run 2 window (request_28)

Model: `llama3.2:3b` via local Ollama. Per-call timeout: **600 s**. Turn/tool budget:
**shipped 6/6** (the `--max-turns/--max-tool-calls` flags were accepted but not
forwarded to the runner in this build, so this window is not a 10-turn experiment).

## Result

| request | machine fields | explanation | fallback | model calls | tool calls | seconds | tokens (in/out) |
|---|---|---|---|---|---|---|---|
| request_28 | identical | kernel text | propose_decision rejected twice: missing `amount_safe_to_pay, confidence, evidence_sufficient, rationale, request_id`; unexpected `payment_method` | 6 | 6 | 454.09 | 13973 / 580 |

The verifier kept every machine field. Six times the run-1 patience produced no
additional valid proposal: the model reached the proposal step and still could not
emit the required typed fields.

## Not run, and why

`request_29` and `request_30` were reported in the first version of this window but
were **never executed**. `tools/experiments/compare_agentic.py` passed `--offset` to the reported
slice only, while the loop always starts at the first request in dataset order, so the
window advanced the report without advancing the run. The runner now receives
`offset + limit`, and the alignment was verified with a fail-fast provider run
(`--provider none --offset 2 --limit 1` → loop executes 26–28, report shows
`request_28`, zero model calls).

That bug also explains the "silent rows" seen elsewhere: unmeasured requests render
as blank cells. A second, related fault was inside the pipeline — a crashing agent
invocation was swallowed by a bare `except Exception`. It now prints the request id
and error frame to stderr and lands in `usage["agent_loop_errors"]`, which the
comparison report surfaces as a `Crashed agent invocations` section.
