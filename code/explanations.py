from __future__ import annotations

import json
import os
import re
import socket
from urllib.request import Request, urlopen

from .usage import estimate_cost_usd, MAX_PROVIDER_CALLS

_REQUIRED_KEYS = ("decision_explanation", "evidence_sufficient", "supporting_facts")
_OPTIONAL_KEYS = ("missing_evidence",)


def _schema_ok(parsed: dict) -> bool:
    if not isinstance(parsed, dict):
        return False
    if any(k not in parsed for k in _REQUIRED_KEYS):
        return False
    if any(k not in _REQUIRED_KEYS + _OPTIONAL_KEYS for k in parsed):
        return False
    if not isinstance(parsed["evidence_sufficient"], bool):
        return False
    if not isinstance(parsed["supporting_facts"], list):
        return False
    return isinstance(parsed["decision_explanation"], str)


def _fallback(packet: dict) -> str:
    warnings = packet.get("evidence_warnings") or []
    if warnings:
        ids = ", ".join(str(w.get("event_id")) for w in warnings)
        return f"Not recommended because financial evidence for {ids} remains unresolved, so safety cannot be verified while preserving the minimum balance."
    method = packet.get("recommended_payment_method", "not_recommended")
    safe = packet.get("amount_safe_to_pay", "0")
    minimum = packet.get("minimum_balance", "0")
    return f"Recommended {method}; up to {safe} is safe under the forecast while preserving the minimum balance of {minimum}."


def _configured_call(packet: dict) -> dict:
    endpoint = os.environ.get("BUY_WAIT_LLM_ENDPOINT", "").strip()
    api_key = os.environ.get("BUY_WAIT_LLM_API_KEY", "").strip() or os.environ.get("OPENROUTER_KEY", "").strip()
    model = os.environ.get("BUY_WAIT_LLM_MODEL", "").strip()
    if api_key and not endpoint:
        endpoint = "https:" + "//openrouter.ai/api/v1/chat/completions"
    if api_key and not model:
        model = "Ling 3.0 Flash Fin"
    if model.strip().lower() == "ling 3.0 flash fin":
        model = "inclusionai/ling-3.0-flash-fin"
    if not endpoint or not api_key or not model:
        return None
    body = {
        "model": model,
        "temperature": 0,
        # Reasoning models spend max_tokens on their chain of thought before any
        # content is emitted, so leave enough headroom for the JSON answer.
        "max_tokens": 1000,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Respond with one JSON object and no other text. Use exactly these keys: "
                    '"decision_explanation" (string), "evidence_sufficient" (boolean), '
                    '"supporting_facts" (array of strings), and optionally "missing_evidence" '
                    "(array of strings). Use no other keys. When missing_evidence is non-empty, "
                    "evidence_sufficient must be false. Write one concise explanation using only "
                    "the supplied verified decision facts. Never invent facts or change the decision."
                ),
            },
            {"role": "user", "content": json.dumps(packet, sort_keys=True)},
        ],
        "response_format": {"type": "json_object"},
    }
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        timeout = float(os.environ.get("BUY_WAIT_LLM_TIMEOUT_SECONDS", "12"))
    except ValueError:
        timeout = 5.0
    timeout = max(1.0, min(timeout, 15.0))
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    message = payload.get("choices", [{}])[0].get("message", {}) if isinstance(payload.get("choices", [{}])[0], dict) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        # Reasoning models can answer entirely inside the reasoning channel and
        # leave content empty; use it rather than discarding a valid answer.
        reasoning = message.get("reasoning")
        content = reasoning if isinstance(reasoning, str) and reasoning.strip() else None
    if content is not None and not isinstance(content, str):
        content = None
    provider = os.environ.get("BUY_WAIT_LLM_PROVIDER", "openrouter").strip() or "openrouter"
    provider_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    input_tokens = int(provider_usage.get("prompt_tokens", 0)) if provider_usage else 0
    output_tokens = int(provider_usage.get("completion_tokens", 0)) if provider_usage else 0
    total_tokens = int(provider_usage.get("total_tokens", input_tokens + output_tokens)) if provider_usage else 0
    cost_usd, cost_status = estimate_cost_usd(provider, model, input_tokens, output_tokens)
    return {
        "content": content,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_present": provider_usage is not None,
        "cost_usd": cost_usd,
        "cost_status": cost_status,
    }


def _grounded(explanation: str, packet: dict, supporting_facts=None) -> bool:
    text = explanation.strip().lower()
    if not text or len(text) > 400:
        return False
    numbers = {token.replace(",", "") for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    for cited in supporting_facts or ():
        numbers.update(token.replace(",", "") for token in re.findall(r"\d[\d,]*(?:\.\d+)?", str(cited)))
    for fact in (packet.get("amount_safe_to_pay"),):
        if fact is not None and str(fact).strip():
            normalized = str(fact).replace(",", "").strip()
            if normalized not in numbers:
                return False
    requested = str(packet.get("requested_amount", "")).replace(",", "").strip()
    safe = str(packet.get("amount_safe_to_pay", "")).replace(",", "").strip()
    if requested and safe and requested != safe and requested in numbers and re.search(r"\b(now|today)\b", text) and re.search(r"\b(pay|buy|purchase)\b", text):
        return False
    warnings = packet.get("evidence_warnings") or []
    if warnings and not any(word in text for word in ("unresolved", "cannot", "can't", "unsafe", "not recommended", "not affordable")):
        return False
    method = str(packet.get("recommended_payment_method", "")).replace("_", " ").lower()
    if method and method not in text:
        return False
    return True


def _extract_json(text: str) -> str:
    """Recover the JSON object from an answer wrapped in prose or code fences."""
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except (ValueError, TypeError):
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fenced:
        return fenced.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start:end + 1]
    return stripped


