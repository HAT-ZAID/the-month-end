"""What-if scenarios: a hypothesis decided on a throwaway copy of an account.

A scenario changes one recorded fact and asks the kernel what the decision would
be.  :func:`simulate_scenario` deep-copies the account, applies the change to the
copy, and hands the copy to :func:`code.service.decide` -- the same path a real
decision takes.  It never writes: the result says ``"persisted": False`` and the
caller's account is byte-for-byte what it was before the call.

Three hypotheses are supported: pay for the request on another day
(``pay_on``), shift it by a whole number of days (``delay_days``), or re-price
one committed expense (``change_expense``).  A field the kind cannot apply, a
date or amount that cannot be read, a request or event that does not exist, a
re-pricing of an income row, a change that would remove or reduce a protected
expense, and a record that belongs to another account are all refused with
:class:`ValueError` before the kernel runs.  Ignoring half a hypothesis would
produce a confident number about a question nobody asked; inventing income or
spending protected money would produce one about a position the person never
had.

:func:`compare_scenarios` runs two hypotheses through the same path and names
the machine fields that differ, so a caller can see exactly what moved.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from code import service
from code.amounts import strict_decimal

#: The hypotheses this module knows how to apply, in the order they are named
#: back to a caller that asked for something else.
SUPPORTED_KINDS = ("pay_on", "delay_days", "change_expense")

#: The decision columns the output contract defines.  A scenario reports exactly
#: these, so a hypothetical and a real decision are compared field for field.
KERNEL_FIELDS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

#: Every field a scenario can carry.  Used to refuse a value the chosen kind
#: would otherwise silently drop.
_SCENARIO_FIELDS = ("request_id", "amount", "on", "by", "event_id", "new_amount")


@dataclass
class Scenario:
    """One change to try on a copy of the account.

    ``amount`` is reserved for a kind that re-prices the request itself.  The
    kinds here do not, so a non-blank ``amount`` is refused rather than ignored.
    """

    kind: str
    request_id: str = ""
    amount: str = ""
    on: str = ""
    by: int = 0
    event_id: str = ""
    new_amount: str = ""


def simulate_scenario(account, scenario) -> dict:
    """Decide ``scenario`` on a copy of ``account``; never touch the original."""
    if not isinstance(account, dict):
        raise TypeError(
            f"simulate_scenario needs an account dict, got {type(account).__name__}"
        )
    if not isinstance(scenario, Scenario):
        raise TypeError(
            f"simulate_scenario needs a Scenario, got {type(scenario).__name__}"
        )

    kind = _kind(scenario)
    owner = _owner(account)
    copied = copy.deepcopy(account)
    assumptions = _apply(copied, scenario, kind, owner)

    request_id = str(scenario.request_id or "").strip()
    decision = service.decide(copied, request_id)

    assumptions.append("figures come from code.service.decide on the copied account")
    assumptions.append("the scenario is a hypothesis: nothing was saved")
    return {
        "scenario_id": _scenario_id(kind, scenario),
        "kernel_result": {name: decision.get(name) for name in KERNEL_FIELDS},
        "assumptions": assumptions,
        "persisted": False,
    }


def compare_scenarios(account, baseline, alternative) -> dict:
    """Decide two hypotheses and name the machine fields that moved.

    Both sides run through :func:`simulate_scenario`, so each gets its own copy
    of the account and neither is saved.  ``delta_fields`` lists the contract
    fields whose value differs, in :data:`KERNEL_FIELDS` order; an empty list
    means the two hypotheses decide the request identically.
    """
    if not isinstance(baseline, Scenario):
        raise TypeError(
            f"compare_scenarios needs a Scenario baseline, got {type(baseline).__name__}"
        )
    if not isinstance(alternative, Scenario):
        raise TypeError(
            "compare_scenarios needs a Scenario alternative, got "
            f"{type(alternative).__name__}"
        )

    left = simulate_scenario(account, baseline)
    right = simulate_scenario(account, alternative)
    delta_fields = [
        name
        for name in KERNEL_FIELDS
        if left["kernel_result"].get(name) != right["kernel_result"].get(name)
    ]
    return {
        "baseline": left,
        "alternative": right,
        "delta_fields": delta_fields,
    }


# ------------------------------------------------------------- applying a kind


def _kind(scenario) -> str:
    kind = str(scenario.kind or "").strip().lower()
    if kind not in SUPPORTED_KINDS:
        raise ValueError(
            f"unsupported scenario kind {scenario.kind!r}; "
            f"supported kinds: {', '.join(SUPPORTED_KINDS)}"
        )
    return kind


def _apply(copied, scenario, kind, owner) -> list[str]:
    """Apply exactly one change to ``copied`` and say what it assumed."""
    request = _request_row(copied, scenario.request_id, owner)
    lines = [
        "the account was deep-copied; the original is unchanged",
        f"request {request['request_id']} keeps its amount of "
        f"{request.get('requested_amount')}",
    ]

    if kind == "pay_on":
        _refuse_unused(scenario, keep=("request_id", "on"))
        day = _iso_day(scenario.on, "on")
        before = _iso_day(request.get("request_date"), "request_date")
        request["request_date"] = day.isoformat()
        lines.append(f"the purchase moved from {before.isoformat()} to {day.isoformat()}")
    elif kind == "delay_days":
        _refuse_unused(scenario, keep=("request_id", "by"))
        days = _whole_days(scenario.by)
        before = _iso_day(request.get("request_date"), "request_date")
        try:
            day = before + timedelta(days=days)
        except OverflowError:
            raise ValueError(
                f"{days} days from {before.isoformat()} is not a date"
            ) from None
        request["request_date"] = day.isoformat()
        lines.append(
            f"the purchase moved from {before.isoformat()} to {day.isoformat()} "
            f"({days:+d} days)"
        )
    else:  # change_expense
        _refuse_unused(scenario, keep=("request_id", "event_id", "new_amount"))
        event = _event_row(copied, scenario.event_id, owner)
        _refuse_income(event)
        _refuse_protected(copied, event)
        amount = _non_negative_amount(scenario.new_amount)
        before = str(event.get("amount") or "")
        event["amount"] = str(amount)
        lines.append(
            f"expense {event['event_id']} moved from {before} to {str(amount)}"
        )
    return lines


def _refuse_unused(scenario, *, keep) -> None:
    for name in _SCENARIO_FIELDS:
        if name in keep:
            continue
        value = getattr(scenario, name, None)
        if value is None or value in ("", 0):
            continue
        raise ValueError(
            f"{scenario.kind!r} does not use {name!r}; a field the kind cannot "
            "apply is refused rather than ignored"
        )


def _refuse_income(event) -> None:
    """Refuse to re-price anything that is not a committed expense debit.

    The kernel counts an income row forward; lowering or raising it inside a
    what-if would invent (or erase) money the person did not move.  A direction
    that claims ``debit`` does not make an income row spendable, so the event
    type is checked too.
    """
    event_type = str(event.get("event_type") or "").strip().lower()
    direction = str(event.get("direction") or "").strip().lower()
    if direction == "debit" and event_type in ("expense", "subscription"):
        return
    label = event_type or "unclassified"
    raise ValueError(
        f"change_expense only re-prices an expense or subscription debit; "
        f"row {event.get('event_id')!r} is {label!r}, so re-pricing it would "
        "invent income and is refused"
    )


def _refuse_protected(copied, event) -> None:
    """Refuse to touch an expense in a category the person asked to protect."""
    profile = copied.get("profile") or {}
    protected = _profile_categories(profile, "expense_categories_to_protect")
    category = _category_word(event.get("category"))
    if category and category in protected:
        raise ValueError(
            f"row {event.get('event_id')!r} is in protected category "
            f"{category!r}; changing or removing a protected expense is refused"
        )


def _profile_categories(profile, key) -> set[str]:
    raw = str((profile or {}).get(key, "") or "")
    return {
        word
        for word in (_category_word(part) for part in raw.replace(",", "|").split("|"))
        if word
    }


def _category_word(value) -> str:
    """The lower-case, underscore-joined form of a category label."""
    text = str(value or "").strip().lower()
    for separator in (" ", "-", "/"):
        text = text.replace(separator, "_")
    return "_".join(part for part in text.split("_") if part)


def _owner(account) -> str:
    profile = account.get("profile") or {}
    return str((profile or {}).get("user_id") or "me")


def _refuse_foreign(row, owner, kind) -> None:
    row_user = str(row.get("user_id") or "").strip()
    if row_user and row_user != owner:
        row_id = row.get("request_id") or row.get("event_id")
        raise ValueError(
            f"{kind} {row_id!r} belongs to another account ({row_user!r}); "
            f"this scenario may only target {owner!r}"
        )


def _request_row(copied, request_id, owner) -> dict:
    wanted = str(request_id or "").strip()
    if not wanted:
        raise ValueError("a scenario needs the request_id it is asked about")
    for row in copied.get("requests") or []:
        if str(row.get("request_id")) == wanted:
            _refuse_foreign(row, owner, "request")
            return row
    raise ValueError(f"no request {wanted!r} on this account")


def _event_row(copied, event_id, owner) -> dict:
    wanted = str(event_id or "").strip()
    if not wanted:
        raise ValueError("change_expense needs the event_id of the expense to re-price")
    for row in copied.get("events") or []:
        if str(row.get("event_id")) == wanted:
            _refuse_foreign(row, owner, "event")
            return row
    raise ValueError(f"no event {wanted!r} on this account")


def _iso_day(value, field) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise ValueError(
            f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}"
        ) from None


def _whole_days(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"by must be a whole number of days, got {value!r}")
    return value


def _non_negative_amount(value):
    amount = strict_decimal(value)
    if amount is None:
        raise ValueError(f"new_amount must be a finite number, got {value!r}")
    if amount < 0:
        raise ValueError(
            "new_amount cannot be negative; stop the expense instead of "
            "re-pricing it below zero"
        )
    return amount


# --------------------------------------------------------------- identity


def _scenario_id(kind, scenario) -> str:
    """A stable id for the hypothesis itself, so two runs can be compared."""
    payload = {**asdict(scenario), "kind": kind}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    return f"scenario-{kind}-{digest}"
