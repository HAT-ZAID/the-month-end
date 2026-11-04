"""A user's own financial position, and the requests they want decided.

The dataset is calibration material: it is how the kernel was tuned and tested.
This module is the product path. An *account* is a JSON file holding one
person's profile, their committed and expected money movements, the purchases
they are weighing, and the payment options each of those offers. It is converted
into exactly the structure ``tools.tui.load_dataset`` returns, so the kernel
solves a real user's numbers without a line of solver change.

Nothing here computes a financial fact. Balances, forecasts and safe amounts
belong to ``code.solver``; this module stores what the person told us and hands
it over in the shape the contract already defines.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from code.amounts import ISO_CODES, strict_decimal

ACCOUNT_VERSION = 1

#: The profile columns the contract defines, in the order the CSV holds them.
PROFILE_FIELDS = (
    "user_id",
    "home_currency",
    "current_available_balance",
    "minimum_balance_to_keep",
    "financial_priorities",
    "expense_categories_to_protect",
    "expense_categories_user_is_willing_to_reduce",
    "expense_categories_user_is_willing_to_stop",
    "payment_methods_user_will_consider",
    "max_installment_months",
)

MULTI_VALUE_PROFILE_FIELDS = (
    "financial_priorities",
    "expense_categories_to_protect",
    "expense_categories_user_is_willing_to_reduce",
    "expense_categories_user_is_willing_to_stop",
    "payment_methods_user_will_consider",
)

EVENT_FIELDS = (
    "event_id",
    "user_id",
    "event_type",
    "description",
    "category",
    "direction",
    "amount",
    "currency",
    "event_date",
    "settlement_date",
    "status",
    "linked_event_id",
    "flexibility",
    "minimum_allowed_amount",
)

REQUEST_FIELDS = (
    "request_id",
    "user_id",
    "request_date",
    "request_type",
    "requested_amount",
    "desired_completion_date",
    "allows_partial_payment",
    "request_text",
)

OPTION_FIELDS = (
    "payment_option_id",
    "request_id",
    "payment_method",
    "payment_amount",
    "number_of_payments",
    "first_payment_date",
    "payment_frequency_days",
    "financing_fee",
    "total_payable_amount",
)

#: One dated exchange rate, in the same direction the contract's CSV states it
#: (``from_currency`` -> ``to_currency``).  A rate is not a user's guess at a
#: market: it is the figure they recorded, and the kernel only converts with it
#: when the pair and date match.
FX_FIELDS = (
    "rate_date",
    "from_currency",
    "to_currency",
    "rate",
)

#: The vocabularies the kernel's ledger understands. These are the dataset's
#: own values, so an account and a calibration row mean the same thing to the
#: solver; a guess here is a silently wrong forecast.
DIRECTIONS = ("credit", "debit", "non_cash")
EVENT_STATUSES = (
    "settled",
    "pending",
    "scheduled",
    "failed",
    "cancelled",
    "unrealized",
)
EVENT_TYPES = (
    "income",
    "expense",
    "subscription",
    "debt_payment",
    "investment_purchase",
    "investment_sale",
    "investment_valuation",
    "refund",
)
FLEXIBILITY = ("fixed", "reducible", "reducible_or_stoppable", "stoppable")
#: Multi-value profile columns are pipe separated in the contract.
MULTI_VALUE_SEPARATOR = "|"


def default_path(repo_root=None) -> Path:
    """Where the account lives: ``data/account.json``, which is gitignored."""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    return root / "data" / "account.json"


def blank_account(user_id: str = "me", home_currency: str = "") -> dict:
    """An empty account, with every contract field present and blank."""
    profile = {name: "" for name in PROFILE_FIELDS}
    profile["user_id"] = user_id
    profile["home_currency"] = home_currency
    return {
        "version": ACCOUNT_VERSION,
        "profile": profile,
        "events": [],
        "requests": [],
        "options": [],
        "fx": [],
    }


def load_account(path=None, repo_root=None) -> dict | None:
    """The stored account, or ``None`` when there is not one yet."""
    target = Path(path) if path is not None else default_path(repo_root)
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8") as handle:
        account = json.load(handle)
    if not isinstance(account, dict):
        raise ValueError("the account file must hold a JSON object")
    account.setdefault("version", ACCOUNT_VERSION)
    account.setdefault("profile", {name: "" for name in PROFILE_FIELDS})
    for key in ("events", "requests", "options", "fx"):
        account.setdefault(key, [])
    return account


def save_account(account: dict, path=None, repo_root=None) -> Path:
    """Write the account, creating its directory. Returns where it went."""
    target = Path(path) if path is not None else default_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {**account, "version": ACCOUNT_VERSION}
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return target


def _present(value) -> bool:
    """Whether a contract field carries an answer at all.

    A blank value means *unknown*, not zero: the person has not answered yet,
    and the decision path names the missing fact instead of computing against
    an invented figure.
    """
    return bool(str(value if value is not None else "").strip())


def _check_amount(problems: list[str], label: str, value) -> None:
    """Reject a present amount that is not a finite, non-negative Decimal."""
    if not _present(value):
        return
    amount = strict_decimal(value)
    if amount is None:
        problems.append(f"{label}: {str(value)!r} is not a finite amount")
    elif amount < 0:
        problems.append(f"{label}: the amount cannot be negative ({str(value)!r})")


def _check_date(problems: list[str], label: str, value) -> None:
    """Reject a present date the kernel cannot read."""
    if not _present(value):
        return
    try:
        date.fromisoformat(str(value).strip())
    except ValueError:
        problems.append(f"{label}: {str(value)!r} is not an ISO date (YYYY-MM-DD)")


def _check_currency(problems: list[str], label: str, value) -> None:
    """Reject a present currency outside the contract's ISO set."""
    if not _present(value):
        return
    code = str(value).strip()
    if code not in ISO_CODES:
        problems.append(
            f"{label}: {code!r} is not one of the ISO currency codes "
            f"({', '.join(sorted(ISO_CODES))})"
        )


