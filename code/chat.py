"""The conversation: a model chooses tools, the kernel answers.

``ask`` is not a rule router.  It hands the model a small set of tools over the
person's account and lets the model choose which one to run.  Every number a tool
returns comes from :mod:`code.service`, which takes it from
:func:`code.solver.position` or :func:`code.solver.solve_request` — so a sentence
the model writes can cite a kernel figure.  A reply is shown only when every
figure in it appears in this turn's tool results; with no result to check
against, the answer is a plain refusal rather than a plausible estimate.

When no provider is configured the function says so.  It does not fall back to a
rule table and present the result as a conversation: an honest "I have no model"
is worth more than a confident FAQ bot, and it is the difference this module was
written to remove.

The loop is bounded by ``max_tool_calls`` **dispatched calls** and by the
provider's own call budget (``BUY_WAIT_LLM_MAX_CALLS``), so a model that keeps
asking cannot run away with a session.  A batch that asks for more calls than the
remainder is trimmed: the overflow gets a tool error, never a kernel call.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

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


#: The answer shown when no kernel result exists in this turn to check against.
UNVERIFIED_REPLY = "I could not verify that against your ledger."

#: Shown when the run used or exceeded its dispatched-call budget.
BUDGET_REPLY = (
    "I stopped after the tool budget ran out. The figures above are the "
    "kernel's; nothing was estimated."
)

#: Every run of digits in a reply: one figure, however it is grouped.
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _figures(text) -> list[tuple[str, Decimal]]:
    """The digit runs in ``text``, each with the number it writes."""
    found: list[tuple[str, Decimal]] = []
    for match in _FIGURE.finditer(str(text or "")):
        token = match.group(0)
        try:
            found.append((token, Decimal(token.replace(",", ""))))
        except (InvalidOperation, ValueError):
            continue
    return found


def _tool_error(result) -> bool:
    """True for the fail-closed dict ``dispatch`` puts in place of a result."""
    return isinstance(result, dict) and set(result) == {"error"}


def _kernel_observations(observations) -> list[dict]:
    """Only successful kernel results: an error message is not a figure source."""
    return [
        item
        for item in (observations or [])
        if isinstance(item, dict) and not _tool_error(item)
    ]


def _observed_figures(observations) -> set[Decimal]:
    """Every number the serialized tool results reproduce."""
    figures: set[Decimal] = set()
    for observation in observations:
        serialized = json.dumps(observation, default=str, sort_keys=True)
        figures.update(value for _token, value in _figures(serialized))
    return figures


def _render_value(value) -> str | None:
    """One observation field as words; ``None`` when it carries nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return "none recorded"
        if all(isinstance(item, (str, int, float, Decimal)) for item in value):
            return ", ".join(str(item) for item in value)
        return f"{len(value)} recorded"
    if isinstance(value, dict):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)


def _summary(observations) -> str:
    """A deterministic sentence rendered only from the tool results themselves."""
    parts: list[str] = []
    for observation in observations:
        fields = []
        for key, value in observation.items():
            rendered = _render_value(value)
            if rendered is None:
                continue
            fields.append(f"{key.replace('_', ' ')} {rendered}")
        if fields:
            parts.append("; ".join(fields))
    if not parts:
        return "The ledger returned no figure I can quote."
    return "From your ledger: " + " | ".join(parts)


def verify_reply(text: str, observations: list[dict]) -> tuple[str, bool]:
    """Check a model reply against the tool results produced in the same turn.

    Returns ``(reply, verified)``.  No kernel observation means nothing can be
    checked, so the reply is a plain refusal.  Otherwise every digit run in the
    prose has to be reproduced by the serialized observations; a reply that
    cites anything else is replaced by :func:`_summary` of what the tools
    actually returned.  The replacement is built from those results, so its
    figures are grounded by construction.
    """
    checked = _kernel_observations(observations)
    prose = str(text or "").strip()
    if not checked:
        return UNVERIFIED_REPLY, False
    if not prose:
        return _summary(checked), False
    supported = _observed_figures(checked)
    if any(value not in supported for _token, value in _figures(prose)):
        return _summary(checked), False
    return prose, True


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


