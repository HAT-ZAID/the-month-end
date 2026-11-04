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
from code.accounts import AccountExistsError
from code.forecast import DISCRETIONARY_CATEGORIES, FORECASTABLE_EXPENSE_CATEGORIES
from code.solver import MISSING_FACTS
from code.solver import position as kernel_position
from code.solver import solve_request

#: The four kinds a person records in one line.  Each maps onto the contract's
#: own vocabulary rather than inventing a word the kernel would ignore.  The
#: *category* is not fixed here: it depends on what the money is, so
#: :func:`resolve_category` names it.
FACT_KINDS = {
    "income": {
        "event_type": "income",
        "direction": "credit",
        "flexibility": "fixed",
        "status": "settled",
    },
    "bill": {
        "event_type": "expense",
        "direction": "debit",
        "flexibility": "fixed",
        "status": "scheduled",
    },
    "subscription": {
        "event_type": "subscription",
        "direction": "debit",
        "flexibility": "stoppable",
        "status": "scheduled",
    },
    "refund": {
        "event_type": "refund",
        "direction": "credit",
        "flexibility": "fixed",
        "status": "settled",
    },
}

#: Expense categories the ledger actually projects forward: the committed ones,
#: never the lifestyle categories the forecast refuses to extrapolate.  Derived
#: from :mod:`code.forecast` so the service cannot drift from the kernel.
PROJECTED_EXPENSE_CATEGORIES = frozenset(
    FORECASTABLE_EXPENSE_CATEGORIES - DISCRETIONARY_CATEGORIES
)

#: Categories that are recorded but never projected.  ``irregular_income`` and
#: ``refunds`` are credits counted on their own date and never invented forward;
#: the rest are the catch-all words this module used to store, kept here so a
#: refusal can say what the person typed.
NON_FORECASTABLE_CATEGORIES = frozenset(
    {"irregular_income", "refunds", "bills", "subscriptions", "income"}
)

#: Where an unlabelled bill or subscription lands.  A committed bucket, because
#: dropping the row would understate the month; the person's description stays
#: as typed, so the money itself is never invented.
DEFAULT_EXPENSE_CATEGORY = {"bill": "utilities", "subscription": "streaming"}


class EngineError(ValueError):
    """A request the engine refuses before it can become a wrong number."""


# The profile facts the kernel cannot invent, paired with the plain words a
# refusal uses for them.  An unanswered fact stays ``""`` (unknown) all the way
# to the decision gate: computing against an invented zero is how a skipped
# onboarding question became a confident "not affordable" verdict (CR-14).
# Defined once in the kernel and imported above, so this module cannot drift
# into naming fields or labels a position does not name.


def missing_facts(account) -> list[str]:
    """What a decision still needs, as refusals that name each field.

    An empty list means the account is answered well enough to decide.  The
    vocabulary is the kernel's own (:data:`code.solver.MISSING_FACTS`); the
    check is presence only: a value that is present is parsed by
    :func:`update_profile`, never reinterpreted here.
    """
    profile = account.get("profile") or {}
    return [
        f"{label} is not set"
        for field, label in MISSING_FACTS
        if not str(profile.get(field) or "").strip()
    ]


def _refuse_missing_facts(account) -> None:
    """Stop a decision that would otherwise be computed against a zero."""
    missing = missing_facts(account)
    if missing:
        raise EngineError("; ".join(missing))


def _category_word(value) -> str:
    """The lower-case, underscore-joined form of a typed category or label."""
    text = str(value or "").strip().lower()
    for separator in (" ", "-", "/"):
        text = text.replace(separator, "_")
    return "_".join(part for part in text.split("_") if part)


def resolve_category(kind: str, category: str = "", recurrence: str = "",
                     description: str = "") -> str:
    """The contract category for one recorded fact, or a refusal.

    A fact is only worth recording if the ledger can see it.  The forecast
    projects exactly two vocabularies: ``salary`` income
    (:data:`code.forecast.PRIMARY_INCOME_CATEGORIES`) and the committed expense
    categories (:data:`PROJECTED_EXPENSE_CATEGORIES`).  Every other word --
    including the ``bills``/``subscriptions``/``income``/``refunds`` catch-alls
    this module used to store -- is kept on the row and then ignored by the
    projection, which is how a salary typed into the app vanished from the
    decision (CR-05).

    * income paid monthly is ``salary``; any other income is
      ``irregular_income``, counted on its own date and never projected;
    * a bill or subscription must name a projected category, because the
      forecast groups months by category to build its monthly budget;
    * a refund keeps its own ``refunds`` category: it is credited once, never
      repeated.
    """
    key = str(kind or "").strip().lower()
    if key not in FACT_KINDS:
        raise EngineError(
            f"unknown kind {kind!r}; use one of {', '.join(sorted(FACT_KINDS))}"
        )
    if key == "income":
        monthly = str(recurrence or "").strip().lower() == "monthly"
        return "salary" if monthly else "irregular_income"
    stated = _category_word(category)
    if key == "refund":
        return stated or "refunds"
    resolved = stated
    if not resolved:
        # People often type the category into the description ("rent"); honour
        # it when it is one the forecast projects.
        word = _category_word(description)
        if word in PROJECTED_EXPENSE_CATEGORIES:
            resolved = word
    if not resolved:
        resolved = DEFAULT_EXPENSE_CATEGORY[key]
    if resolved not in PROJECTED_EXPENSE_CATEGORIES:
        because = (
            " (that is the kind of word recorded but never projected)"
            if resolved in NON_FORECASTABLE_CATEGORIES
            else ""
        )
        raise EngineError(
            f"{str(category or '').strip()!r} is not a category the forecast "
            f"projects for a {key}{because}; use one of "
            f"{', '.join(sorted(PROJECTED_EXPENSE_CATEGORIES))}"
        )
    return resolved


