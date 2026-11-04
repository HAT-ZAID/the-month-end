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

A tool call is untrusted input.  Every call is checked against the published
schema before it runs: an unknown tool, an undeclared field, a repeated call id
or a non-object argument payload gets a structured ``{code, retryable,
safe_summary}`` envelope and never reaches the kernel.
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
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "Every recorded money movement, in date order.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_requests",
            "description": "Every purchase already asked about, with its decision.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
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
                "additionalProperties": False,
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
                    "amount": {
                        "type": "string",
                        "description": (
                            "Positive decimal amount as a JSON string, for "
                            'example "500.00". Never a JSON number.'
                        ),
                    },
                    "on": {"type": "string", "description": "ISO date, default today"},
                    "by": {"type": "string", "description": "optional ISO deadline"},
                },
                "required": ["amount"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "facts",
            "description": "What is missing from this person's account, in plain words.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES = tuple(item["function"]["name"] for item in TOOL_SCHEMAS)

#: Each tool's published parameters, by name: the closed set of arguments.
TOOL_PARAMETERS = {
    item["function"]["name"]: item["function"]["parameters"] for item in TOOL_SCHEMAS
}

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


#: The keys of every fail-closed tool reply: a protocol rejection or a failure.
_ERROR_KEYS = frozenset({"code", "retryable", "safe_summary"})


def _protocol_error(code: str, safe_summary: str, *, retryable: bool = True) -> dict:
    """One structured, fail-closed tool reply the model can read and correct.

    ``safe_summary`` is a fixed sentence built from the published schema; it
    never repeats an untrusted argument value.
    """
    return {
        "code": str(code),
        "retryable": bool(retryable),
        "safe_summary": str(safe_summary),
    }


def _tool_error(result) -> bool:
    """True for a fail-closed dict rather than a kernel result.

    Protocol rejections, budget refusals and tool failures share one three-key
    envelope, so none of them can become a figure source for verification.  The
    legacy single-key ``{"error": ...}`` shape is still recognised.
    """
    if not isinstance(result, dict):
        return False
    return set(result) == _ERROR_KEYS or set(result) == {"error"}


def _accepts_text(name: str) -> str:
    """The arguments a tool declares, for a rejection the model can act on."""
    accepted = sorted((TOOL_PARAMETERS.get(name) or {}).get("properties") or ())
    if accepted:
        return "only these arguments: " + ", ".join(accepted)
    return "no arguments"


def _amount_problem(value: str) -> bool:
    """True when a string is not a finite, positive decimal."""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return True
    return not amount.is_finite() or amount <= 0


def validate_tool_call(name, args) -> list[str]:
    """The rejection codes for one model tool call; empty means it may run.

    The codes are stable: ``UNKNOWN_TOOL``, ``BAD_ARGS`` and ``EXTRA_FIELD``.
    ``DUPLICATE_ID`` depends on the other calls in the same batch, so
    :func:`ask` raises it.  Only the published schema is consulted; no user
    data and no kernel state take part in the decision.
    """
    tool = str(name or "")
    schema = TOOL_PARAMETERS.get(tool)
    if schema is None:
        return ["UNKNOWN_TOOL"]
    if not isinstance(args, dict):
        return ["BAD_ARGS"]
    properties = schema.get("properties") or {}
    required = tuple(schema.get("required") or ())
    problems: list[str] = []
    if any(key not in properties for key in args):
        problems.append("EXTRA_FIELD")
    if any(key not in args for key in required):
        problems.append("BAD_ARGS")
    for key, value in args.items():
        spec = properties.get(key) or {}
        if spec.get("type") == "string" and not isinstance(value, str):
            problems.append("BAD_ARGS")
    if tool == "what_if" and isinstance(args.get("amount"), str):
        if _amount_problem(args["amount"]):
            problems.append("BAD_ARGS")
    if tool == "decide" and isinstance(args.get("request_id"), str):
        if not args["request_id"].strip():
            problems.append("BAD_ARGS")
    ordered: list[str] = []
    for code in problems:
        if code not in ordered:
            ordered.append(code)
    return ordered


def _rejection_summary(name: str, args, code: str) -> str:
    """A short, safe sentence for one rejection; never repeats an untrusted value."""
    if code == "UNKNOWN_TOOL":
        return f"no such tool; available tools: {', '.join(TOOL_NAMES)}"
    if code == "EXTRA_FIELD":
        return (
            f"{name} accepts {_accepts_text(name)}; the extra argument was not "
            "accepted and the call was not executed"
        )
    if not isinstance(args, dict):
        return (
            "tool arguments must be a JSON object; this call was not executed, "
            "and a string, list or number is never read as an empty argument set"
        )
    if name == "what_if":
        amount = args.get("amount")
        if "amount" not in args:
            return (
                'what_if needs "amount" as a JSON string such as "500.00"; use '
                "a string of decimal digits, not a number"
            )
        if not isinstance(amount, str):
            return (
                'what_if "amount" must be a JSON string such as "500.00"; use a '
                "string of decimal digits, never a JSON number, and the call "
                "was not executed"
            )
        return (
            'what_if "amount" must be a finite positive decimal written as a '
            'string such as "500.00"; use a string, not a number'
        )
    if name == "decide":
        return 'decide needs "request_id" as a non-empty string from list_requests'
    return f"{name} has invalid arguments; expected {_accepts_text(name)}"


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
    """Run one tool after checking its arguments against the published schema.

    Raises ``KeyError`` for a tool that does not exist.  A non-object payload,
    an undeclared field, a missing required field or a malformed amount raises
    ``ValueError`` instead of being coerced; :func:`ask` checks the same way
    first and turns a rejection into a protocol envelope.
    """
    tool = str(name or "")
    if tool not in TOOL_PARAMETERS:
        raise KeyError(f"no tool {name!r}; available: {', '.join(TOOL_NAMES)}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be a JSON object")
    problems = validate_tool_call(tool, args)
    if problems:
        raise ValueError(_rejection_summary(tool, args, problems[0]))
    if tool == "position":
        return service.position(account)
    if tool == "list_events":
        return {"events": [dict(row) for row in (account.get("events") or [])]}
    if tool == "list_requests":
        return {"requests": [
            {
                "request_id": row.get("request_id"),
                "request_date": row.get("request_date"),
                "requested_amount": row.get("requested_amount"),
            }
            for row in (account.get("requests") or [])
        ]}
    if tool == "decide":
        request_id = args["request_id"].strip()
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
    if tool == "what_if":
        return _decide_hypothetically(
            account, args["amount"], args.get("on"), args.get("by")
        )
    if tool == "facts":
        return {"problems": service.acct.problems(account)}
    raise KeyError(f"no tool {tool!r}; available: {', '.join(TOOL_NAMES)}")


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
    return _protocol_error(
        "TOOL_BUDGET",
        (
            f"tool call budget exhausted: this run dispatches at most {limit} "
            "tool call(s), and this call was not executed"
        ),
        retryable=False,
    )


def _tool_failure(name: str, exc: BaseException) -> dict:
    """A tool that raised, in the same envelope a rejection uses.

    Only the exception's type is repeated: its message can contain untrusted
    argument text, and this reply goes back to the model.
    """
    return _protocol_error(
        "TOOL_ERROR",
        (
            f"{name} could not run ({type(exc).__name__}); no figure from this "
            "call was used"
        ),
        retryable=True,
    )


def _call_id(entry: dict, index: int, prefix: str) -> str:
    """The id a call is answered under, with a deterministic fallback."""
    return str(entry.get("id") or entry.get("name") or f"{prefix}_{index + 1}")


def _turn_calls(message) -> list[dict]:
    """The valid and invalid tool calls in one assistant turn, normalised.

    ``invalid_tool_calls`` carries calls whose arguments the provider could not
    parse.  They are kept so each one gets a protocol reply and none is silently
    lost; they are marked so the loop never dispatches them.
    """
    calls: list[dict] = []
    for index, raw in enumerate(getattr(message, "tool_calls", None) or ()):
        entry = raw if isinstance(raw, dict) else {}
        calls.append({
            "id": _call_id(entry, index, "call"),
            "name": str(entry.get("name") or ""),
            "args": entry["args"] if "args" in entry else {},
            "invalid": False,
        })
    for index, raw in enumerate(getattr(message, "invalid_tool_calls", None) or ()):
        entry = raw if isinstance(raw, dict) else {}
        calls.append({
            "id": _call_id(entry, index, "invalid_call"),
            "name": str(entry.get("name") or ""),
            "args": entry.get("args"),
            "invalid": True,
        })
    return calls


def _invalid_call_error(name: str) -> dict:
    """The reply for a call the provider itself could not parse."""
    if name in TOOL_NAMES:
        return _protocol_error(
            "BAD_ARGS",
            (
                f"the arguments for {name} could not be parsed as JSON; the call "
                "was not executed"
            ),
            retryable=True,
        )
    return _protocol_error(
        "UNKNOWN_TOOL",
        f"no such tool; available tools: {', '.join(TOOL_NAMES)}",
        retryable=True,
    )


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

    Every call is validated against the published schema before dispatch.  An
    unknown tool, an undeclared field, a repeated call id, a non-object argument
    payload or a non-string amount gets a structured ``{code, retryable,
    safe_summary}`` reply and never runs.
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
        calls = _turn_calls(message)
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
        seen_ids: set[str] = set()
        for call in calls:
            call_id = call["id"]
            if call_id in seen_ids:
                messages.append(_tool_message(
                    call_id,
                    _protocol_error(
                        "DUPLICATE_ID",
                        (
                            "this turn repeated a tool call id; each call needs a "
                            "unique id and the duplicate was not executed"
                        ),
                        retryable=True,
                    ),
                ))
                continue
            seen_ids.add(call_id)
            if call["invalid"]:
                messages.append(
                    _tool_message(call_id, _invalid_call_error(call["name"]))
                )
                continue
            problems = validate_tool_call(call["name"], call["args"])
            if problems:
                messages.append(_tool_message(
                    call_id,
                    _protocol_error(
                        problems[0],
                        _rejection_summary(
                            call["name"], call["args"], problems[0]
                        ),
                    ),
                ))
                continue
            # The budget is consumed per dispatched call, before dispatch.  An
            # overflow call never reaches the kernel; it still needs a reply so
            # the next turn, if any, is well formed.
            if remaining <= 0:
                overflowed = True
                messages.append(_tool_message(call_id, _budget_error(budget)))
                continue
            remaining -= 1
            try:
                result = dispatch(account, call["name"], call["args"])
            except Exception as exc:
                result = _tool_failure(call["name"], exc)
            ran.append(call["name"])
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


def _arguments_text(args) -> str:
    """A tool call's arguments as JSON text, keeping raw invalid JSON as sent."""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args if args is not None else {}, default=str)
    except (TypeError, ValueError):
        return "{}"


def _assistant_message(content, calls) -> dict:
    """The assistant turn replayed back to the provider, invalid calls included."""
    return {
        "role": "assistant",
        "content": str(content or ""),
        "tool_calls": [
            {
                "id": str(call["id"]),
                "type": "function",
                "function": {
                    "name": str(call["name"]),
                    "arguments": _arguments_text(call["args"]),
                },
            }
            for call in calls
        ],
    }
