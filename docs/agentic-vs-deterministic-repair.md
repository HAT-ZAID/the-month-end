# Agentic vs deterministic decisions

Provider: `ollama:gemma4:e2b-it-qat`
Requests compared: **3** (first 3 in dataset order)

## Guardrail

- Machine fields identical: **3/3**
- Verifier rewrote the explanation from the model's rationale: **2/3**
- Agent fallbacks: **0/3**

## Cost

- Model calls: **22** (7.3 per request)
- Tool calls: **22** (7.3 per request)
- Wall clock per request: mean **176.31s**, max **245.74s**

## Per request

| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |
|---|---|---|---|---|---|---|---|
| request_26 | identical | kernel text | kernel | — | 8 | 8 | 193.391 |
| request_27 | identical | rewritten | candidate | — | 5 | 5 | 89.797 |
| request_28 | identical | rewritten | candidate | — | 9 | 9 | 245.735 |

## Reading this

The verifier is the authority: it recomputes the kernel row and keeps its own
machine fields, so a differing amount or status here would be a bug, not a
better answer. The agent's real contribution is the explanation, the evidence
it pulled in, and the trace a human can audit afterwards.
