# Agentic vs deterministic decisions

Provider: `ollama:llama3.2:3b`
Requests compared: **2** (first 2 in dataset order)

## Guardrail

- Machine fields identical: **2/2**
- Verifier rewrote the explanation from the model's rationale: **0/2**
- Agent fallbacks: **2/2**

## Cost

- Model calls: **6** (3.0 per request)
- Tool calls: **6** (3.0 per request)
- Wall clock per request: mean **327.67s**, max **600.38s**

## Per request

| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |
|---|---|---|---|---|---|---|---|
| request_26 | identical | kernel text | kernel | propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id; unexpected fields: spending_changes | 5 | 6 | 54.969 |
| request_27 | identical | kernel text | kernel | provider timeout: provider did not answer within 600s | 1 | 0 | 600.375 |

## Fallback reasons

- `request_26`: propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id; unexpected fields: spending_changes
- `request_27`: provider timeout: provider did not answer within 600s

## Reading this

The verifier is the authority: it recomputes the kernel row and keeps its own
machine fields, so a differing amount or status here would be a bug, not a
better answer. The agent's real contribution is the explanation, the evidence
it pulled in, and the trace a human can audit afterwards.
