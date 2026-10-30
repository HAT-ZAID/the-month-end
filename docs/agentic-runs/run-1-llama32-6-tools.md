# Agentic vs deterministic decisions

Provider: `ollama:llama3.2:3b`
Requests compared: **5** (first 5 in dataset order)

## Guardrail

- Machine fields identical: **5/5**
- Verifier rewrote the explanation from the model's rationale: **0/5**
- Agent fallbacks: **5/5**

## Cost

- Model calls: **9** (1.8 per request)
- Tool calls: **6** (1.2 per request)
- Wall clock per request: mean **160.56s**, max **180.73s**

## Per request

| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |
|---|---|---|---|---|---|---|---|
| request_26 | identical | kernel text | kernel | propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id; unexpected fields: spending_changes | 5 | 6 | 80.297 |
| request_27 | identical | kernel text | kernel | provider timeout: provider did not answer within 180s | 1 | 0 | 180.438 |
| request_28 | identical | kernel text | kernel | provider timeout: provider did not answer within 180s | 1 | 0 | 180.625 |
| request_29 | identical | kernel text | kernel | provider timeout: provider did not answer within 180s | 1 | 0 | 180.704 |
| request_30 | identical | kernel text | kernel | provider timeout: provider did not answer within 180s | 1 | 0 | 180.734 |

## Fallback reasons

- `request_26`: propose_decision failed twice: candidate rejected: missing fields: amount_safe_to_pay, confidence, rationale, request_id; unexpected fields: spending_changes
- `request_27`: provider timeout: provider did not answer within 180s
- `request_28`: provider timeout: provider did not answer within 180s
- `request_29`: provider timeout: provider did not answer within 180s
- `request_30`: provider timeout: provider did not answer within 180s

## Reading this

The verifier is the authority: it recomputes the kernel row and keeps its own
machine fields, so a differing amount or status here would be a bug, not a
better answer. The agent's real contribution is the explanation, the evidence
it pulled in, and the trace a human can audit afterwards.
