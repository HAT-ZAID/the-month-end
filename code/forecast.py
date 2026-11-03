"""Forecast policy for the daily ledger.

The oracle answers one question: *how much cash can this user safely commit on
the request date without dropping below ``minimum_balance_to_keep`` anywhere in
the 90-day horizon?*  This module supplies the only forward-looking cash rows the
ledger may contain, under an explicit, conservative policy:

* expenses are projected as **one monthly budget per committed category**
  (:data:`FORECASTABLE_EXPENSE_CATEGORIES`); discretionary spending
  (:data:`DISCRETIONARY_CATEGORIES`) is never invented forward;
* the budget is the **median of the monthly home-currency totals** of the
  category's eligible history, so a one-off month or a single outlier cannot
  dominate it, and two **complete** months of history are required before a
  category is projected at all (the still-running request month is not a month);
* the budget posts on the request date and on each following month boundary of
  the horizon, net of the real countable rows already inside that month, so
  nothing is double counted and no bucket goes negative.  The default first
  bucket is the conservative charge; setting :data:`FORECAST_CORRECTIONS_ENV`
  switches it to the unspent remainder of the request month
  (``full - month_to_date``), never a negative amount;
* the request month is prorated **only when the window contains a projected
  salary**: proration assumes the rest of the month is funded by the rest of
  the month's income.  The default keeps the fixed 30-day, inclusive-day charge;
  :data:`FORECAST_CORRECTIONS_ENV` switches to the days after the request date
  (numerator ``days_left - 1``) over the month's real length.  Those two
  corrections are opt-in because they raise the amount declared safe and would
  move the G1 safety gate off 0/0/0;
* at most **one** regular salary stream is projected, chosen by cadence and
  amount stability, and never more than three occurrences inside the horizon;
* gig/platform/freelance/commission/bonus/arrears/prize/seasonal/temporary/
  final/prorated/secondary streams are never projected, and an ended employment
  stops projection entirely;
* a permitted spending change moves the budget: ``stop`` removes the category,
  ``reduce_to`` replaces its monthly budget and keeps it;
* typed evidence expense facts (percent increase, explicit new amount, ended
  category) move only the forecast rows on or after their effective date.  An
  ambiguous fact leaves the forecast unchanged and is reported as a warning.

Everything is deterministic: no randomness, no live data, no LLM.  Entries are
sorted by date, category, kind, source and reason.
"""
from __future__ import annotations

import os
from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from code.amounts import to_decimal
from code.fx import resolve_rate

HORIZON_DAYS = 90
DEFAULT_INTERVAL_DAYS = 30
# A 90-day horizon can contain three monthly paydays (e.g. 15 June, 15 July,
# 15 August); capping at two silently drops the third salary from the ledger.
MAX_INCOME_OCCURRENCES = 3
# A monthly budget needs at least two observed months: one month is a one-off.
MIN_BUDGET_MONTHS = 2
# A calendar month is charged as 30 days when prorating the request month.
PRORATION_DAYS_PER_MONTH = 30
# Opt-in for the corrected first-bucket arithmetic (CR-13 D1/D2).  Both rules
# lower the day-0 charge, which raises the amount declared safe; the default
# keeps the conservative charge so the G1 safety gate stays at 0/0/0.
FORECAST_CORRECTIONS_ENV = "BUY_WAIT_FORECAST_CORRECTIONS"
# Latest calendar day a monthly budget boundary may land on.
MAX_BUCKET_DAY = 28

# Committed / essential categories the ledger may project forward.
FORECASTABLE_EXPENSE_CATEGORIES = frozenset(
    {
        "rent",
        "utilities",
        "groceries",
        "transport",
        "healthcare",
        "education",
        "family_support",
        "housing",
        "work_expense",
        "debt_repayment",
        "cloud_storage",
        "delivery_membership",
        "gym",
        "music_subscription",
        "streaming",
    }
)

# Lifestyle spending: never extrapolated into the future, even when it recurs.
DISCRETIONARY_CATEGORIES = frozenset({"dining", "entertainment", "shopping"})

