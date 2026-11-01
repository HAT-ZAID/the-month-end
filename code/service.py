"""The engine the interface talks to.

One rule decides the shape of this module: **every figure about a person's money
comes from the kernel**.  :func:`code.oracle.solve_safe_amount` and
:func:`code.solver.solve_request` own the arithmetic and the decisions;
:func:`code.solver.position` owns the position summary.  This module only
assembles the plain structures those functions take and stores the result, so a
view layer (the Textual app, the Rich TUI, a test) can render a number it did not
compute.  Nothing here sums a balance.

Persistence is :mod:`code.accounts` (SQLite, ``data/accounts.db``).  The account
shape is :mod:`code.account`, which also owns the contract vocabularies and
rejects an unknown word at the boundary rather than letting it reach the solver
as a silently wrong forecast.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from code import account as acct
from code import accounts as store
from code.solver import position as kernel_position
from code.solver import solve_request

#: The four kinds a person records in one line.  Each maps onto the contract's
#: own vocabulary rather than inventing a word the kernel would ignore.
FACT_KINDS = {
    "income": {
        "event_type": "income",
        "direction": "credit",
        "category": "income",
        "flexibility": "fixed",
        "status": "settled",
    },
    "bill": {
        "event_type": "expense",
        "direction": "debit",
        "category": "bills",
        "flexibility": "fixed",
        "status": "scheduled",
    },
    "subscription": {
        "event_type": "subscription",
        "direction": "debit",
        "category": "subscriptions",
        "flexibility": "stoppable",
        "status": "scheduled",
    },
    "refund": {
        "event_type": "refund",
        "direction": "credit",
        "category": "refunds",
        "flexibility": "fixed",
        "status": "settled",
    },
}


class EngineError(ValueError):
    """A request the engine refuses before it can become a wrong number."""


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise EngineError(f"{value!r} is not an amount") from None


def money(value) -> str:
    """A kernel figure as a plain two-place string, for display only."""
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return str(value)
    return f"{amount:,.2f}"


# ---------------------------------------------------------------- users


def list_users(root=None) -> list[str]:
    return store.list_users(repo_root=root)


def create_user(name, currency="", balance="", floor="", root=None) -> dict:
    """Create one named person and record what they told us so far."""
    account = store.create(name, home_currency=currency, repo_root=root)
    if str(balance).strip() or str(floor).strip():
        update_profile(account, balance=balance, floor=floor, currency=currency)
        store.save(account, repo_root=root)
    return account


def load_user(name, root=None) -> dict | None:
    return store.load(name, repo_root=root)


def save_user(account, root=None) -> str:
    return store.save(account, repo_root=root)


def update_profile(account, *, currency=None, balance=None, floor=None, **extra) -> dict:
    """Record one or more profile facts, validating them before storage."""
    fields = {}
    if currency is not None:
        fields["home_currency"] = currency
    if balance is not None:
        amount = _decimal(balance)
        if amount < 0:
            raise EngineError("available balance cannot be negative")
        fields["current_available_balance"] = str(amount)
    if floor is not None:
        amount = _decimal(floor)
        if amount < 0:
            raise EngineError("the minimum balance to keep cannot be negative")
        fields["minimum_balance_to_keep"] = str(amount)
    for key, value in extra.items():
        fields[key] = value
    if not fields:
        return account.get("profile", {})
    if "payment_methods_user_will_consider" not in fields:
        profile = account.get("profile", {})
        if not str(profile.get("payment_methods_user_will_consider") or "").strip():
            # A person who has not answered still wants the ordinary methods
            # considered; the kernel would otherwise reject every plan shape.
            fields["payment_methods_user_will_consider"] = (
                "full_payment|partial_payment|installments"
            )
    return acct.set_profile(account, **fields)


# ------------------------------------------------------------- facts


def add_fact(account, kind, amount, *, when=None, description="", category="",
             source="", status=None) -> dict:
    """Record one money movement in one call.

    ``kind`` is one of :data:`FACT_KINDS`; the contract words behind it are
    supplied here so the caller never has to remember ``credit`` versus
    ``inflow``.
    """
    key = str(kind or "").strip().lower()
    if key not in FACT_KINDS:
        raise EngineError(
            f"unknown kind {kind!r}; use one of {', '.join(sorted(FACT_KINDS))}"
        )
    value = _decimal(amount)
    if value <= 0:
        raise EngineError("an amount must be greater than zero")
    day = str(when or date.today().isoformat())
    if not _is_iso(day):
        raise EngineError(f"{day!r} is not an ISO date (YYYY-MM-DD)")
    template = FACT_KINDS[key]
    event = acct.add_event(
        account,
        event_type=template["event_type"],
        description=str(description or key),
        category=str(category or template["category"]),
        direction=template["direction"],
        amount=str(value),
        currency=str(account.get("profile", {}).get("home_currency") or ""),
        event_date=day,
        settlement_date=day,
        status=str(status or template["status"]),
        flexibility=template["flexibility"],
        linked_event_id=str(source or ""),
    )
    return event


def delete_event(account, event_id) -> bool:
    before = len(account.get("events") or [])
    account["events"] = [
        row for row in (account.get("events") or [])
        if str(row.get("event_id")) != str(event_id)
    ]
    return len(account["events"]) != before


def _is_iso(value) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------- position


def position(account, as_of=None) -> dict:
    """The kernel's position summary for one account."""
    profile = dict(account.get("profile") or {})
    events = [dict(row) for row in (account.get("events") or [])]
    day = as_of or date.today().isoformat()
    result = kernel_position(
        str(profile.get("user_id") or "me"), day, profile, events, [],
    )
    result["problems"] = acct.problems(account)
    return result