def _tool_message(call_id, result: dict) -> dict:
    """One protocol-correct tool reply, whatever the tool said."""
    return {
        "role": "tool",
        "tool_call_id": str(call_id),
        "content": json.dumps(result, default=str)[:4000],
    }


def _budget_error(limit: int) -> dict:
    """The fail-closed reply for a call the run had no budget left to dispatch."""
    return {
        "error": (
            f"tool call budget exhausted: this run dispatches at most {limit} "
            "tool call(s), and this call was not executed"
        )
    }


def ask(account, text: str, provider=None, *, max_tool_calls: int = 4) -> dict:
    """Answer a question with tools, or say plainly that there is no model.

    Returns ``{"reply", "tool_calls", "model", "reason", "verified"}``.
    ``tool_calls`` is the ordered list of names that actually ran, so a caller can
    show them.  ``verified`` is true only when the text the model wrote is
    grounded in a tool result from this turn; an answer with no kernel result is
    a refusal, and a figure no tool returned replaces the prose with a summary
    of the results.

    ``max_tool_calls`` caps the number of *dispatched* calls, not the number of
    model turns.  A batch that asks for more than the remainder is trimmed: each
    overflow call still gets a tool error, so the conversation stays
    protocol-correct, and the run ends with ``reason="tool_budget"``.
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

    binder = getattr(model, "bind_tools", None)
    if not callable(binder):
        raise NoModel(
            "the configured model cannot bind tools, so an answer from it could "
            "not be checked against the ledger"
        )
    try:
        bound = binder(TOOL_SCHEMAS)
    except Exception as exc:
        raise NoModel(
            "the configured model cannot bind tools, so an answer from it could "
            f"not be checked against the ledger ({type(exc).__name__}: {exc})"
        ) from None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(facts=_facts_text(account))},
        {"role": "user", "content": str(text)},
    ]
    ran: list[str] = []
    observations: list[dict] = []
    budget = max(0, int(max_tool_calls))
    remaining = budget

    # One turn beyond the call budget: a model that has spent its last call may
    # still answer from the results it already has.
    for _ in range(budget + 1):
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
                "verified": False,
            }
        calls = list(getattr(message, "tool_calls", None) or [])
        content = getattr(message, "content", "")
        if not calls:
            reply, verified = verify_reply(str(content or ""), observations)
            return {
                "reply": reply,
                "tool_calls": ran,
                "model": getattr(provider, "name", "provider"),
                "reason": "answered" if verified else "unverified",
                "verified": verified,
            }
        messages.append(_assistant_message(content, calls))
        overflowed = False
        for call in calls:
            entry = call or {}
            name = str(entry.get("name") or "")
            call_id = str(entry.get("id") or name or "call")
            # The budget is consumed per dispatched call, before dispatch.  An
            # overflow call never reaches the kernel; it still needs a reply so
            # the next turn, if any, is well formed.
            if remaining <= 0:
                overflowed = True
                messages.append(_tool_message(call_id, _budget_error(budget)))
                continue
            remaining -= 1
            args = entry.get("args") or {}
            try:
                result = dispatch(account, name, args)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            ran.append(name)
            observations.append(result)
            messages.append(_tool_message(call_id, result))
        if overflowed:
            return {
                "reply": BUDGET_REPLY,
                "tool_calls": ran,
                "model": getattr(provider, "name", "provider"),
                "reason": "tool_budget",
                "verified": False,
            }
        if provider.remaining_calls <= 0:
            break
        provider.reserve_call()

    return {
        "reply": BUDGET_REPLY,
        "tool_calls": ran,
        "model": getattr(provider, "name", "provider"),
        "reason": "budget",
        "verified": False,
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
