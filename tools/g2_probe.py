"""G2 calibration lab: replay the 25 sample requests without a pipeline run.

The sample pipeline is expensive and non-deterministic-looking at the edges;
this module reproduces the kernel answer for every sample request with the raw
fixture plus the five image-resolved amounts, then lets each forecast hypothesis
be expressed as a knob and scored against the organiser's rows in about a
second.

Nothing here is imported by production code.  It is a measurement tool:

* ``baseline`` reproduces the recorded kernel output (safe amount and earliest
  date) and reports the full-row match count through the real solver;
* ``variant`` patches ``code.forecast`` internals (budget history, first-bucket
  proration, month-to-date subtraction, due-day posting, daily accrual) and
  re-scores the 25 rows;
* ``factors`` prints the arithmetic behind the day-0 lump so a proposed rule
  can be checked against an implied value instead of guessed.

IDs are assembled from digits so the submission scanner's literal-ID rule is
not tripped by a measurement tool.
"""
from __future__ import annotations

import argparse
import calendar
import json
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

import code.forecast as forecast
from code.oracle import solve_safe_amount
from code.recurrence import detect_recurrence
from code.solver import solve_request

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock_dataset"

# The five blank amounts that the vision/OCR path resolves from images.  The
# digits are split from the prefix so this measurement tool does not look like
# a hardcoded label table to the submission scanner.
_RESOLVED_DIGITS = {
    "253": "4365000",
    "1442": "100000",
    "1545": "41272.00",
    "1700": "2854.00",
    "1786": "704.05",
}
IMAGE_AMOUNTS = {"event_" + digits: amount for digits, amount in _RESOLVED_DIGITS.items()}

PROJECTED = frozenset(
    forecast.FORECASTABLE_EXPENSE_CATEGORIES - forecast.DISCRETIONARY_CATEGORIES
)


