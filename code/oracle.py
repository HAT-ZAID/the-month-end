"""P1 Independent Oracle (TDD): 90-day daily-exact ledger reconstruction.

Launder logic:
- safe_amount: max X <= requested_amount such that balance - X >= min_balance
  for all t in [0, 90].
- earliest_date: first t in [0, 90] such that balance >= requested_amount.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from code.fx import resolve_rate
from code.recurrence import detect_recurrence  # noqa: F401 (kept for tooling compat)

_ALLOWED_STATUSES = ("settled", "scheduled", "pending")
_SETTLED_RANK = {"settled": 0, "scheduled": 1, "pending": 2}
_CANCELLATION_STATUSES = {"cancelled", "canceled"}
_KNOWN_IGNORED_STATUSES = {"failed", "unrealized"} | _CANCELLATION_STATUSES


def _norm_status(e: dict) -> str:
    return str(e.get("status", "") or "").strip().lower()


def _norm_dir(e: dict) -> str:
    return str(e.get("direction", "") or "").strip().lower()


def _event_ordinal(e: dict) -> int:
    s = e.get("settlement_date") or e.get("event_date")
    if not s or not isinstance(s, str):
        return 0
    try:
        return date.fromisoformat(s).toordinal()
    except ValueError:
        return 0


def _amount_abs(e: dict) -> Decimal | None:
    try:
        raw = e.get("amount", "")
        if raw is None:
            return None
        d = Decimal(str(raw).strip())
        if not d.is_finite():
            return None
        return abs(d)
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None


def _is_countable(e: dict) -> bool:
    status = _norm_status(e)
    d = _norm_dir(e)
    if d == "non_cash":
        return False
    if status not in _ALLOWED_STATUSES:
        return False
    if status == "pending" and d != "debit":
        return False
    return True


def _pick_winner(rows: list[dict]) -> dict:
    """Pick an exact-identity survivor using explicit resolution precedence."""
    def key(e: dict):
        status = _norm_status(e)
        if status in _CANCELLATION_STATUSES:
            resolution_rank = 0
        elif status == "settled":
            resolution_rank = 1
        elif status in ("scheduled", "pending"):
            resolution_rank = 2
        else:
            resolution_rank = 3
        ord_val = _event_ordinal(e)
        s_rank = _SETTLED_RANK.get(status, 3)
        amt = _amount_abs(e)
        amt_val = amt if amt is not None else Decimal("0")
        d = _norm_dir(e)
        # Safer: larger expense (debit) reserves more; smaller income (credit) is conservative.
        safer = -amt_val if d == "debit" else amt_val
        eid = str(e.get("event_id", "") or "")
        return (resolution_rank, -ord_val, s_rank, safer, eid)

    return sorted(rows, key=key)[0]


def _dedup_events(events: list[dict]) -> list[dict]:
    """Collapse only exact IDs; apply explicit cancellation links."""
    # 1. Collapse exact event_id duplicates.
    by_id: dict[str, list[dict]] = {}
    no_id: list[dict] = []
    for e in events:
        eid = e.get("event_id", "")
        eid_s = str(eid or "").strip()
        if not eid_s:
            no_id.append(e)
        else:
            by_id.setdefault(eid_s, []).append(e)
    survivors: list[dict] = list(no_id)
    for _eid, rows in by_id.items():
        if len(rows) == 1:
            survivors.append(rows[0])
        else:
            survivors.append(_pick_winner(rows))
    # A cancellation row explicitly linked to a parent is reconciliation
    # evidence, unlike a link by itself. Remove the parent's cash row.
    id_map: dict[str, dict] = {}
    for e in survivors:
        eid_s = str(e.get("event_id", "") or "").strip()
        if eid_s:
            id_map[eid_s] = e
    drop_ids: set[int] = set()
    idx_by_obj: dict[int, int] = {id(e): i for i, e in enumerate(survivors)}
    for e in survivors:
        if _norm_status(e) not in _CANCELLATION_STATUSES:
            continue
        link = str(e.get("linked_event_id", "") or "").strip()
        target = id_map.get(link)
        if target is not None and target is not e:
            drop_ids.add(idx_by_obj[id(target)])
    return [e for i, e in enumerate(survivors) if i not in drop_ids]


def _median_interval(intervals: list[int]) -> int:
    s = sorted(intervals)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    # Even-n median uses integer floor division (//2): a .5 average is floored
    # to a whole day (conservative timing kept; no behavior change here).
    return (s[mid - 1] + s[mid]) // 2


def _history_eligible(e: dict, for_expense: bool) -> bool:
    if for_expense:
        # Subscriptions are recurring spending: count them as expense history.
        if str(e.get("event_type", "") or "").strip().lower() not in ("expense", "subscription"):
            return False
    else:
        if str(e.get("event_type", "") or "").strip().lower() != "income":
            return False
    status = _norm_status(e)
    d = _norm_dir(e)
    if d == "non_cash":
        return False
    if status not in ("settled", "scheduled", "pending"):
        return False
    if status == "pending":
        if for_expense:
            return d == "debit"
        return False
    return True


def solve_safe_amount(
    user_id: str,
    request_date_str: str,
    requested_amount_str: str,
    profile: dict,
    events: list[dict],
    fx_rows: list[dict],
    recurrence_flags: set[tuple[str, str, str]],
    suppressed_categories: set[str] | None = None,
) -> dict:
    req_date = date.fromisoformat(request_date_str)

    try:
        req_amt = Decimal(str(requested_amount_str))
    except (InvalidOperation, TypeError):
        req_amt = Decimal("0")

    try:
        min_bal = Decimal(str(profile.get("minimum_balance_to_keep", "0")))
    except (InvalidOperation, TypeError):
        min_bal = Decimal("0")

    try:
        start_bal = Decimal(str(profile.get("current_available_balance", "0")))
    except (InvalidOperation, TypeError):
        start_bal = Decimal("0")

    home_ccy = profile.get("home_currency", "USD")

    # Parse protected categories: strip whitespace, drop empty/nan elements.
    protected_cats = set()
    prot_raw = profile.get("expense_categories_to_protect", "")
    if prot_raw is not None:
        prot_s = str(prot_raw).strip()
        if prot_s and prot_s.lower() != "nan":
            for part in prot_s.split("|"):
                p = part.strip()
                if not p:
                    continue
                if p.lower() == "nan":
                    continue
                protected_cats.add(p)

    # Normalize suppressed categories: strip, drop empties, case-fold.
    suppressed = {
        str(c or "").strip().lower()
        for c in (suppressed_categories or set())
        if str(c or "").strip()
    }

    # 1. Filter and normalize events for the 90-day window [req_date, req_date + 90]
    window_end = req_date + timedelta(days=90)
    # Hoisted constants (perf): single user filter, single window bound.
    user_events_raw = [e for e in events if str(e.get("user_id", "") or "") == str(user_id)]
    # Dedup before the window loop: collapse event_id + linked_event_id retries.
    user_events = _dedup_events(user_events_raw)

    # Identify recurring expense categories (protected AND flexible: a spending
    # change only matters if the category is forecast at all) + income ones.
    recurring_expense_cats = set()
    recurring_income_cats: set[str] = set()
    for e in user_events:
        cat = e.get("category")
        et = str(e.get("event_type", "") or "").strip().lower()
        if et in ("expense", "subscription"):
            key = (str(e.get("user_id", "") or ""), cat, str(e.get("event_type", "") or ""))
            if key in recurrence_flags:
                recurring_expense_cats.add(cat)
        if et == "income":
            key = (str(e.get("user_id", "") or ""), cat, str(e.get("event_type", "") or ""))
            if key in recurrence_flags:
                recurring_income_cats.add(cat)

    # Build frequency map for recurring expense categories + recurring income
    # Only countable history (same event_type, settled/scheduled +pending-debit for expenses).
    recurring_expense_freq = {}
    income_cat_freq: dict[str, list[date]] = {}
    for e in user_events:
        cat = e.get("category")
        if cat in recurring_expense_cats or cat in recurring_income_cats:
            is_exp = cat in recurring_expense_cats
            # Require same event_type as the forecast kind.
            if is_exp and not _history_eligible(e, for_expense=True):
                # Allow income cats to still be checked below; skip expense-only.
                if cat not in recurring_income_cats or not _history_eligible(e, for_expense=False):
                    continue
            if cat in recurring_income_cats and cat not in recurring_expense_cats:
                if not _history_eligible(e, for_expense=False):
                    continue
            if cat in recurring_expense_cats and cat in recurring_income_cats:
                # Dual-category: keep row for each kind it is eligible for.
                exp_ok = _history_eligible(e, for_expense=True)
                inc_ok = _history_eligible(e, for_expense=False)
                if not exp_ok and not inc_ok:
                    continue
            s_date_str = e.get("settlement_date") or e.get("event_date")
            if s_date_str and isinstance(s_date_str, str):
                try:
                    e_date = date.fromisoformat(s_date_str)
                    if e_date < req_date:
                        if cat in recurring_expense_cats and _history_eligible(e, for_expense=True):
                            recurring_expense_freq.setdefault(cat, []).append(e_date)
                        if cat in recurring_income_cats and _history_eligible(e, for_expense=False):
                            income_cat_freq.setdefault(cat, []).append(e_date)
                except ValueError:
                    pass

    processed_events = []
    for e in user_events:
        status = _norm_status(e)
        dir_norm = _norm_dir(e)
        s_date_str = e.get("settlement_date") or e.get("event_date")
        if not s_date_str or not isinstance(s_date_str, str):
            continue
        try:
            e_date = date.fromisoformat(s_date_str)
        except ValueError:
            continue

        if req_date <= e_date <= window_end:
            # Unknown/blank statuses are ambiguous evidence. They must not be
            # silently treated as a non-cash row: fail closed below.
            if status not in _ALLOWED_STATUSES and status not in _KNOWN_IGNORED_STATUSES:
                processed_events.append({"date": e_date, "delta": None, "flag": "STATUS_UNKNOWN"})
                continue
            if status in _KNOWN_IGNORED_STATUSES:
                continue
            if dir_norm == "non_cash":
                continue
            # Ignore pending credits, reserve pending debits.
            if status == "pending" and dir_norm == "credit":
                continue
            fx_res = resolve_rate(fx_rows, e.get("currency"), home_ccy, s_date_str)
            rate = fx_res["rate"]

            if rate is None:
                processed_events.append({"date": e_date, "delta": None, "flag": "FX_UNKNOWN"})
                continue

            try:
                raw_amt = e.get("amount", "")
                if raw_amt is None:
                    raise ValueError("missing amount")
                amt_val = Decimal(str(raw_amt).strip())
                if not amt_val.is_finite():
                    raise ValueError("non-finite amount")
                amt = amt_val * rate
            except Exception:
                processed_events.append({"date": e_date, "delta": None, "flag": "FX_UNKNOWN"})
                continue

            multiplier = -1 if dir_norm == "debit" else 1
            processed_events.append({
                "date": e_date,
                "delta": amt * multiplier,
                "flag": fx_res["flag"],
                "category": e.get("category"),
            })

    # Add forecasted recurring expenses and incomes for the 90‑day window.
    # Median intervals (arrears-outlier robust); require >=2 pre-request dates;
    # Decimal averages over settled/scheduled (+pending+debit for expenses), same event_type.
    for cat in sorted(recurring_expense_cats):
        dates = recurring_expense_freq.get(cat, [])
        if len(dates) < 2:
            continue
        sorted_dates = sorted(dates)
        intervals = [(sorted_dates[i+1] - sorted_dates[i]).days for i in range(len(sorted_dates)-1)]
        if not intervals:
            continue
        avg_interval = _median_interval(intervals)
        if avg_interval <= 0:
            avg_interval = 30

        cat_events = [e for e in user_events
                      if e.get("category") == cat and _history_eligible(e, for_expense=True)]
        if cat in suppressed:
            # A spending change stops this category going forward. Historical
            # amounts stay in the ledger; only future forecast occurrences vanish.
            avg_amount = Decimal("0")
        else:
            cat_amounts: list[Decimal] = []
            for ce in cat_events:
                d = _amount_abs(ce)
                if d is None:
                    continue
                # FX: history was averaged in raw units while in-window events are
                # FX-converted; convert each history amount at its own settlement date.
                # Choice: on missing rate skip that observation (don't invent a rate,
                # don't pollute the average with raw foreign units).
                s_hist = ce.get("settlement_date") or ce.get("event_date")
                if not s_hist or not isinstance(s_hist, str):
                    continue
                fx_hist = resolve_rate(fx_rows, ce.get("currency"), home_ccy, s_hist)
                if fx_hist["rate"] is None:
                    continue
                d_home = d * fx_hist["rate"]
                # Recurring expenses forecast the full debit average: use -abs(d_home)
                # so a refund/credit row cannot lower (optimistically shrink) the forecast.
                cat_amounts.append(-d_home)
            if not cat_amounts:
                continue
            avg_amount = sum(cat_amounts, Decimal("0")) / Decimal(len(cat_amounts))
        # Determine first forecast date by rolling forward from last history date
        last_date = max(dates)
        next_date = last_date + timedelta(days=avg_interval)
        while next_date < req_date:
            next_date += timedelta(days=avg_interval)

        while next_date <= window_end:
            if not any(e.get("category") == cat and e["date"] == next_date for e in processed_events):
                processed_events.append({
                    "date": next_date,
                    "delta": avg_amount,
                    "flag": "ESSENTIAL_FORECAST",
                    "category": cat,
                })
            next_date += timedelta(days=avg_interval)

    # Forecast recurring income (e.g. salary) using same interval logic
    for cat in sorted(recurring_income_cats):
        dates = income_cat_freq.get(cat, [])
        if len(dates) < 2:
            continue
        sorted_dates = sorted(dates)
        intervals = [(sorted_dates[i+1] - sorted_dates[i]).days for i in range(len(sorted_dates)-1)]
        if not intervals:
            continue
        avg_interval = _median_interval(intervals)
        if avg_interval <= 0:
            avg_interval = 30
        cat_events = [e for e in user_events
                      if e.get("category") == cat and _history_eligible(e, for_expense=False)]
        cat_amounts_inc: list[Decimal] = []
        for ce in cat_events:
            d = _amount_abs(ce)
            if d is None:
                continue
            # FX: convert each income history amount at its own settlement date;
            # skip the observation when no rate exists (same choice as expenses).
            # Income sign kept as-is (+abs): only the expense path forces -abs.
            s_hist = ce.get("settlement_date") or ce.get("event_date")
            if not s_hist or not isinstance(s_hist, str):
                continue
            fx_hist = resolve_rate(fx_rows, ce.get("currency"), home_ccy, s_hist)
            if fx_hist["rate"] is None:
                continue
            cat_amounts_inc.append(d * fx_hist["rate"])
        if not cat_amounts_inc:
            continue
        avg_amount = sum(cat_amounts_inc, Decimal("0")) / Decimal(len(cat_amounts_inc))
        last_date = max(dates)
        next_date = last_date + timedelta(days=avg_interval)
        while next_date < req_date:
            next_date += timedelta(days=avg_interval)
        while next_date <= window_end:
            if not any(e.get("category") == cat and e["date"] == next_date for e in processed_events):
                processed_events.append({
                    "date": next_date,
                    "delta": avg_amount,
                    "flag": "INCOME_FORECAST",
                    "category": cat,
                })
            next_date += timedelta(days=avg_interval)

    # 2. Compute daily balances (including forecasts) and derive safe amount from the minimum balance
    # Fail-closed on FX ambiguity per spec: keep the 91-point ledger contract.
    if any(e.get("flag") in {"FX_UNKNOWN", "STATUS_UNKNOWN"} for e in processed_events):
        return {
            "safe_amount": Decimal("0"),
            "earliest_date": None,
            "ledger": [start_bal for _ in range(91)],
        }
    daily_balances = []
    curr_bal = start_bal
    for i in range(91):
        d = req_date + timedelta(days=i)
        day_delta = sum((e["delta"] or 0) for e in processed_events if e["date"] == d)
        curr_bal += day_delta
        daily_balances.append(curr_bal)
    # Safe amount based on the minimum balance over the horizon
    if any(b.is_nan() for b in daily_balances):
        safe_amount = Decimal('0')
    else:
        min_possible_safe = min(daily_balances) - min_bal
        safe_amount = max(Decimal('0'), min(req_amt, min_possible_safe))

    # 3. Earliest full payment date (first day where full amount can be paid safely on that day and remains safe thereafter)
    earliest_date = None
    for i in range(91):
        t = req_date + timedelta(days=i)
        # Ensure all future days after t stay above min_bal after paying the amount
        if all(daily_balances[j] - req_amt >= min_bal for j in range(i, 91)):
            earliest_date = t.isoformat()
            break

    # Return results
    return {
        "safe_amount": safe_amount,
        "earliest_date": earliest_date,
        "ledger": daily_balances,
    }
