"""Compare the deterministic kernel with the agentic loop on the same requests.

The product has two decision paths:

* **non-agentic** — ``solve_request`` alone: deterministic forecast, plan search
  and ranking, no model.
* **agentic** — the bounded LangChain loop reads evidence, projects cash and
  proposes a decision, which the deterministic verifier then accepts or rejects.

Because the verifier owns every machine field, the agent should never change
``amount_safe_to_pay`` or the status.  This tool proves that on a real slice and
measures what the agent actually adds: a written rationale, tool usage, model
calls and latency.

    python -m tools.experiments.compare_agentic --limit 5 --provider ollama \\
        --model qwen3:4b-instruct --base-url http://127.0.0.1:11435

Manual tool: it can talk to a local model, it never writes ``output.csv`` and it
never changes a decision.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MACHINE_COLUMNS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def _configure_provider(args) -> None:
    """Point the provider factory at the requested backend, without a key file."""
    if args.provider == "ollama" and args.base_url:
        os.environ["BUY_WAIT_OLLAMA_BASE_URL"] = args.base_url
    if args.model:
        env_name = {
            "ollama": "BUY_WAIT_OLLAMA_MODEL",
            "kilo": "BUY_WAIT_KILO_MODEL",
            "openrouter": "BUY_WAIT_LLM_MODEL",
        }.get(args.provider)
        if env_name:
            os.environ[env_name] = args.model
    os.environ["BUY_WAIT_LLM_PROVIDER"] = args.provider


def _trace_usage(trace_path) -> dict:
    if not trace_path:
        return {}
    try:
        payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return usage if isinstance(usage, dict) else {}


def _run(limit, provider_name, trace_dir, timeout_s, max_turns=None, max_tool_calls=None, offset=0):
    """Run the pipeline twice: plain kernel, then the agentic slice."""
    from code.main import run_pipeline
    from code.providers import build_provider

    baseline, _ = run_pipeline(write_output=False, write_usage=False)

    provider, reason = (None, None) if provider_name == "none" else build_provider()
    recorded: dict = {}
    # Only override the product budget when the caller asks for it, so the
    # comparison keeps the shipped limits by default.
    budget = {}
    if max_turns is not None:
        budget["max_provider_calls"] = max_turns
    if max_tool_calls is not None:
        budget["max_tool_calls"] = max_tool_calls

    def instrumented(context, *, provider, tools, trace):
        from code.agent import run_agent_loop

        started = time.monotonic()
        outcome = run_agent_loop(
            context, provider=provider, tools=tools, trace=trace, timeout_s=timeout_s, **budget
        )
        recorded[outcome.request_id] = {
            "seconds": round(time.monotonic() - started, 3),
            "provider_calls": outcome.provider_calls,
            "tool_calls": outcome.tool_calls,
            "fallback_reason": outcome.fallback_reason,
            "retrieval_backend": outcome.retrieval_backend,
            "verification": dict(outcome.verification or {}),
            "trace_path": outcome.trace_path,
            "trace_error": outcome.trace_error,
            "model": outcome.model,
            "provider": outcome.provider,
        }
        return outcome

    agentic, run_usage = run_pipeline(
        write_output=False,
        write_usage=False,
        provider=provider,
        trace_dir=trace_dir,
        # The loop always starts from the first request in dataset order, so an
        # offset runs the longer slice and the summariser drops its head.
        agent_limit=offset + limit,
        agent=instrumented,
    )
    return baseline, agentic, recorded, provider, reason, run_usage


def _summarise(baseline, agentic, recorded, *, limit, offset=0, loop_errors=None):
    crash_by_id = {}
    for item in loop_errors or []:
        if isinstance(item, dict) and item.get("request_id"):
            crash_by_id[str(item["request_id"])] = (
                str(item.get("error") or "unknown error")
                + (f" [{item.get('where')}]" if item.get("where") else "")
            )
    rows = []
    for request_id in list(agentic["request_id"])[offset:offset + limit]:
        base = baseline[baseline["request_id"] == request_id].iloc[0]
        agent = agentic[agentic["request_id"] == request_id].iloc[0]
        detail = recorded.get(request_id, {})
        differing = [column for column in MACHINE_COLUMNS if str(base[column]) != str(agent[column])]
        verification = detail.get("verification") or {}
        usage = _trace_usage(detail.get("trace_path"))
        rows.append(
            {
                "request_id": request_id,
                "machine_diffs": differing,
                "explanation_changed": str(base["decision_explanation"]) != str(agent["decision_explanation"]),
                "rationale_source": verification.get("rationale_source"),
                "fallback_reason": detail.get("fallback_reason"),
                "provider_calls": detail.get("provider_calls", 0),
                "tool_calls": detail.get("tool_calls", 0),
                "retrieval_backend": detail.get("retrieval_backend"),
                "seconds": detail.get("seconds"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "trace_error": detail.get("trace_error"),
                "loop_error": crash_by_id.get(request_id),
            }
        )
    return rows


def _markdown(rows, *, provider_label, provider_reason, limit, offset=0) -> str:
    total_provider = sum(row["provider_calls"] or 0 for row in rows)
    total_tools = sum(row["tool_calls"] or 0 for row in rows)
    fallbacks = [row for row in rows if row["fallback_reason"]]
    machine_equal = [row for row in rows if not row["machine_diffs"]]
    rewritten = [row for row in rows if row["explanation_changed"]]
    seconds = [row["seconds"] for row in rows if isinstance(row["seconds"], (int, float))]

    lines = [
        "# Agentic vs deterministic decisions",
        "",
        f"Provider: `{provider_label}`"
        + (f" (unavailable: {provider_reason})" if provider_reason else ""),
        f"Requests compared: **{len(rows)}** ("
        + (f"requests {offset + 1}-{offset + limit} in dataset order" if offset else f"first {limit} in dataset order")
        + ")",
        "",
        "## Guardrail",
        "",
        f"- Machine fields identical: **{len(machine_equal)}/{len(rows)}**",
        f"- Verifier rewrote the explanation from the model's rationale: **{len(rewritten)}/{len(rows)}**",
        f"- Agent fallbacks: **{len(fallbacks)}/{len(rows)}**",
        "",
        "## Cost",
        "",
        f"- Model calls: **{total_provider}** ({total_provider / len(rows):.1f} per request)"
        if rows
        else "- Model calls: 0",
        f"- Tool calls: **{total_tools}** ({total_tools / len(rows):.1f} per request)"
        if rows
        else "- Tool calls: 0",
        f"- Wall clock per request: mean **{sum(seconds) / len(seconds):.2f}s**, max **{max(seconds):.2f}s**"
        if seconds
        else "- Wall clock: not measured",
        "",
        "## Per request",
        "",
        "| request | machine fields | explanation | rationale | fallback | model calls | tool calls | seconds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        machine = "identical" if not row["machine_diffs"] else "CHANGED: " + ", ".join(row["machine_diffs"])
        lines.append(
            "| {request_id} | {machine} | {explanation} | {rationale} | {fallback} | {pc} | {tc} | {sec} |".format(
                request_id=row["request_id"],
                machine=machine,
                explanation="rewritten" if row["explanation_changed"] else "kernel text",
                rationale=row["rationale_source"] or "—",
                fallback=row["fallback_reason"] or "—",
                pc=row["provider_calls"] if row["provider_calls"] is not None else "—",
                tc=row["tool_calls"] if row["tool_calls"] is not None else "—",
                sec=row["seconds"] if row["seconds"] is not None else "—",
            )
        )
    if fallbacks:
        lines += ["", "## Fallback reasons", ""]
        for row in fallbacks:
            lines.append(f"- `{row['request_id']}`: {row['fallback_reason']}")
    crashed = [row for row in rows if row.get("loop_error")]
    if crashed:
        # A crashed invocation ran to the kernel without any provider call and
        # without a normal fallback reason; hiding it would look like a skipped
        # request, not a failure.
        lines += ["", "## Crashed agent invocations", ""]
        for row in crashed:
            lines.append(f"- `{row['request_id']}`: {row['loop_error']}")
    lines += [
        "",
        "## Reading this",
        "",
        "The verifier is the authority: it recomputes the kernel row and keeps its own",
        "machine fields, so a differing amount or status here would be a bug, not a",
        "better answer. The agent's real contribution is the explanation, the evidence",
        "it pulled in, and the trace a human can audit afterwards.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=5, help="requests to run through the agent (default 5)")
    parser.add_argument("--offset", type=int, default=0, help="skip the first N requests so heavy runs can be chunked with cooldowns")
    parser.add_argument("--provider", default="ollama", choices=("ollama", "replay", "kilo", "openrouter", "none"))
    parser.add_argument("--model", default=None, help="model id for the chosen provider")
    parser.add_argument("--base-url", default=None, help="local Ollama base URL, e.g. http://127.0.0.1:11435")
    parser.add_argument("--trace-dir", default="traces/agentic-compare")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="per model call timeout in seconds (default 120; the pipeline default is 30)",
    )
    parser.add_argument("--out", default="docs/agentic-vs-deterministic.md")
    parser.add_argument("--json", default=None, help="also write the raw comparison as JSON")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="override the agent's model-call budget (default: the shipped limit of 6)",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help="override the agent's tool-call budget (default: the shipped limit of 6)",
    )
    args = parser.parse_args(argv)

    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.offset < 0:
        parser.error("--offset cannot be negative")
    _configure_provider(args)

    baseline, agentic, recorded, provider, reason, run_usage = _run(
        args.limit,
        args.provider,
        args.trace_dir,
        args.timeout,
        max_turns=args.max_turns,
        max_tool_calls=args.max_tool_calls,
        offset=args.offset,
    )
    rows = _summarise(
        baseline,
        agentic,
        recorded,
        limit=args.limit,
        offset=args.offset,
        loop_errors=(run_usage or {}).get("agent_loop_errors"),
    )
    label = f"{getattr(provider, 'name', args.provider)}:{getattr(provider, 'model', args.model or '')}"
    report = _markdown(rows, provider_label=label, provider_reason=reason, limit=args.limit, offset=args.offset)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8", newline="\n")
    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
