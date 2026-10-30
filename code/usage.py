from __future__ import annotations

import os
from pathlib import Path


MAX_PROVIDER_CALLS = 50

# Prices are deliberately opt-in.  A provider/model without an entry is reported
# as unavailable instead of being presented as a paid call costing zero.
PRICING_USD_PER_MILLION_TOKENS: dict[tuple[str, str], dict[str, float]] = {}


def pricing_for(provider: str, model: str) -> dict[str, float] | None:
    return PRICING_USD_PER_MILLION_TOKENS.get((provider, model))


def estimate_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> tuple[float | None, str]:
    pricing = pricing_for(provider, model)
    if pricing is None:
        return None, "unavailable"
    return (
        (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000,
        "estimated",
    )


def new_usage() -> dict:
    has_openrouter = bool(os.environ.get("OPENROUTER_KEY", "").strip())
    try:
        configured_max = int(os.environ.get("BUY_WAIT_LLM_MAX_CALLS", "50"))
    except ValueError:
        configured_max = MAX_PROVIDER_CALLS
    return {
        "provider": os.environ.get("BUY_WAIT_LLM_PROVIDER", "openrouter" if has_openrouter else "none"),
        "model": os.environ.get("BUY_WAIT_LLM_MODEL", "Ling 3.0 Flash Fin" if has_openrouter else "none"),
        "calls": 0,
        "successful": 0,
        "failed": 0,
        "failed_calls": 0,
        "timeouts": 0,
        "rejected": 0,
        "cache_hits": 0,
        "fallbacks": 0,
        "missing_usage": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "cost_status": "unavailable",
        "models": {},
        "max_calls": max(0, min(configured_max, MAX_PROVIDER_CALLS)),
    }


def _cost_text(value) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value):.6f}"


def write_usage_report(path: Path, usage: dict, request_count: int) -> None:
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens))
    average = total_tokens / request_count if request_count else 0
    cost = usage.get("estimated_cost_usd")
    cost_status = usage.get("cost_status")
    if cost_status not in {"estimated", "unavailable"}:
        cost_status = "estimated" if cost is not None else "unavailable"
    cost_value = _cost_text(cost) if cost_status != "unavailable" else "unavailable"
    per_request_cost = "unavailable" if cost_value == "unavailable" else f"{float(cost) / request_count:.6f}" if request_count else "0.000000"
    model_rows = usage.get("models") or {
        f"{usage.get('provider', 'none')}|{usage.get('model', 'none')}": {
            "provider": usage.get("provider", "none"),
            "model": usage.get("model", "none"),
            "calls": int(usage.get("calls", 0)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }
    }
    rows = []
    for row in model_rows.values():
        row_total = int(row.get("total_tokens", int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))))
        row_cost = row.get("cost_usd") if cost_status != "unavailable" else None
        rows.append(
            f"| {row.get('provider', 'none')} | {row.get('model', 'none')} | {int(row.get('calls', 0))} | "
            f"{int(row.get('input_tokens', 0))} | {int(row.get('output_tokens', 0))} | {row_total} | {_cost_text(row_cost)} |"
        )
    rows.append(
        f"| Overall | all models | {int(usage.get('calls', 0))} | {input_tokens} | {output_tokens} | {total_tokens} | {cost_value} |"
    )
    rows_text = "\n".join(rows)
    text = f"""# LLM Usage Report

Provider: {usage.get('provider', 'none')}
Model: {usage.get('model', 'none')}
Model calls: {int(usage.get('calls', 0))}
Successful calls: {int(usage.get('successful', 0))}
Failed calls: {int(usage.get('failed', usage.get('failed_calls', 0)))}
Timeouts: {int(usage.get('timeouts', 0))}
Rejected responses: {int(usage.get('rejected', 0))}
Cache hits: {int(usage.get('cache_hits', 0))}
Fallback explanations: {int(usage.get('fallbacks', 0))}
Input tokens: {input_tokens}
Output tokens: {output_tokens}
Total tokens: {total_tokens}
Average tokens per request: {average:g}
Cost status: {cost_status}
Estimated total cost (USD): {cost_value}
Estimated cost per request (USD): {per_request_cost}
Unpriced cost estimate (USD): 0.000000 (not a paid estimate when pricing is unavailable)

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Cost (USD) |
|---|---|---:|---:|---:|---:|---:|
{rows_text}

No API keys or credentials are included. Token counts are zero when no provider call is configured or provider usage is missing; unavailable pricing is never represented as a paid zero estimate.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