def _check_direction(problems: list[str], label: str, value) -> None:
    """Reject a present direction the ledger would silently misread."""
    if not _present(value):
        return
    direction = str(value).strip()
    if direction not in DIRECTIONS:
        problems.append(
            f"{label}: unknown direction {direction!r}; "
            f"use one of {', '.join(DIRECTIONS)}"
        )


def validate_account(account: dict) -> list[str]:
    """Every reason this account would store a fact the kernel cannot trust.

    Called before a save.  An empty list means the account may be written; a
    blank value stays unknown, so only a value that is present and malformed is
    reported.  Duplicate event ids are refused because two rows sharing one id
    make an amendment ambiguous -- the ledger cannot tell which row a later
    correction refers to.  A rate row must be complete: a dated pair with a
    finite, positive rate is the only shape the kernel can convert with.
    """
    if not isinstance(account, dict):
        return ["the account must be a JSON object"]
    problems: list[str] = []
    profile = account.get("profile") or {}
    if not isinstance(profile, dict):
        problems.append("the profile must be a JSON object")
        profile = {}
    _check_currency(problems, "profile home currency", profile.get("home_currency"))
    for name in ("current_available_balance", "minimum_balance_to_keep"):
        _check_amount(problems, f"profile {name}", profile.get(name))

    seen_events: set[str] = set()
    for position, event in enumerate(account.get("events") or [], start=1):
        if not isinstance(event, dict):
            problems.append(f"event {position}: the record must be a JSON object")
            continue
        event_id = str(event.get("event_id") or "").strip()
        label = event_id or f"event {position}"
        if event_id:
            if event_id in seen_events:
                problems.append(f"{label}: duplicate event_id {event_id!r}")
            else:
                seen_events.add(event_id)
        _check_direction(problems, label, event.get("direction"))
        for name in ("amount", "minimum_allowed_amount"):
            _check_amount(problems, f"{label} {name}", event.get(name))
        _check_currency(problems, f"{label} currency", event.get("currency"))
        for name in ("event_date", "settlement_date"):
            _check_date(problems, f"{label} {name}", event.get(name))

    for position, request in enumerate(account.get("requests") or [], start=1):
        if not isinstance(request, dict):
            problems.append(f"request {position}: the record must be a JSON object")
            continue
        request_id = str(request.get("request_id") or "").strip()
        label = request_id or f"request {position}"
        _check_amount(
            problems, f"{label} requested_amount", request.get("requested_amount")
        )
        for name in ("request_date", "desired_completion_date"):
            _check_date(problems, f"{label} {name}", request.get(name))

    for position, option in enumerate(account.get("options") or [], start=1):
        if not isinstance(option, dict):
            problems.append(f"payment option {position}: the record must be a JSON object")
            continue
        option_id = str(option.get("payment_option_id") or "").strip()
        label = option_id or f"payment option {position}"
        for name in ("payment_amount", "financing_fee", "total_payable_amount"):
            _check_amount(problems, f"{label} {name}", option.get(name))
        _check_date(
            problems, f"{label} first_payment_date", option.get("first_payment_date")
        )

    for position, rate_row in enumerate(account.get("fx") or [], start=1):
        if not isinstance(rate_row, dict):
            problems.append(f"fx {position}: the record must be a JSON object")
            continue
        label = f"fx {position}"
        # A rate row is only meaningful when it says which pair it prices and
        # when.  A half-written row would never match a settlement date, so it
        # is refused rather than stored as a rate that silently does nothing.
        for name in FX_FIELDS:
            if not _present(rate_row.get(name)):
                problems.append(f"{label}: {name} is required for a rate row")
        _check_date(problems, f"{label} rate_date", rate_row.get("rate_date"))
        for name in ("from_currency", "to_currency"):
            _check_currency(problems, f"{label} {name}", rate_row.get(name))
        rate = rate_row.get("rate")
        if _present(rate):
            value = strict_decimal(rate)
            if value is None:
                problems.append(f"{label} rate: {str(rate)!r} is not a finite amount")
            elif value <= 0:
                problems.append(
                    f"{label} rate: the rate must be greater than zero "
                    f"({str(rate)!r})"
                )

    return problems