def _decimal(value, what: str = "amount") -> Decimal:
    """Parse one stored fact amount, or refuse it by the field it belongs to.

    A non-finite value is a number only to :class:`Decimal`: storing it makes
    the account unsavable later and, for NaN, makes the next comparison raise
    ``InvalidOperation``.  It is refused here, before anything is recorded.
    """
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise EngineError(f"{value!r} is not an amount") from None
    if not amount.is_finite():
        raise EngineError(f"{what} must be a finite amount")
    return amount


def _profile_amount(value, what: str) -> str:
    """A profile amount as stored: ``""`` for a blank answer, else the number.

    A blank answer means *not answered yet*, so it is kept as ``""`` rather than
    becoming a zero the person never gave (CR-14).  Anything present is parsed
    exactly as before; a non-finite word is refused instead of being stored and
    later read as a balance.
    """
    text = str(value).strip()
    if not text:
        return ""
    amount = _decimal(text, what)
    if amount < 0:
        raise EngineError(f"{what} cannot be negative")
    return str(amount)


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


def create_user(name, currency="", balance="", floor="", root=None, *, overwrite=False) -> dict:
    """Create one named person and record what they told us so far.

    An unanswered balance or floor is stored blank -- unknown, not zero -- so
    :func:`decide` can name the fact the person has yet to answer (CR-14).
    A name that already exists is refused unless ``overwrite`` is true, because
    the store replaces every row for that person; a blank profile must never
    erase a ledger by accident.
    """
    account = store.create(
        name, home_currency=currency, repo_root=root, overwrite=overwrite
    )
    update_profile(account, balance=balance, floor=floor, currency=currency)
    store.save(account, repo_root=root)
    return account


def load_user(name, root=None) -> dict | None:
    return store.load(name, repo_root=root)


def save_user(account, root=None) -> str:
    return store.save(account, repo_root=root)


def update_profile(account, *, currency=None, balance=None, floor=None, **extra) -> dict:
    """Record one or more profile facts, validating them before storage.

    A blank balance or floor is stored as ``""``: the person has not answered,
    and :func:`decide` says so rather than treating the silence as zero.
    """
    fields = {}
    if currency is not None:
        fields["home_currency"] = currency
    if balance is not None:
        fields["current_available_balance"] = _profile_amount(
            balance, "available balance"
        )
    if floor is not None:
        fields["minimum_balance_to_keep"] = _profile_amount(
            floor, "the minimum balance to keep"
        )
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
             recurrence="", source="", status=None) -> dict:
    """Record one money movement in one call.

    ``kind`` is one of :data:`FACT_KINDS`; the contract words behind it are
    supplied here so the caller never has to remember ``credit`` versus
    ``inflow``.  ``category`` and ``recurrence`` say what the money actually is,
    and :func:`resolve_category` turns that into a word the forecast reads.
    """
    key = str(kind or "").strip().lower()
    if key not in FACT_KINDS:
        raise EngineError(
            f"unknown kind {kind!r}; use one of {', '.join(sorted(FACT_KINDS))}"
        )
    value = _decimal(amount, "amount")
    if value <= 0:
        raise EngineError("an amount must be greater than zero")
    day = str(when or date.today().isoformat())
    if not _is_iso(day):
        raise EngineError(f"{day!r} is not an ISO date (YYYY-MM-DD)")
    written = str(description or key)
    resolved = resolve_category(key, category, recurrence, description=written)
    template = FACT_KINDS[key]
    event = acct.add_event(
        account,
        event_type=template["event_type"],
        description=written,
        category=resolved,
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
    fx_rows = [dict(row) for row in (account.get("fx") or [])]
    day = as_of or date.today().isoformat()
    result = kernel_position(
        str(profile.get("user_id") or "me"), day, profile, events, fx_rows,
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
    """Run the kernel for one recorded request and return its decision.

    Refuses while a fact the kernel cannot invent is unanswered: a verdict
    computed against an invented zero is worse than no verdict (CR-14).
    """
    requests = [row for row in (account.get("requests") or [])
                if str(row.get("request_id")) == str(request_id)]
    if not requests:
        raise EngineError(f"no request {request_id!r} on this account")
    _refuse_missing_facts(account)
    request = requests[0]
    profile = dict(account.get("profile") or {})
    events = [dict(row) for row in (account.get("events") or [])]
    fx_rows = [dict(row) for row in (account.get("fx") or [])]
    decision = solve_request(
        str(request.get("request_id")),
        str(profile.get("user_id") or "me"),
        str(request.get("request_date")),
        str(request.get("requested_amount")),
        profile,
        events,
        fx_rows,
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
    value = _decimal(amount, "purchase amount")
    if value <= 0:
        raise EngineError("a purchase amount must be greater than zero")
    day = str(on or date.today().isoformat())
    if not _is_iso(day):
        raise EngineError(f"{day!r} is not an ISO date (YYYY-MM-DD)")
    deadline = str(by or "")
    if deadline and not _is_iso(deadline):
        raise EngineError(f"{by!r} is not an ISO date (YYYY-MM-DD)")
    # Checked before anything is recorded: a refused purchase must not leave a
    # phantom request on the account (CR-14).
    _refuse_missing_facts(account)
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