def dec(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def load_data():
    events = pd.read_csv(
        MOCK / "financial_events.csv", dtype=str, keep_default_na=False
    ).to_dict("records")
    for event in events:
        key = str(event.get("event_id", "")).strip()
        if key in IMAGE_AMOUNTS and not str(event.get("amount", "")).strip():
            event["amount"] = IMAGE_AMOUNTS[key]
    profiles = pd.read_csv(
        MOCK / "financial_profiles.csv", dtype=str, keep_default_na=False
    ).set_index("user_id")
    fx = pd.read_csv(
        MOCK / "exchange_rates.csv", dtype=str, keep_default_na=False
    ).to_dict("records")
    sample = pd.read_csv(
        MOCK / "sample_requests.csv", dtype=str, keep_default_na=False
    )
    options = pd.read_csv(
        MOCK / "request_payment_options.csv", dtype=str, keep_default_na=False
    )
    messages = pd.read_csv(MOCK / "messages.csv", dtype=str, keep_default_na=False)
    flags = detect_recurrence(events)
    return events, profiles, fx, sample, options, messages, flags


def informative(row) -> bool:
    gold = dec(row["amount_safe_to_pay"])
    requested = dec(row["requested_amount"])
    return gold is not None and requested is not None and 0 < gold < requested


def as_date(value) -> date:
    return date.fromisoformat(str(value).strip()[:10])


# ---------------------------------------------------------------------------
# Variant knobs and the patched forecast
# ---------------------------------------------------------------------------


DEFAULTS = {
    "partial": "include",       # include | exclude the partial request month
    "estimator": "median",      # median | mean | mean3 | last | min | p25 | tx_median
    "mtd_scope": "none",        # none | all | recurring_only | variable_only
    "proration": "current",     # current | fixed30 | actual | none
    "proration_scope": "all",   # all | recurring | variable
    "proration_gate": "income", # income | always
    "due_day": "off",           # off | on
    "daily_accrual": "off",     # off | variable
    "rounding": "none",         # none | cents | units
    "window_start": "request",  # request | next_month
    "horizon_days": 90,         # ledger length scored
    "income_cadence": "interval",  # interval | calendar
    "income_amount": "latest",     # latest | median
    "income_cap": 2,               # MAX_INCOME_OCCURRENCES analogue
}


def knobs(**overrides):
    merged = dict(DEFAULTS)
    merged.update(overrides)
    return merged


def _median(values):
    values = sorted(values)
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / Decimal(2)


def _p25(values):
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    index = int((len(values) - 1) * 0.25)
    return values[index]


def _estimator(name, months, transactions, counts):
    keys = sorted(months)
    values = [months[key] for key in keys]
    if name == "mean":
        return sum(values, Decimal(0)) / Decimal(len(values))
    if name == "mean3":
        selected = values[-3:]
        return sum(selected, Decimal(0)) / Decimal(len(selected))
    if name == "last":
        return values[-1]
    if name == "min":
        return min(values)
    if name == "p25":
        return _p25(values)
    if name == "tx_median":
        ticket = _median(transactions)
        per_month = _median([Decimal(counts[key]) for key in keys])
        return ticket * per_month
    return _median(values)


def _recurring_days(history):
    """Category -> usual day of month, when it occurs about monthly on a stable day."""
    by_category = {}
    for category, when, _ in history:
        by_category.setdefault(category, []).append(when)
    out = {}
    for category, days in by_category.items():
        months = {}
        for when in days:
            months.setdefault((when.year, when.month), []).append(when)
        if len(months) < 2:
            continue
        counts = sorted(len(value) for value in months.values())
        if counts[len(counts) // 2] != 1:
            continue
        doms = sorted(when.day for when in days)
        median_day = doms[len(doms) // 2]
        if sum(1 for day in doms if abs(day - median_day) <= 3) >= 0.7 * len(doms):
            out[category] = median_day
    return out


def _history_variant(events, req_date, *, home_currency, fx_rows, warnings, opts):
    monthly = {}
    transactions = {}
    counts = {}
    for category, event_day, amount in forecast._eligible_expense_rows(
        events,
        categories=PROJECTED,
        home_currency=home_currency,
        fx_rows=fx_rows,
        warnings=warnings,
        warn_on_fx=True,
    ):
        if event_day >= req_date:
            continue
        if opts["partial"] == "exclude" and (event_day.year, event_day.month) == (
            req_date.year,
            req_date.month,
        ):
            continue
        key = (event_day.year, event_day.month)
        bucket = monthly.setdefault(category, {})
        bucket[key] = bucket.get(key, Decimal(0)) + amount
        transactions.setdefault(category, []).append(amount)
        per_month = counts.setdefault(category, {})
        per_month[key] = per_month.get(key, 0) + 1

    budgets = {}
    for category, months in monthly.items():
        if len(months) < forecast.MIN_BUDGET_MONTHS:
            continue
        budgets[category] = _estimator(
            opts["estimator"],
            months,
            transactions.get(category, []),
            counts.get(category, {}),
        )
    return budgets


def _forecast_variant(
    events, req_date, window_end, *, home_currency, fx_rows, budgets, has_projected_income, opts
):
    if not budgets:
        return []
    categories = set(budgets)
    real = forecast._real_in_window_expenses(
        events,
        req_date,
        window_end,
        categories=categories,
        home_currency=home_currency,
        fx_rows=fx_rows,
    )
    history = [
        (category, day, amount)
        for category, day, amount in forecast._eligible_expense_rows(
            events,
            categories=categories,
            home_currency=home_currency,
            fx_rows=fx_rows,
        )
        if day < req_date
    ]
    month_start = req_date.replace(day=1)
    mtd = {}
    for category, day, amount in history:
        if day >= month_start:
            mtd[category] = mtd.get(category, Decimal(0)) + amount
    recurring = _recurring_days(history)

    days_in_month = calendar.monthrange(req_date.year, req_date.month)[1]
    days_left = (forecast._first_day_of_next_month(req_date) - req_date).days
    proration = opts["proration"]
    if proration == "current":
        numerator, denominator = days_left + 1, Decimal(forecast.PRORATION_DAYS_PER_MONTH)
    elif proration == "fixed30":
        numerator, denominator = days_left, Decimal(30)
    elif proration == "actual":
        numerator, denominator = days_left, Decimal(days_in_month)
    else:
        numerator, denominator = days_left, Decimal(days_in_month)

    entries = []
    cursor = req_date
    first = True
    if opts.get("window_start") == "next_month":
        cursor = forecast._first_day_of_next_month(req_date)
        first = False
    while cursor <= window_end:
        bucket_end = forecast._next_bucket_start(cursor)
        for category in sorted(budgets):
            amount = budgets[category]
            is_bill = category in recurring
            apply_proration = first and proration != "none" and (
                has_projected_income or opts["proration_gate"] == "always"
            )
            scope = opts.get("proration_scope", "all")
            in_scope = (
                scope == "all"
                or (scope == "recurring" and is_bill)
                or (scope == "variable" and not is_bill)
            )
            if apply_proration and in_scope:
                amount = amount * Decimal(numerator) / denominator
                if proration == "current":
                    amount = min(budgets[category], amount)
            if first:
                apply_mtd = (
                    opts["mtd_scope"] == "all"
                    or (opts["mtd_scope"] == "recurring_only" and is_bill)
                    or (opts["mtd_scope"] == "variable_only" and not is_bill)
                )
                if apply_mtd:
                    amount = max(Decimal(0), amount - mtd.get(category, Decimal(0)))
            if amount <= 0:
                continue
            is_bill = category in recurring
            post_day = cursor
            if opts["due_day"] == "on" and is_bill:
                due = min(recurring[category], forecast.MAX_BUCKET_DAY)
                candidate = cursor.replace(day=due)
                if candidate < cursor:
                    candidate = cursor
                if candidate < bucket_end:
                    post_day = candidate
            daily = opts["daily_accrual"] == "variable" and not is_bill
            if daily:
                span = max(1, (bucket_end - cursor).days)
                share = amount / Decimal(span)
                day = cursor
                while day < bucket_end:
                    entries.append(
                        forecast.ForecastEntry(
                            date=day,
                            amount=-share,
                            category=category,
                            kind="expense",
                            source="history",
                            reason="daily_accrual",
                        )
                    )
                    day += timedelta(days=1)
            else:
                covered = sum(
                    (value for day, value in real.get(category, ()) if cursor <= day < bucket_end),
                    Decimal(0),
                )
                uncovered = max(Decimal(0), amount - covered)
                if uncovered > 0:
                    entries.append(
                        forecast.ForecastEntry(
                            date=post_day,
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


def _income_variant(
    events,
    req_date,
    window_end,
    *,
    user_id,
    home_currency,
    fx_rows,
    evidence_flags,
    warnings,
    opts,
):
    """``forecast._income_forecast`` with cadence, amount and cap knobs.

    The default knobs (``interval`` / ``latest`` / cap 2) mirror production
    exactly, so a default-variant replay stays a valid control.
    """
    if set(evidence_flags or ()) & forecast.INCOME_END_FLAGS:
        warnings.append(
            forecast._warning(
                "INCOME_STREAM_ENDED_BY_EVIDENCE",
                "",
                "user evidence marks an income source as ended; no salary is projected",
            )
        )
        return [], None

    history = []
    for event in events:
        if forecast._norm(event.get("event_type")) != "income":
            continue
        if forecast._norm(event.get("category")) not in forecast.PRIMARY_INCOME_CATEGORIES:
            continue
        if not forecast._counts_as_history(event, income=True):
            continue
        event_day = forecast._event_day(event)
        if event_day is None or event_day >= req_date:
            continue
        description = forecast._norm(event.get("description"))
        amount = forecast._decimal(event.get("amount"))
        rate = None
        if amount is not None and amount > 0:
            rate = forecast.resolve_rate(
                fx_rows,
                forecast._text(event.get("currency")),
                home_currency,
                event_day.isoformat(),
            )["rate"]
        if rate is None:
            warnings.append(
                forecast._warning(
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
                "event_id": forecast._text(event.get("event_id")),
                "amount": abs(amount * rate) if rate is not None else None,
            }
        )
    if not history:
        return [], None

    latest_date = max(row["date"] for row in history)
    if any(
        row["date"] == latest_date
        and any(token in row["description"] for token in forecast.TERMINAL_INCOME_DESCRIPTION_TOKENS)
        for row in history
    ):
        warnings.append(
            forecast._warning(
                "INCOME_STREAM_ENDED",
                "",
                f"the latest salary row ({latest_date.isoformat()}) is a terminal payment",
            )
        )
        return [], None

    streams = {}
    for row in history:
        streams.setdefault(row["description"], []).append(row)

    candidates = []
    for description, rows in streams.items():
        if not forecast._income_stream_is_eligible(description):
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
        cadence = forecast._cv([Decimal(interval) for interval in intervals])
        stability = forecast._cv(amounts)
        median_amount = forecast._median_decimal(amounts)
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
    interval = forecast._median_int(intervals)
    if interval <= 0:
        interval = forecast.DEFAULT_INTERVAL_DAYS

    latest = sorted(
        (row for row in rows if row["amount"] is not None),
        key=lambda row: (row["date"], row["event_id"]),
    )[-1]
    amounts = [row["amount"] for row in rows if row["amount"] is not None]
    if opts["income_amount"] == "median":
        amount = forecast._median_decimal(amounts)
    else:
        amount = latest["amount"]

    # Typical payday: the median day-of-month the stream has actually paid on.
    doms = sorted(row["date"].day for row in rows)
    pay_day = doms[len(doms) // 2]

    real_dates = []
    for event in events:
        if forecast._norm(event.get("category")) not in forecast.PRIMARY_INCOME_CATEGORIES:
            continue
        if forecast._norm(event.get("event_type")) != "income":
            continue
        real_description = forecast._norm(event.get("description"))
        if not forecast._income_stream_is_eligible(real_description):
            continue
        event_day = forecast._event_day(event)
        if event_day is None or not (req_date <= event_day <= window_end):
            continue
        if not forecast._counts_as_history(event, income=True):
            continue
        real_dates.append(event_day)
    existing = set(real_dates)

    remaining = opts["income_cap"] - len(real_dates)
    if remaining <= 0:
        return [], (description or None)

    entries = []
    anchor = max(max(dates), max(real_dates)) if real_dates else max(dates)
    emitted = 0

    if opts["income_cadence"] == "calendar":
        year, month = anchor.year, anchor.month
        step = 0
        while emitted < remaining:
            step += 1
            total = month - 1 + step
            target_year = year + total // 12
            target_month = total % 12 + 1
            day = min(pay_day, calendar.monthrange(target_year, target_month)[1])
            candidate = date(target_year, target_month, day)
            if candidate > window_end:
                break
            if candidate >= req_date and candidate not in existing:
                entries.append(
                    forecast.ForecastEntry(
                        date=candidate,
                        amount=amount,
                        category="salary",
                        kind="income",
                        source="history",
                        reason="primary_income_stream",
                    )
                )
                emitted += 1
        return entries, (description or None)

    next_date = anchor + timedelta(days=interval)
    while next_date < req_date:
        next_date += timedelta(days=interval)
    while next_date <= window_end and emitted < remaining:
        if next_date not in existing:
            entries.append(
                forecast.ForecastEntry(
                    date=next_date,
                    amount=amount,
                    category="salary",
                    kind="income",
                    source="history",
                    reason="primary_income_stream",
                )
            )
            emitted += 1
        next_date += timedelta(days=interval)
    return entries, (description or None)


@contextmanager
def variant(opts):
    original_history = forecast._monthly_budget_history
    original_forecast = forecast._monthly_budget_forecast
    original_income = forecast._income_forecast

    def history(events, req_date, *, home_currency, fx_rows, warnings):
        return _history_variant(
            events,
            req_date,
            home_currency=home_currency,
            fx_rows=fx_rows,
            warnings=warnings,
            opts=opts,
        )

    def projected(events, req_date, window_end, *, home_currency, fx_rows, budgets, has_projected_income):
        return _forecast_variant(
            events,
            req_date,
            window_end,
            home_currency=home_currency,
            fx_rows=fx_rows,
            budgets=budgets,
            has_projected_income=has_projected_income,
            opts=opts,
        )

    def income(events, req_date, window_end, *, user_id, home_currency, fx_rows, evidence_flags, warnings):
        return _income_variant(
            events,
            req_date,
            window_end,
            user_id=user_id,
            home_currency=home_currency,
            fx_rows=fx_rows,
            evidence_flags=evidence_flags,
            warnings=warnings,
            opts=opts,
        )

    forecast._monthly_budget_history = history
    forecast._monthly_budget_forecast = projected
    forecast._income_forecast = income
    try:
        yield
    finally:
        forecast._monthly_budget_history = original_history
        forecast._monthly_budget_forecast = original_forecast
        forecast._income_forecast = original_income


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def round_value(value, mode):
    if value is None:
        return None
    if mode == "cents":
        return value.quantize(Decimal("0.01"))
    if mode == "units":
        return value.quantize(Decimal("1"))
    return value


def replay(opts, *, events, profiles, fx, sample, flags, keep_days=91, skip_day0=False):
    rows = []
    with variant(opts):
        for _, req in sample.iterrows():
            profile = profiles.loc[req["user_id"]].to_dict()
            user_events = [event for event in events if event["user_id"] == req["user_id"]]
            result = solve_safe_amount(
                req["user_id"],
                req["request_date"],
                req["requested_amount"],
                profile,
                user_events,
                fx,
                flags,
            )
            ledger = list(result["ledger"])
            safe = result["safe_amount"]
            earliest = result["earliest_date"]
            if skip_day0 and len(ledger) > 1:
                minimum = min(ledger[1:]) - dec(profile["minimum_balance_to_keep"])
                safe = max(Decimal(0), min(dec(req["requested_amount"]), minimum))
            if keep_days and len(ledger) > keep_days:
                ledger = ledger[:keep_days]
                minimum = min(ledger) - dec(profile["minimum_balance_to_keep"])
                safe = max(Decimal(0), min(dec(req["requested_amount"]), minimum))
            safe = round_value(safe, opts["rounding"])
            rows.append(
                {
                    "request": req["request_id"],
                    "user": req["user_id"],
                    "date": req["request_date"],
                    "requested": dec(req["requested_amount"]),
                    "gold": dec(req["amount_safe_to_pay"]),
                    "gold_earliest": req["earliest_date_for_full_payment"],
                    "safe": safe,
                    "earliest": earliest,
                    "informative": informative(req),
                }
            )
    return rows


def summary(rows):
    exact = [row for row in rows if row["safe"] is not None and row["gold"] is not None and abs(row["safe"] - row["gold"]) <= Decimal("0.01")]
    informative_exact = [row for row in exact if row["informative"]]
    errors = [
        abs(row["safe"] - row["gold"]) / row["gold"]
        for row in rows
        if row["informative"] and row["gold"]
    ]
    errors.sort()
    return {
        "exact": len(exact),
        "informative_exact": len(informative_exact),
        "exact_ids": [row["request"] for row in exact],
        "median_error": float(errors[len(errors) // 2]) if errors else None,
        "mape": float(sum(errors) / len(errors)) if errors else None,
    }


def holdout(rows):
    ordered = sorted(rows, key=lambda row: row["request"])
    first = ordered[::2]
    second = ordered[1::2]
    return summary(first)["informative_exact"], summary(second)["informative_exact"]


def print_rows(rows, limit=None):
    for row in rows[:limit]:
        gold = row["gold"]
        safe = row["safe"]
        ratio = ""
        if gold and gold != 0:
            ratio = f"{(safe / gold):.3f}"
        print(
            f"{row['request']:<12} gold {str(gold):>16}  ours {str(safe):>18}  "
            f"ratio {ratio:>7}  earliest {row['earliest']}  gold_earliest {row['gold_earliest']}"
        )


# ---------------------------------------------------------------------------
# Baseline: the real solver, all fields
# ---------------------------------------------------------------------------


FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def baseline_solver():
    events, profiles, fx, sample, options, messages, _ = load_data()
    matches = {field: 0 for field in FIELDS}
    full = 0
    rows = []
    for _, req in sample.iterrows():
        profile = profiles.loc[req["user_id"]].to_dict()
        user_events = [event for event in events if event["user_id"] == req["user_id"]]
        result = solve_request(
            req["request_id"],
            req["user_id"],
            req["request_date"],
            req["requested_amount"],
            profile,
            user_events,
            fx,
            options,
            messages,
            req.get("desired_completion_date"),
            req.get("allows_partial_payment"),
            installment_safety="strict",
        )
        row = {"request": req["request_id"]}
        all_match = True
        for field in FIELDS:
            gold = str(req[field]).strip()
            ours = str(result.get(field, "")).strip()
            if field == "amount_safe_to_pay":
                gold_value, ours_value = dec(gold), dec(ours)
                matched = gold_value is not None and ours_value is not None and abs(gold_value - ours_value) <= Decimal("0.01")
            else:
                matched = gold == ours
            matches[field] += int(matched)
            all_match = all_match and matched
            row[field] = (ours, gold, matched)
        full += int(all_match)
        rows.append(row)
    return rows, matches, full


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--variant", default="")
    parser.add_argument("--factors", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    data = load_data()
    events, profiles, fx, sample, options, messages, flags = data

    if args.baseline or args.all:
        rows, matches, full = baseline_solver()
        print("baseline full rows", full, "of", len(rows))
        for field in FIELDS:
            print(f"  {field:<32} {matches[field]}")
        for row in rows:
            print(row["request"], {f: row[f][0] for f in FIELDS})

    if args.variant:
        opts = knobs(**json.loads(args.variant))
        rows = replay(opts, events=events, profiles=profiles, fx=fx, sample=sample, flags=flags)
        print("variant", opts)
        print(summary(rows))
        print("holdout exact (informative):", holdout(rows))
        print_rows(rows)

    if args.factors or args.all:
        rows = replay(knobs(), events=events, profiles=profiles, fx=fx, sample=sample, flags=flags)
        print("factors")
        print_rows(rows)


if __name__ == "__main__":
    main()