def as_list(value) -> list[str]:
    """Split a contract multi-value field, accepting a comma too.

    The contract uses ``|``; people type commas. Storing the contract's form is
    what makes the account and a dataset row mean the same thing.
    """
    parts = str(value or "").replace(",", MULTI_VALUE_SEPARATOR).split(
        MULTI_VALUE_SEPARATOR
    )
    return [part.strip() for part in parts if part.strip()]


def as_contract_list(value) -> str:
    """The canonical ``a|b|c`` form of a multi-value field."""
    return MULTI_VALUE_SEPARATOR.join(as_list(value))


def _next_id(existing, prefix: str) -> str:
    """The next free ``<prefix>_<n>`` for a list of records."""
    used = set()
    for record in existing:
        value = str(record.get(f"{prefix}_id") or "")
        if value.startswith(f"{prefix}_") and value[len(prefix) + 1 :].isdigit():
            used.add(int(value[len(prefix) + 1 :]))
    number = 1
    while number in used:
        number += 1
    return f"{prefix}_{number}"


def set_profile(account: dict, **fields) -> dict:
    """Record what the person told us about their position."""
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise KeyError(f"unknown profile field(s): {', '.join(sorted(unknown))}")
    profile = account.setdefault("profile", {name: "" for name in PROFILE_FIELDS})
    for name, value in fields.items():
        written = "" if value is None else str(value).strip()
        if name in MULTI_VALUE_PROFILE_FIELDS:
            written = as_contract_list(written)
        profile[name] = written
    return profile


