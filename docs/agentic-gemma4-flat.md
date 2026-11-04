# Agentic vs deterministic decisions

Provider: `ollama:gemma4:e2b-it-qat`
Requests compared: **3** (first 3 in dataset order)

## Guardrail

- Machine fields identical: **3/3**
- Verifier rewrote the explanation from the model's rationale: **0/3**
- Agent fallbacks: **3/3**

## Cost

- Model calls: **11** (3.7 per request)
- Tool calls: **9** (3.0 per request)
- Wall clock per request: mean **71.45s**, max **84.56s**

## Per request

| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |
|---|---|---|---|---|---|---|---|
| request_26 | identical | kernel text | kernel | candidate disagreed with the deterministic kernel on payment_plan, earliest_date_for_full_payment; the kernel row stands | 4 | 3 | 78.766 |
| request_27 | identical | kernel text | kernel | candidate disagreed with the deterministic kernel on payment_plan, earliest_date_for_full_payment; the kernel row stands | 3 | 3 | 51.016 |
| request_28 | identical | kernel text | kernel | candidate disagreed with the deterministic kernel on recommended_payment_method; the kernel row stands | 4 | 3 | 84.563 |

## Fallback reasons

- `request_26`: candidate disagreed with the deterministic kernel on payment_plan, earliest_date_for_full_payment; the kernel row stands
- `request_27`: candidate disagreed with the deterministic kernel on payment_plan, earliest_date_for_full_payment; the kernel row stands
- `request_28`: candidate disagreed with the deterministic kernel on recommended_payment_method; the kernel row stands

## Reading this

The verifier is the authority: it recomputes the kernel row and keeps its own
machine fields, so a differing amount or status here would be a bug, not a
better answer. The agent's real contribution is the explanation, the evidence
it pulled in, and the trace a human can audit afterwards.
