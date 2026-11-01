"""The conversation: a model chooses tools, the kernel answers.

``ask`` is not a rule router.  It hands the model a small set of tools over the
person's account and lets the model choose which one to run.  Every number a tool
returns comes from :mod:`code.service`, which takes it from
:func:`code.solver.position` or :func:`code.solver.solve_request` — so a sentence
the model writes can cite a kernel figure, and it cannot invent one.

When no provider is configured the function says so.  It does not fall back to a
rule table and present the result as a conversation: an honest "I have no model"
is worth more than a confident FAQ bot, and it is the difference this module was
written to remove.

The loop is bounded by ``max_tool_calls`` and by the provider's own call budget
(``BUY_WAIT_LLM_MAX_CALLS``), so a model that keeps asking cannot run away with a
session.
"""
from __future__ import annotations

import json

from code import service

#: The tools the model may choose.  Each returns kernel values only.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "position",
            "description": (
                "The kernel's position for this person today: balance, floor, "
                "committed outflows and the largest safe one-off payment."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "Every recorded money movement, in date order.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_requests",
            "description": "Every purchase already asked about, with its decision.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decide",
            "description": (
                "Run the kernel for one recorded request and return its decision. "
                "Use the request_id from list_requests."
            ),
            "parameters": {
                "type": "object",
                "properties": {"request_id": {"type": "string"}},
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "what_if",
            "description": (
                "Decide a hypothetical purchase without recording it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "on": {"type": "string", "description": "ISO date, default today"},
                    "by": {"type": "string", "description": "optional ISO deadline"},
                },
                "required": ["amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "facts",
            "description": "What is missing from this person's account, in plain words.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_NAMES = tuple(item["function"]["name"] for item in TOOL_SCHEMAS)

SYSTEM_PROMPT = (
    "You are the assistant inside Buy or Wait?, a financial decision tool that "
    "runs on the person's own recorded facts. You do not compute money. Every "
    "figure in your answer must come from a tool result; if you have no figure, "
    "say what you would need instead of estimating. The kernel is conservative: "
    "it reserves scheduled outflows, ignores unsettled income, and never lets the "
    "balance fall below the person's floor. Be short, concrete and kind.\n\n"
    "Account facts:\n{facts}"
)


class NoModel(RuntimeError):
    """No provider is configured, so there is nothing that can reason."""


def _facts_text(account) -> str:
    profile = account.get("profile") or {}
    lines = [
        f"- name: {profile.get('user_id') or 'unknown'}",
        f"- currency: {profile.get('home_currency') or 'unset'}",
        f"- balance: {profile.get('current_available_balance') or 'unset'}",
        f"- floor: {profile.get('minimum_balance_to_keep') or 'unset'}",
        f"- recorded movements: {len(account.get('events') or [])}",
        f"- purchases asked about: {len(account.get('requests') or [])}",
    ]
    problems = service.acct.problems(account)
    if problems:
        lines.append("- missing: " + "; ".join(problems))
    return "\n".join(lines)


def dispatch(account, name: str, args: dict | None = None) -> dict:
    """Run one tool.  Raises ``KeyError`` for a tool that does not exist."""
    args = args or {}
    if name == "position":
        return service.position(account)
    if name == "list_events":
        return {"events": [dict(row) for row in (account.get("events") or [])]}
    if name == "list_requests":
        return {"requests": [
            {
                "request_id": row.get("request_id"),
                "request_date": row.get("request_date"),
                "requested_amount": row.get("requested_amount"),
            }
            for row in (account.get("requests") or [])
        ]}
    if name == "decide":
        request_id = str(args.get("request_id") or "").strip()
        if not request_id:
            raise ValueError("decide needs a request_id")
        decision = service.decide(account, request_id)
        return {
            key: decision.get(key)
            for key in (
                "request_id", "amount_safe_to_pay", "affordability_status",
                "recommended_payment_method", "payment_plan",
                "earliest_date_for_full_payment", "spending_changes_needed",
                "decision_explanation",
            )
        }
    if name == "what_if":
        amount = args.get("amount")
        if amount in (None, ""):
            raise ValueError("what_if needs an amount")
        return _decide_hypothetically(account, amount, args.get("on"), args.get("by"))
    if name == "facts":
        return {"problems": service.acct.problems(account)}
    raise KeyError(f"no tool {name!r}; available: {', '.join(TOOL_NAMES)}")


def _decide_hypothetically(account, amount, on=None, by=None) -> dict:
    """A decision on a copy: nothing is recorded, nothing is saved."""
    import copy

    trial = copy.deepcopy(account)
    decision = service.buy(trial, amount, on=on, by=by, text="what-if (not recorded)")
    return {
        key: decision.get(key)
        for key in (
            "amount_safe_to_pay", "affordability_status",
            "recommended_payment_method", "payment_plan",
            "earliest_date_for_full_payment", "spending_changes_needed",
            "decision_explanation",
        )
    }


def _provider(provider=None):
    from code.providers import NullProvider, build_provider

    if provider is None:
        provider = build_provider()
    if provider is None or isinstance(provider, NullProvider):
        return None
    return provider


def ask(account, text: str, provider=None, *, max_tool_calls: int = 4) -> dict:
    """Answer a question with tools, or say plainly that there is no model.

    Returns ``{"reply", "tool_calls", "model", "reason"}``.  ``tool_calls`` is
    the ordered list of names that actually ran, so a caller can show them.
    """
    provider = _provider(provider)
    if provider is None:
        raise NoModel(
            "no model is configured, so nothing here can reason about the ledger; "
            "the tabs run the kernel directly"
        )

    try:
        model = provider.chat_model()
    except Exception as exc:
        raise NoModel(f"the configured provider has no usable chat model: {exc}") from None
    if not provider.reserve_call():
        raise NoModel("this run has spent its model call budget")

    try:
        bound = model.bind_tools(TOOL_SCHEMAS) if hasattr(model, "bind_tools") else model
    except Exception:
        bound = model

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(facts=_facts_text(account))},
        {"role": "user", "content": str(text)},
    ]
    ran: list[str] = []

    for _ in range(max(1, int(max_tool_calls))):
        try:
            message = bound.invoke(messages)
        except Exception as exc:
            return {
                "reply": (
                    "The model could not be reached, so I will not guess at your "
                    f"figures ({type(exc).__name__})."
                ),
                "tool_calls": ran,
                "model": getattr(provider, "name", "provider"),
                "reason": "provider_error",
            }
        calls = list(getattr(message, "tool_calls", None) or [])
        content = getattr(message, "content", "")
        if not calls:
            return {
                "reply": str(content or "").strip(),
                "tool_calls": ran,
                "model": getattr(provider, "name", "provider"),
                "reason": "answered",
            }
        messages.append(_assistant_message(content, calls))
        for call in calls:
            name = str((call or {}).get("name") or "")
            args = (call or {}).get("args") or {}
            try:
                result = dispatch(account, name, args)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            ran.append(name)
            messages.append({
                "role": "tool",
                "tool_call_id": str((call or {}).get("id") or name),
                "content": json.dumps(result, default=str)[:4000],
            })
        if provider.remaining_calls <= 0:
            break
        provider.reserve_call()

    return {
        "reply": (
            "I stopped after the tool budget ran out. The figures above are the "
            "kernel's; nothing was estimated."
        ),
        "tool_calls": ran,
        "model": getattr(provider, "name", "provider"),
        "reason": "budget",
    }


def _assistant_message(content, calls) -> dict:
    return {
        "role": "assistant",
        "content": str(content or ""),
        "tool_calls": [
            {
                "id": str((call or {}).get("id") or (call or {}).get("name") or "call"),
                "type": "function",
                "function": {
                    "name": str((call or {}).get("name") or ""),
                    "arguments": json.dumps((call or {}).get("args") or {}, default=str),
                },
            }
            for call in calls
        ],
    }