def add_event(account: dict, **fields) -> dict:
    """Record one money movement: income, bill, subscription, purchase, refund."""
    unknown = set(fields) - set(EVENT_FIELDS)
    if unknown:
        raise KeyError(f"unknown event field(s): {', '.join(sorted(unknown))}")
    user_id = str(account.get("profile", {}).get("user_id") or "me")
    event = {name: "" for name in EVENT_FIELDS}
    event["event_id"] = _next_id(account.get("events", []), "event")
    event["user_id"] = user_id
    for name, value in fields.items():
        event[name] = "" if value is None else str(value).strip()
    if not event["currency"]:
        event["currency"] = str(account.get("profile", {}).get("home_currency") or "")
    account.setdefault("events", []).append(event)
    return event


def add_request(account: dict, **fields) -> dict:
    """Record a purchase the person wants a decision on."""
    unknown = set(fields) - set(REQUEST_FIELDS)
    if unknown:
        raise KeyError(f"unknown request field(s): {', '.join(sorted(unknown))}")
    user_id = str(account.get("profile", {}).get("user_id") or "me")
    request = {name: "" for name in REQUEST_FIELDS}
    request["request_id"] = _next_id(account.get("requests", []), "request")
    request["user_id"] = user_id
    for name, value in fields.items():
        request[name] = "" if value is None else str(value).strip()
    account.setdefault("requests", []).append(request)
    return request


def add_option(account: dict, request_id: str, **fields) -> dict:
    """Record a payment option the seller offers for one request."""
    unknown = set(fields) - set(OPTION_FIELDS)
    if unknown:
        raise KeyError(f"unknown option field(s): {', '.join(sorted(unknown))}")
    option = {name: "" for name in OPTION_FIELDS}
    option["payment_option_id"] = _next_id(account.get("options", []), "payment_option")
    option["request_id"] = str(request_id)
    for name, value in fields.items():
        option[name] = "" if value is None else str(value).strip()
    account.setdefault("options", []).append(option)
    return option


def _empty_frame(columns):
    import pandas as pd

    return pd.DataFrame({name: [] for name in columns}, dtype=object)


def account_data(account: dict) -> dict:
    """The account in exactly the shape ``load_dataset`` returns.

    The kernel and the TUI share this structure, so a user's own numbers travel
    the same path the calibration data took -- including the fail-closed checks
    in ``_solve``.
    """
    profile = {
        name: str(account.get("profile", {}).get(name, "") or "")
        for name in PROFILE_FIELDS
    }
    user_id = profile.get("user_id") or "me"
    events = [
        {name: str(record.get(name, "") or "") for name in EVENT_FIELDS}
        for record in account.get("events", [])
    ]
    requests = [
        {name: str(record.get(name, "") or "") for name in REQUEST_FIELDS}
        for record in account.get("requests", [])
    ]
    options = account.get("options", [])
    if options:
        import pandas as pd

        frame = pd.DataFrame(
            [{name: str(record.get(name, "") or "") for name in OPTION_FIELDS} for record in options]
        )
    else:
        frame = _empty_frame(OPTION_FIELDS)
    return {
        "requests": requests,
        "profiles": {user_id: profile},
        "events_by_user": {user_id: events},
        "fx": [dict(record) for record in account.get("fx", [])],
        "options": frame,
        "messages": _empty_frame(
            ("message_id", "user_id", "related_event_id", "message_text")
        ),
        "warnings": [],
    }


def problems(account: dict) -> list[str]:
    """What is missing before a decision means anything.

    A decision computed from a half-filled profile is worse than no decision,
    so the TUI refuses to predict until these are answered.
    """
    found: list[str] = []
    profile = account.get("profile", {})
    if not str(profile.get("home_currency") or "").strip():
        found.append("home currency is not set")
    for name, label in (
        ("current_available_balance", "current available balance"),
        ("minimum_balance_to_keep", "minimum balance to keep"),
    ):
        value = str(profile.get(name) or "").strip()
        if not value:
            found.append(f"{label} is not set")
            continue
        try:
            if float(value.replace(",", "")) < 0:
                found.append(f"{label} cannot be negative")
        except ValueError:
            found.append(f"{label} is not a number: {value!r}")
    if not account.get("events"):
        found.append("no money movements recorded, so nothing can be forecast")
    return found