def generate_explanation(packet: dict, call=None) -> str:
    """Generate grounded prose; the LLM never controls the financial decision."""
    raw = None
    usage = packet.get("_usage")
    if usage is not None:
        configured_max = max(0, min(int(usage.get("max_calls", MAX_PROVIDER_CALLS)), MAX_PROVIDER_CALLS))
        usage["max_calls"] = configured_max
        if int(usage.get("calls", 0)) >= configured_max:
            usage["rejected"] = int(usage.get("rejected", 0)) + 1
            usage["fallbacks"] = int(usage.get("fallbacks", 0)) + 1
            return _fallback(packet)

    provider_configured = (
        (os.environ.get("BUY_WAIT_LLM_ENDPOINT", "").strip() or os.environ.get("OPENROUTER_KEY", "").strip())
        and (os.environ.get("BUY_WAIT_LLM_API_KEY", "").strip() or os.environ.get("OPENROUTER_KEY", "").strip())
    )
    if call is not None or provider_configured:
        if usage is not None:
            usage["calls"] = int(usage.get("calls", 0)) + 1
        try:
            raw = call(packet) if call is not None else _configured_call(packet)
        except (TimeoutError, socket.timeout):
            if usage is not None:
                usage["failed"] = int(usage.get("failed", 0)) + 1
                usage["failed_calls"] = int(usage.get("failed_calls", 0)) + 1
                usage["timeouts"] = int(usage.get("timeouts", 0)) + 1
                usage["fallbacks"] = int(usage.get("fallbacks", 0)) + 1
            return _fallback(packet)
        except Exception:
            if usage is not None:
                usage["failed"] = int(usage.get("failed", 0)) + 1
                usage["failed_calls"] = int(usage.get("failed_calls", 0)) + 1
                usage["fallbacks"] = int(usage.get("fallbacks", 0)) + 1
            return _fallback(packet)

    metadata = raw if isinstance(raw, dict) else {}
    content = raw.get("content") if isinstance(raw, dict) else raw
    if usage is not None and (call is not None or provider_configured):
        provider = str(metadata.get("provider", usage.get("provider", "none")))
        model = str(metadata.get("model", usage.get("model", "none")))
        input_tokens = max(0, int(metadata.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(metadata.get("output_tokens", 0) or 0))
        total_tokens = max(0, int(metadata.get("total_tokens", input_tokens + output_tokens) or 0))
        usage["input_tokens"] = int(usage.get("input_tokens", 0)) + input_tokens
        usage["output_tokens"] = int(usage.get("output_tokens", 0)) + output_tokens
        usage["total_tokens"] = int(usage.get("total_tokens", 0)) + total_tokens
        if metadata.get("usage_present") is not True:
            usage["missing_usage"] = int(usage.get("missing_usage", 0)) + 1
        cost = metadata.get("cost_usd")
        if cost is None:
            usage["cost_status"] = "unavailable"
        else:
            usage["cost_status"] = "estimated"
            usage["estimated_cost_usd"] = float(usage.get("estimated_cost_usd", 0.0)) + float(cost)
        bucket_key = f"{provider}|{model}"
        bucket = usage.setdefault("models", {}).setdefault(bucket_key, {
            "provider": provider,
            "model": model,
            "calls": 0,
            "successful": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        })
        bucket["calls"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        if cost is None:
            bucket["cost_usd"] = None
        elif bucket.get("cost_usd") is not None:
            bucket["cost_usd"] += float(cost)
        bucket_key_for_success = bucket_key
    try:
        if content:
            structured = isinstance(raw, dict)
            parsed = json.loads(_extract_json(content)) if isinstance(content, str) else content
            explanation = None
            supporting_facts = []
            if isinstance(parsed, dict):
                if _schema_ok(parsed):
                    explanation = parsed["decision_explanation"]
                    supporting_facts = parsed["supporting_facts"]
                elif structured:
                    if set(parsed) == {"decision_explanation"} and isinstance(parsed["decision_explanation"], str):
                        explanation = parsed["decision_explanation"]
                elif len(parsed) == 1:
                    only_value = next(iter(parsed.values()))
                    if isinstance(only_value, str):
                        explanation = only_value
            if isinstance(explanation, str) and _grounded(explanation, packet, supporting_facts):
                if usage is not None:
                    usage["successful"] += 1
                    if isinstance(usage.get("models"), dict) and "bucket_key_for_success" in locals():
                        usage["models"][bucket_key_for_success]["successful"] += 1
                return explanation.strip()
    except Exception:
        pass
    if usage is not None:
        usage["rejected"] = int(usage.get("rejected", 0)) + 1
        usage["fallbacks"] += 1
    return _fallback(packet)