# --------------------------------------------------------- decisions


def _options_frame(account, request_id) -> pd.DataFrame:
    rows = [
        row for row in (account.get("options") or [])
        if str(row.get("request_id")) == str(request_id)
    ]
    frame = pd.DataFrame(rows, columns=list(acct.OPTION_FIELDS)) if rows else pd.DataFrame(
        columns=list(acct.OPTION_FIELDS)
    )
    return frame


def decide(account, request_id) -> dict:
    """Run the kernel for one recorded request and return its decision."""
    requests = [row for row in (account.get("requests") or [])
                if str(row.get("request_id")) == str(request_id)]
    if not requests:
        raise EngineError(f"no request {request_id!r} on this account")
    request = requests[0]
    profile = dict(account.get("profile") or {})
    events = [dict(row) for row in (account.get("events") or [])]
    decision = solve_request(
        str(request.get("request_id")),
        str(profile.get("user_id") or "me"),
        str(request.get("request_date")),
        str(request.get("requested_amount")),
        profile,
        events,
        [],
        _options_frame(account, request_id),
        messages_df=None,
        desired_completion_date=str(request.get("desired_completion_date") or "") or None,
        allows_partial_payment=str(request.get("allows_partial_payment") or ""),
    )
    decision["request"] = request
    return decision


def buy(account, amount, *, on=None, by=None, text="", allows_partial=True,
        options=None) -> dict:
    """Record a purchase, decide it, and return the decision.

    ``options`` is an optional list of dicts in the contract's payment-option
    columns; it is stored against the new request before the kernel runs.
    """
    value = _decimal(amount)
    if value <= 0:
        raise EngineError("a purchase amount must be greater than zero")
    day = str(on or date.today().isoformat())
    if not _is_iso(day):
        raise EngineError(f"{day!r} is not an ISO date (YYYY-MM-DD)")
    deadline = str(by or "")
    if deadline and not _is_iso(deadline):
        raise EngineError(f"{by!r} is not an ISO date (YYYY-MM-DD)")
    request = acct.add_request(
        account,
        request_date=day,
        request_type="purchase",
        requested_amount=str(value),
        desired_completion_date=deadline,
        allows_partial_payment="True" if allows_partial else "False",
        request_text=str(text or ""),
    )
    for option in options or []:
        acct.add_option(account, request["request_id"], **option)
    return decide(account, request["request_id"])


def history(account) -> list[dict]:
    """Every recorded request with the decision the kernel gives today."""
    rows = []
    for request in account.get("requests") or []:
        try:
            rows.append(decide(account, request.get("request_id")))
        except Exception as exc:  # a bad row must not hide the others
            rows.append({
                "request_id": request.get("request_id"),
                "request": request,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return rows