# Event types that can carry committed expense history.  A `debt_payment` row is a
# cash event but not a *expense* history row.
EXPENSE_EVENT_TYPES = frozenset({"expense", "subscription"})

# Only a regular salary can be projected as income.
PRIMARY_INCOME_CATEGORIES = frozenset({"salary"})

# Description tokens that disqualify an income stream from projection.  A
# stream is one (category, description) history, so "Payroll credit" stays
# eligible while "Weekly app earnings" or "Final employer payroll" never does.
EXCLUDED_INCOME_DESCRIPTION_TOKENS = (
    "gig",
    "platform",
    "marketplace",
    "app earnings",
    "freelance",
    "contract",
    "consulting",
    "invoice",
    "retainer",
    "commission",
    "bonus",
    "arrears",
    "prize",
    "seasonal",
    "temporary",
    "final",
    "prorated",
    "previous employer",
    "new employer",
    # Secondary household income is explicitly not projected.
    "second",
    "secondary",
)

# User-level evidence that an income source has ended.
INCOME_END_FLAGS = frozenset(
    {"employment_ended", "contract_ended", "household_ended", "partial_income_ended"}
)

# A salary history whose most recent row is terminal means the stream ended:
# earlier rows with a generic description must not be projected past it.
TERMINAL_INCOME_DESCRIPTION_TOKENS = (
    "final",
    "previous employer",
    "employment ended",
    "contract ended",
    "household ended",
)

_EXPENSE_END_FACT_KINDS = frozenset({"ended", "removed", "stop", "stopped", "cancel", "cancelled"})
_EXPENSE_AMOUNT_FACT_KINDS = frozenset({"new_recurring", "amount_change", "amount", "replacement"})
_EXPENSE_INCREASE_FACT_KINDS = frozenset({"increase", "percent_increase"})

_EXCLUDED_HISTORY_STATUSES = frozenset({"cancelled", "canceled", "failed", "unrealized"})


@dataclass(frozen=True)
class ForecastEntry:
    """One projected cash movement, signed in the home currency."""

    date: date
    amount: Decimal
    category: str
    kind: str  # "expense" | "income"
    source: str  # "history" | "evidence"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "amount": format(self.amount, "f"),
            "category": self.category,
            "kind": self.kind,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass
class ForecastResult:
    """Projected entries plus the warnings and stream choice behind them."""

    entries: list[ForecastEntry] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    income_stream: str | None = None


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:  # NaN
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _norm(value) -> str:
    return _text(value).lower()


