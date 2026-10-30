# Agentic vs deterministic decisions

Provider: `ollama:gemma4:e2b-it-qat`
Requests compared: **3** (first 3 in dataset order)

## Guardrail

- Machine fields identical: **3/3**
- Verifier rewrote the explanation from the model's rationale: **0/3**
- Agent fallbacks: **3/3**

## Cost

- Model calls: **14** (4.7 per request)
- Tool calls: **12** (4.0 per request)
- Wall clock per request: mean **92.17s**, max **103.72s**

## Per request

| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |
|---|---|---|---|---|---|---|---|
| request_26 | identical | kernel text | kernel | propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id | 5 | 5 | 91.813 |
| request_27 | identical | kernel text | kernel | propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id | 4 | 4 | 80.984 |
| request_28 | identical | kernel text | kernel | model returned neither a tool call nor a valid JSON candidate | 5 | 3 | 103.719 |

## Fallback reasons

- `request_26`: propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id
- `request_27`: propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id
- `request_28`: model returned neither a tool call nor a valid JSON candidate

## Reading this

The verifier is the authority: it recomputes the kernel row and keeps its own
machine fields, so a differing amount or status here would be a bug, not a
better answer. The agent's real contribution is the explanation, the evidence
it pulled in, and the trace a human can audit afterwards.