def _as_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, (int, float)):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _decimal(value) -> Decimal | None:
    return to_decimal(_text(value))


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _median_decimal(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _cv(values: list[Decimal]) -> Decimal:
    """Coefficient of variation, deterministic and outlier-sensitive."""
    count = len(values)
    if count < 2:
        return Decimal(1)
    mean = sum(values, Decimal(0)) / Decimal(count)
    if mean == 0:
        return Decimal(0) if all(value == 0 for value in values) else Decimal(1)
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / Decimal(count - 1)
    return variance.sqrt() / abs(mean)


def _event_day(event: dict) -> date | None:
    return _as_date(event.get("settlement_date") or event.get("event_date"))


def _counts_as_history(event: dict, *, income: bool) -> bool:
    status = _norm(event.get("status"))
    direction = _norm(event.get("direction"))
    if direction == "non_cash" or status in _EXCLUDED_HISTORY_STATUSES:
        return False
    if status not in {"settled", "scheduled", "pending"}:
        return False
    if direction != ("credit" if income else "debit"):
        return False
    if status == "pending" and not income:
        # A pending debit is reserved cash, so it is valid expense evidence.
        return True
    if status == "pending" and income:
        return False
    return True


def _warning(flag: str, source: str, detail: str, category: str = "") -> dict:
    payload = {"flag": flag, "detail": detail}
    if source:
        payload["source_message_id"] = source
    if category:
        payload["category"] = category
    return payload


def _forecast_corrections_enabled() -> bool:
    """Whether the opt-in corrected first-bucket arithmetic is enabled.

    Rules 2 and 3 of CR-13 (charge only ``budget - month_to_date``, prorate on
    the real month length excluding the request day) each lower the day-0 charge
    and therefore raise the amount declared safe.  They stay off unless
    :data:`FORECAST_CORRECTIONS_ENV` asks for them: the shipped default is the
    conservative charge that keeps the G1 safety gate at 0/0/0.  The variable is
    read per call, so a test or a caller can flip it without a reimport.
    """
    value = str(os.environ.get(FORECAST_CORRECTIONS_ENV) or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _fact_value(fact, key: str):
    if isinstance(fact, dict):
        return fact.get(key)
    return getattr(fact, key, None)


# ---------------------------------------------------------------------------
# Expense projection: one monthly budget per committed category
# ---------------------------------------------------------------------------


def _first_day_of_next_month(day: date) -> date:
    """First day of the calendar month after ``day``."""
    year = day.year + (1 if day.month == 12 else 0)
    month = 1 if day.month == 12 else day.month + 1
    return date(year, month, 1)


def _next_bucket_start(day: date) -> date:
    """Next month boundary inside the horizon (``min(day, 28)`` like the plan)."""
    year = day.year + (1 if day.month == 12 else 0)
    month = 1 if day.month == 12 else day.month + 1
    return date(year, month, min(day.day, MAX_BUCKET_DAY))


def _eligible_expense_rows(
    events,
    *,
    categories,
    home_currency: str,
    fx_rows,
    warnings: list[dict] | None = None,
    warn_on_fx: bool = False,
) -> list[tuple[str, date, Decimal]]:
    """Countable expense rows of the projected categories, in home currency.

    History and in-window coverage share this one eligibility rule: expense or
    subscription rows of the category, settled/scheduled/pending debits (a pending
    debit is reserved cash), a positive amount, and a known FX rate on the row's
    own date.  An unconvertible amount is skipped and, for history, reported.
    """
    rows: list[tuple[str, date, Decimal]] = []
    for event in events:
        category = _norm(event.get("category"))
        if category not in categories:
            continue
        if _norm(event.get("event_type")) not in EXPENSE_EVENT_TYPES:
            continue
        if not _counts_as_history(event, income=False):
            continue
        event_day = _event_day(event)
        if event_day is None:
            continue
        amount = _decimal(event.get("amount"))
        if amount is None or amount <= 0:
            continue
        rate = resolve_rate(
            fx_rows, _text(event.get("currency")), home_currency, event_day.isoformat()
        )["rate"]
        if rate is None:
            if warn_on_fx and warnings is not None:
                warnings.append(
                    _warning(
                        "FORECAST_HISTORY_FX_UNKNOWN",
                        "",
                        f"skipped {category} observation on {event_day.isoformat()}",
                        category,
                    )
                )
            continue
        rows.append((category, event_day, abs(amount * rate)))
    return rows


def _monthly_budget_history(
    events,
    req_date: date,
    *,
    home_currency: str,
    fx_rows,
    warnings: list[dict],
) -> dict[str, Decimal]:
    """Median monthly home-currency total per forecastable category.

    Only *complete* months before the request count: the request month is still
    running, so the days before the request date are not a monthly total and
    including them would drag the median down and charge the same spend twice
    (once here, once as coverage of the first bucket).  A month without a
    convertible amount contributes no total (no zero months are invented), and a
    category needs at least :data:`MIN_BUDGET_MONTHS` observed months before it
    becomes a budget.
    """
    projected = FORECASTABLE_EXPENSE_CATEGORIES - DISCRETIONARY_CATEGORIES
    request_month = (req_date.year, req_date.month)
    monthly: dict[str, dict[tuple[int, int], Decimal]] = {}
    for category, event_day, amount in _eligible_expense_rows(
        events,
        categories=projected,
        home_currency=home_currency,
        fx_rows=fx_rows,
        warnings=warnings,
        warn_on_fx=True,
    ):
        if event_day >= req_date:
            continue
        month_key = (event_day.year, event_day.month)
        if month_key == request_month:
            # The request month is incomplete; it is never a budget month.
            continue
        bucket = monthly.setdefault(category, {})
        bucket[month_key] = bucket.get(month_key, Decimal(0)) + amount

    budgets: dict[str, Decimal] = {}
    for category, months in monthly.items():
        if len(months) < MIN_BUDGET_MONTHS:
            continue
        budgets[category] = _median_decimal(sorted(months.values()))
    return budgets


def _real_in_window_expenses(
    events,
    req_date: date,
    window_end: date,
    *,
    categories,
    home_currency: str,
    fx_rows,
) -> dict[str, list[tuple[date, Decimal]]]:
    """Countable real rows inside the window, per budgeted category."""
    real: dict[str, list[tuple[date, Decimal]]] = {}
    for category, event_day, amount in _eligible_expense_rows(
        events,
        categories=categories,
        home_currency=home_currency,
        fx_rows=fx_rows,
    ):
        if not (req_date <= event_day <= window_end):
            continue
        real.setdefault(category, []).append((event_day, amount))
    return real


def _month_to_date_expenses(
    events,
    req_date: date,
    *,
    categories,
    home_currency: str,
    fx_rows,
) -> dict[str, Decimal]:
    """Countable spend already posted in the request month, before the request.

    The first bucket is the unspent remainder of that month, so a row from the
    request month is subtracted from the budget instead of being projected a
    second time.  It is strictly before ``req_date``, so it never overlaps the
    in-window coverage that nets the bucket a second time.
    """
    month_start = req_date.replace(day=1)
    spent: dict[str, Decimal] = {}
    for category, event_day, amount in _eligible_expense_rows(
        events,
        categories=categories,
        home_currency=home_currency,
        fx_rows=fx_rows,
    ):
        if not (month_start <= event_day < req_date):
            continue
        spent[category] = spent.get(category, Decimal(0)) + amount
    return spent


def _monthly_budget_forecast(
    events,
    req_date: date,
    window_end: date,
    *,
    home_currency: str,
    fx_rows,
    budgets: dict[str, Decimal],
    has_projected_income: bool,
) -> list[ForecastEntry]:
    """Post each category's monthly budget on the horizon's month boundaries.

    The first bucket starts on the request date and is prorated to the remaining
    days of the request month only when a salary is projected in the window.
    A bucket is net of the real countable rows it already contains.  The default
    first bucket is the conservative pre-CR-13 charge; with
    :data:`FORECAST_CORRECTIONS_ENV` set it keeps only the unspent remainder of
    its month (``full - month_to_date``) and prorates on the month's real length,
    never a negative amount.
    """
    if not budgets:
        return []
    real = _real_in_window_expenses(
        events,
        req_date,
        window_end,
        categories=set(budgets),
        home_currency=home_currency,
        fx_rows=fx_rows,
    )
    corrections = _forecast_corrections_enabled()
    month_to_date = (
        _month_to_date_expenses(
            events,
            req_date,
            categories=set(budgets),
            home_currency=home_currency,
            fx_rows=fx_rows,
        )
        if corrections
        else {}
    )
    days_left = (_first_day_of_next_month(req_date) - req_date).days
    days_in_month = monthrange(req_date.year, req_date.month)[1]

    entries: list[ForecastEntry] = []
    cursor = req_date
    first = True
    while cursor <= window_end:
        bucket_end = _next_bucket_start(cursor)
        for category in sorted(budgets):
            amount = budgets[category]
            if first and has_projected_income:
                if corrections:
                    # `days_left` counts the request day itself; the request day
                    # is not projected again, so the remaining share of the
                    # month is (days_left - 1) over the month's real length.
                    prorated = (
                        amount * Decimal(days_left - 1) / Decimal(days_in_month)
                    )
                else:
                    # Conservative default: a fixed 30-day month and an
                    # inclusive day count.  The guard keeps the first bucket at
                    # or below one month's budget.
                    prorated = (
                        amount
                        * Decimal(days_left + 1)
                        / Decimal(PRORATION_DAYS_PER_MONTH)
                    )
                amount = min(amount, prorated)
            if first and corrections:
                # The budget is the whole month; the rows already posted this
                # month are part of it, not an extra charge. The unspent
                # remainder can never be negative.
                amount = max(
                    Decimal(0), amount - month_to_date.get(category, Decimal(0))
                )
            covered = sum(
                (value for day, value in real.get(category, ()) if cursor <= day < bucket_end),
                Decimal(0),
            )
            uncovered = max(Decimal(0), amount - covered)
            if uncovered <= 0:
                continue
            entries.append(
                ForecastEntry(
                    date=cursor,
                    amount=-uncovered,
                    category=category,
                    kind="expense",
                    source="history",
                    reason="monthly_accrual",
                )
            )
        first = False
        cursor = bucket_end
    return entries


# ---------------------------------------------------------------------------
# Income projection
# ---------------------------------------------------------------------------


def _income_stream_is_eligible(description: str) -> bool:
    return not any(token in description for token in EXCLUDED_INCOME_DESCRIPTION_TOKENS)


def _projected_income_dates(anchor, req_date, window_end, *, interval, pay_day):
    """Salary dates strictly after ``anchor`` and inside the request window.

    A monthly payday is a *day of the month*, not a day count: stepping a
    15th payday by 30 days lands on the 14th, then the 13th, and the
    projection slowly drifts away from the salary it is modelling. When the
    stream pays on a stable day of month, project that calendar day.
    """
    if pay_day is None:
        day = anchor + timedelta(days=interval)
        while day < req_date:
            day += timedelta(days=interval)
        while day <= window_end:
            yield day
            day += timedelta(days=interval)
        return
    year, month = anchor.year, anchor.month
    step = 0
    while True:
        step += 1
        total = month - 1 + step
        target_year = year + total // 12
        target_month = total % 12 + 1
        day = min(pay_day, monthrange(target_year, target_month)[1])
        candidate = date(target_year, target_month, day)
        if candidate > window_end:
            return
        if candidate >= req_date:
            yield candidate


def _income_forecast(
    events,
    req_date: date,
    window_end: date,
    *,
    user_id: str,
    home_currency: str,
    fx_rows,
    evidence_flags,
    warnings: list[dict],
) -> tuple[list[ForecastEntry], str | None]:
    if set(evidence_flags or ()) & INCOME_END_FLAGS:
        warnings.append(
            _warning(
                "INCOME_STREAM_ENDED_BY_EVIDENCE",
                "",
                "user evidence marks an income source as ended; no salary is projected",
            )
        )
        return [], None

    history: list[dict] = []
    for event in events:
        if _norm(event.get("event_type")) != "income":
            continue
        if _norm(event.get("category")) not in PRIMARY_INCOME_CATEGORIES:
            continue
        if not _counts_as_history(event, income=True):
            continue
        event_day = _event_day(event)
        if event_day is None or event_day >= req_date:
            continue
        description = _norm(event.get("description"))
        amount = _decimal(event.get("amount"))
        rate = None
        if amount is not None and amount > 0:
            rate = resolve_rate(
                fx_rows, _text(event.get("currency")), home_currency, event_day.isoformat()
            )["rate"]
        if rate is None:
            # A cadence date is still evidence that the stream was paid, even
            # when the amount cannot be converted; keep the date, drop the amount.
            warnings.append(
                _warning(
                    "FORECAST_HISTORY_FX_UNKNOWN",
                    "",
                    f"skipped income observation on {event_day.isoformat()}",
                    "salary",
                )
            )
        history.append(
            {
                "date": event_day,
                "description": description,
                "event_id": _text(event.get("event_id")),
                "amount": abs(amount * rate) if rate is not None else None,
            }
        )
    if not history:
        return [], None

    latest_date = max(row["date"] for row in history)
    if any(
        row["date"] == latest_date
        and any(token in row["description"] for token in TERMINAL_INCOME_DESCRIPTION_TOKENS)
        for row in history
    ):
        warnings.append(
            _warning(
                "INCOME_STREAM_ENDED",
                "",
                f"the latest salary row ({latest_date.isoformat()}) is a terminal payment",
            )
        )
        return [], None

    streams: dict[str, list[dict]] = {}
    for row in history:
        streams.setdefault(row["description"], []).append(row)

    candidates = []
    for description, rows in streams.items():
        if not _income_stream_is_eligible(description):
            continue
        dates = sorted(row["date"] for row in rows)
        if len(dates) < 2:
            continue
        amounts = [row["amount"] for row in rows if row["amount"] is not None]
        if not amounts:
            continue
        intervals = [
            (dates[index + 1] - dates[index]).days for index in range(len(dates) - 1)
        ]
        cadence = _cv([Decimal(interval) for interval in intervals])
        stability = _cv(amounts)
        median_amount = _median_decimal(amounts)
        candidates.append(
            (
                round(cadence, 6),
                round(stability, 6),
                -median_amount,
                description,
                rows,
                dates,
                intervals,
            )
        )
    if not candidates:
        return [], None

    _, _, _, description, rows, dates, intervals = sorted(candidates)[0]
    interval = _median_int(intervals)
    if interval <= 0:
        interval = DEFAULT_INTERVAL_DAYS

    # A monthly salary pays on a stable day of month; only trust the calendar
    # reading when the observations really do cluster on one day.
    pay_day = None
    if 28 <= interval <= 31:
        doms = sorted(row["date"].day for row in rows)
        candidate_day = doms[len(doms) // 2]
        if sum(1 for day in doms if abs(day - candidate_day) <= 3) >= 0.7 * len(doms):
            pay_day = candidate_day

    latest = sorted(
        (row for row in rows if row["amount"] is not None),
        key=lambda row: (row["date"], row["event_id"]),
    )[-1]
    amount = latest["amount"]

    # Real countable salary rows already committed inside the window: they fill
    # part of the occurrence cap and anchor the projection after their latest date.
    real_dates: list[date] = []
    for event in events:
        if _norm(event.get("category")) not in PRIMARY_INCOME_CATEGORIES:
            continue
        if _norm(event.get("event_type")) != "income":
            continue
        real_description = _norm(event.get("description"))
        if not _income_stream_is_eligible(real_description):
            continue
        event_day = _event_day(event)
        if event_day is None or not (req_date <= event_day <= window_end):
            continue
        if not _counts_as_history(event, income=True):
            continue
        real_dates.append(event_day)
    existing = set(real_dates)

    remaining = MAX_INCOME_OCCURRENCES - len(real_dates)
    if remaining <= 0:
        return [], (description or None)

    entries: list[ForecastEntry] = []
    anchor = max(max(dates), max(real_dates)) if real_dates else max(dates)
    emitted = 0
    for next_date in _projected_income_dates(
        anchor, req_date, window_end, interval=interval, pay_day=pay_day
    ):
        if emitted >= remaining:
            break
        if next_date in existing:
            continue
        entries.append(
            ForecastEntry(
                date=next_date,
                amount=amount,
                category="salary",
                kind="income",
                source="history",
                reason="primary_income_stream",
            )
        )
        emitted += 1
    return entries, (description or None)


# ---------------------------------------------------------------------------
# Evidence expense facts
# ---------------------------------------------------------------------------


def _apply_expense_facts(
    entries: list[ForecastEntry],
    facts,
    *,
    window_end: date,
    home_currency: str,
    fx_rows,
    warnings: list[dict],
) -> list[ForecastEntry]:
    """Move forecast rows on/after a fact's effective date; never guess."""
    if not facts:
        return entries
    updated = list(entries)
    for fact in facts:
        category = _norm(_fact_value(fact, "category"))
        kind = _norm(_fact_value(fact, "kind"))
        source_id = _text(_fact_value(fact, "source_message_id"))
        effective = _as_date(_fact_value(fact, "effective_date"))
        if not category:
            warnings.append(
                _warning("EXPENSE_FACT_AMBIGUOUS", source_id, "fact has no category")
            )
            continue
        targeted = [
            entry
            for entry in updated
            if entry.kind == "expense" and entry.category == category
        ]
        if not targeted:
            warnings.append(
                _warning(
                    "EXPENSE_FACT_NOT_APPLIED",
                    source_id,
                    f"no forecast row for {category}",
                    category,
                )
            )
            continue
        if effective is None:
            warnings.append(
                _warning(
                    "EXPENSE_FACT_AMBIGUOUS",
                    source_id,
                    "fact has no effective date; forecast left unchanged",
                    category,
                )
            )
            continue
        if effective > window_end:
            # Outside the forecast horizon: cannot affect this request.
            continue
        if kind in _EXPENSE_END_FACT_KINDS:
            updated = [
                entry
                for entry in updated
                if not (
                    entry.kind == "expense"
                    and entry.category == category
                    and entry.date >= effective
                )
            ]
            continue
        if kind in _EXPENSE_INCREASE_FACT_KINDS:
            percent = _decimal(_fact_value(fact, "percent_change"))
            if percent is None:
                warnings.append(
                    _warning(
                        "EXPENSE_FACT_AMBIGUOUS",
                        source_id,
                        "increase fact has no percent change; forecast left unchanged",
                        category,
                    )
                )
                continue
            factor = Decimal(1) + percent / Decimal(100)
            if factor <= 0:
                warnings.append(
                    _warning(
                        "EXPENSE_FACT_AMBIGUOUS",
                        source_id,
                        "percent change would remove the expense; ignored",
                        category,
                    )
                )
                continue
            updated = [
                replace(
                    entry,
                    amount=entry.amount * factor,
                    source="evidence",
                    reason=f"expense_fact:increase:{format(percent, 'f')}%",
                )
                if entry.kind == "expense"
                and entry.category == category
                and entry.date >= effective
                else entry
                for entry in updated
            ]
            continue
        if kind in _EXPENSE_AMOUNT_FACT_KINDS:
            amount = _decimal(_fact_value(fact, "amount"))
            if amount is None or amount <= 0:
                warnings.append(
                    _warning(
                        "EXPENSE_FACT_AMBIGUOUS",
                        source_id,
                        "new recurring amount is missing; forecast left unchanged",
                        category,
                    )
                )
                continue
            currency = _text(_fact_value(fact, "currency")).upper()
            if not currency:
                warnings.append(
                    _warning(
                        "EXPENSE_FACT_AMBIGUOUS",
                        source_id,
                        "new recurring amount has no currency; forecast left unchanged",
                        category,
                    )
                )
                continue
            rate = resolve_rate(
                fx_rows, currency, home_currency, effective.isoformat()
            )["rate"]
            if rate is None:
                warnings.append(
                    _warning(
                        "EXPENSE_FACT_AMBIGUOUS",
                        source_id,
                        f"no {currency}->{home_currency} rate on {effective.isoformat()}",
                        category,
                    )
                )
                continue
            new_amount = -abs(amount * rate)
            updated = [
                replace(
                    entry,
                    amount=new_amount,
                    source="evidence",
                    reason=f"expense_fact:{kind}",
                )
                if entry.kind == "expense"
                and entry.category == category
                and entry.date >= effective
                else entry
                for entry in updated
            ]
            continue
        warnings.append(
            _warning(
                "EXPENSE_FACT_AMBIGUOUS",
                source_id,
                f"unknown expense fact kind {kind!r}; forecast left unchanged",
                category,
            )
        )
    return updated


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_forecast(
    events,
    req_date,
    *,
    user_id: str | None = None,
    home_currency: str = "USD",
    fx_rows=None,
    recurrence_flags=None,
    suppressed_categories=None,
    protected_categories=None,
    category_overrides=None,
    evidence_flags=None,
    expense_facts=None,
) -> ForecastResult:
    """Project committed cash rows for ``[req_date, req_date + 90]``.

    ``events`` is expected to be the user's normalized event rows (the oracle
    filters and dedups before calling).  Every policy input is explicit so the
    result is reproducible and unit-testable without pandas.

    ``suppressed_categories`` removes a stopped category's monthly budget;
    ``category_overrides`` replaces a ``reduce_to`` category's monthly budget with
    the new amount and keeps the category.  A protected category is never
    silently suppressed.  Income is projected first, because the request month is
    only prorated when a salary is actually projected in the window.

    ``recurrence_flags`` is accepted for caller compatibility (the oracle passes
    it) but no longer selects what is projected: a monthly budget is evidence of
    two observed months, not of a per-transaction cadence.
    """
    request_day = _as_date(req_date)
    if request_day is None:
        raise ValueError(f"req_date must be an ISO date, got {req_date!r}")
    events = list(events or ())
    fx_rows = list(fx_rows or ())
    if user_id is None:
        user_id = next(
            (_text(event.get("user_id")) for event in events if _text(event.get("user_id"))),
            "",
        )
    else:
        user_id = _text(user_id)
    home_currency = _text(home_currency) or "USD"
    window_end = request_day + timedelta(days=HORIZON_DAYS)
    suppressed = {_norm(category) for category in (suppressed_categories or ()) if _norm(category)}
    protected = {_norm(category) for category in (protected_categories or ()) if _norm(category)}
    overrides: dict[str, Decimal] = {}
    for category, value in dict(category_overrides or {}).items():
        name = _norm(category)
        amount = _decimal(value)
        if name and amount is not None and amount >= 0:
            overrides[name] = abs(amount)

    warnings: list[dict] = []
    income_entries, income_stream = _income_forecast(
        events,
        request_day,
        window_end,
        user_id=user_id,
        home_currency=home_currency,
        fx_rows=fx_rows,
        evidence_flags=evidence_flags,
        warnings=warnings,
    )

    budgets = _monthly_budget_history(
        events,
        request_day,
        home_currency=home_currency,
        fx_rows=fx_rows,
        warnings=warnings,
    )
    for category in sorted(budgets):
        if category not in suppressed:
            continue
        if category in protected:
            warnings.append(
                _warning(
                    "PROTECTED_CATEGORY_NOT_SUPPRESSED",
                    "",
                    f"{category} is protected; the suppression was ignored",
                    category,
                )
            )
            continue
        del budgets[category]
    for category, amount in overrides.items():
        # A reduce_to sets the forward run-rate; without a budget there is nothing
        # to move, and the change stays a no-op (the ledger identity check drops it).
        if category in budgets:
            budgets[category] = amount

    # A spending-change override wins over an evidence fact for the same category:
    # the user's explicit plan change is the committed spend, so the fact is
    # reported instead of silently applied on top.
    applicable_facts: list[dict] = []
    for fact in expense_facts or ():
        category = _norm(_fact_value(fact, "category"))
        if category and category in overrides and category in budgets:
            warnings.append(
                _warning(
                    "EXPENSE_FACT_OVERRIDDEN_BY_SPENDING_CHANGE",
                    _text(_fact_value(fact, "source_message_id")),
                    f"spending-change override for {category} wins over the expense fact",
                    category,
                )
            )
            continue
        applicable_facts.append(fact)

    expense_entries = _monthly_budget_forecast(
        events,
        request_day,
        window_end,
        home_currency=home_currency,
        fx_rows=fx_rows,
        budgets=budgets,
        has_projected_income=any(entry.kind == "income" for entry in income_entries),
    )
    expense_entries = _apply_expense_facts(
        expense_entries,
        applicable_facts,
        window_end=window_end,
        home_currency=home_currency,
        fx_rows=fx_rows,
        warnings=warnings,
    )

    entries = expense_entries + income_entries
    entries.sort(key=lambda entry: (entry.date, entry.category, entry.kind, entry.source, entry.reason))
    return ForecastResult(entries=entries, warnings=warnings, income_stream=income_stream)


__all__ = [
    "DISCRETIONARY_CATEGORIES",
    "EXPENSE_EVENT_TYPES",
    "EXCLUDED_INCOME_DESCRIPTION_TOKENS",
    "FORECAST_CORRECTIONS_ENV",
    "FORECASTABLE_EXPENSE_CATEGORIES",
    "ForecastEntry",
    "ForecastResult",
    "HORIZON_DAYS",
    "INCOME_END_FLAGS",
    "MAX_INCOME_OCCURRENCES",
    "MIN_BUDGET_MONTHS",
    "PRIMARY_INCOME_CATEGORIES",
    "build_forecast",
]
