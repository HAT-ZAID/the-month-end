"""P1 Independent Oracle (TDD): 90-day daily-exact ledger reconstruction.

Launder logic:
- safe_amount: max X <= requested_amount such that balance - X >= min_balance
  for all t in [0, 90].
- earliest_date: first t in [0, 90] such that balance >= requested_amount.

Contract (Task 3):
- cash rows come from the supplied events only, plus the committed forecasts
  produced by :mod:`code.forecast`;
- an ambiguous row (unknown FX rate, unknown/blank status) **fails closed**:
  ``safe_amount = 0``, ``earliest_date = None``, ``ledger = []``,
  ``fail_closed = True`` with a reason.  A synthetic flat ledger is never
  returned, because a flat ledger cannot be told apart from a real one and
  must never be treated as a valid plan ledger downstream.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, DecimalException, InvalidOperation, Overflow

from code.amounts import strict_decimal
from code.forecast import build_forecast
from code.fx import resolve_rate
from code.recurrence import detect_recurrence  # noqa: F401 (kept for tooling compat)

_ALLOWED_STATUSES = ("settled", "scheduled", "pending")
_SETTLED_RANK = {"settled": 0, "scheduled": 1, "pending": 2}
_CANCELLATION_STATUSES = {"cancelled", "canceled"}
_KNOWN_IGNORED_STATUSES = {"failed", "unrealized"} | _CANCELLATION_STATUSES

# Every direction a cash row may carry.  An unknown label is ambiguous
# evidence: the sign cannot be guessed, so the ledger fails closed instead of
# treating "anything not debit" as a credit.
DIRECTIONS = frozenset({"debit", "credit", "non_cash"})


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


def _fail_closed_result(reason: str) -> dict:
    """The single fail-closed payload: no safe amount, no date, no ledger.

    A flat synthetic ledger would look like a real one downstream, so an
    ambiguous or unvaluable ledger always returns this shape from one place.
    """
    return {
        "safe_amount": Decimal("0"),
        "earliest_date": None,
        "ledger": [],
        "fail_closed": True,
        "fail_closed_reason": reason,
        "processed_events": [],
        "forecast": [],
        "forecast_warnings": [],
    }


def solve_safe_amount(
    user_id: str,
    request_date_str: str,
    requested_amount_str: str,
    profile: dict,
    events: list[dict],
    fx_rows: list[dict],
    recurrence_flags: set[tuple[str, str, str]],
    suppressed_categories: set[str] | None = None,
    *,
    category_overrides: dict[str, object] | None = None,
    evidence_flags: set[str] | list[str] | None = None,
    expense_facts: list[dict] | None = None,
) -> dict:
    """Recompute the 91-day ledger for one plan state.

    ``suppressed_categories`` removes a category's forward forecast (a ``stop``
    spending change).  ``category_overrides`` sets a category's forward
    run-rate to an exact amount without removing it (a ``reduce_to`` change).
    Both are keyword-only policy inputs so existing callers are unchanged.
    """
    req_date = date.fromisoformat(request_date_str)

    try:
        req_amt = Decimal(str(requested_amount_str))
    except (InvalidOperation, TypeError):
        req_amt = Decimal("0")

    min_bal = strict_decimal(profile.get("minimum_balance_to_keep"))
    start_bal = strict_decimal(profile.get("current_available_balance"))

    # An unanswered balance or reserve is not zero: without it the ledger
    # cannot be proven safe, so the row fails closed with a named reason.
    profile_fail_reasons: list[str] = []
    if start_bal is None:
        profile_fail_reasons.append("BALANCE_INVALID")
    # A negative reserve is not a smaller floor: subtracting it would *raise*
    # the amount declared safe.  It is invalid on the same path as a blank one.
    if min_bal is None or min_bal < 0:
        profile_fail_reasons.append("RESERVE_INVALID")

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

    processed_events = []
    fail_reasons: list[str] = list(profile_fail_reasons)
    for e in user_events:
        status = _norm_status(e)
        # Unknown/blank statuses are ambiguous evidence. They must not be
        # silently treated as a non-cash row: fail closed below.
        if status not in _ALLOWED_STATUSES and status not in _KNOWN_IGNORED_STATUSES:
            fail_reasons.append("STATUS_UNKNOWN")
            continue
        if status in _KNOWN_IGNORED_STATUSES:
            continue
        dir_norm = _norm_dir(e)
        if dir_norm not in DIRECTIONS:
            # An unknown label must never be read as "not a debit, so a
            # credit": guessing the sign could raise the safe amount.
            fail_reasons.append("DIRECTION_UNKNOWN")
            continue
        if dir_norm == "non_cash":
            continue
        s_date_str = e.get("settlement_date") or e.get("event_date")
        e_date = None
        if isinstance(s_date_str, str) and s_date_str:
            try:
                e_date = date.fromisoformat(s_date_str)
            except ValueError:
                e_date = None
        if e_date is None:
            # An active cash row that cannot be dated could fall anywhere in
            # the horizon; dropping it would hide an outflow.
            fail_reasons.append("DATE_UNKNOWN")
            continue

        if req_date <= e_date <= window_end:
            # Ignore pending credits, reserve pending debits.
            if status == "pending" and dir_norm == "credit":
                continue
            amt_base = strict_decimal(e.get("amount"))
            if amt_base is None or amt_base < 0:
                # A signed amount or a blank one is not a magnitude: keeping
                # "-9000" would flip a debit into a credit.  Reject, never abs.
                fail_reasons.append("AMOUNT_INVALID")
                continue
            fx_res = resolve_rate(fx_rows, e.get("currency"), home_ccy, s_date_str)
            rate = fx_res["rate"]

            # A prior-dated snapshot is resolved but still ambiguous: the FX
            # contract flags it FX_UNKNOWN so the ledger fails closed.
            if rate is None or fx_res.get("flag") == "FX_UNKNOWN":
                fail_reasons.append("FX_UNKNOWN")
                continue

            # A finite Decimal can still overflow when the FX rate scales its
            # exponent, and the default context traps Overflow.  An unguarded
            # multiplication would raise into the TUI/position callers, so fail
            # closed on the same path as any other unvaluable amount.
            try:
                amt = amt_base * rate
                # Only debit/credit reach this point (non_cash returned above).
                multiplier = -1 if dir_norm == "debit" else 1
                delta = amt * multiplier
            except (InvalidOperation, Overflow):
                fail_reasons.append("AMOUNT_INVALID")
                continue
            if not delta.is_finite():
                fail_reasons.append("AMOUNT_INVALID")
                continue
            processed_events.append({
                "date": e_date,
                "delta": delta,
                "flag": fx_res["flag"],
                "category": e.get("category"),
            })

    if fail_reasons:
        # Explicit fail-closed contract: no safe amount, no date, and no ledger.
        # A flat synthetic ledger would look like a real one downstream.
        return _fail_closed_result(fail_reasons[0])

    # Committed expense/salary projections come from the explicit policy module;
    # the oracle never extrapolates on its own.
    forecast = build_forecast(
        user_events,
        req_date,
        user_id=user_id,
        home_currency=home_ccy,
        fx_rows=fx_rows,
        recurrence_flags=recurrence_flags,
        suppressed_categories=suppressed,
        protected_categories=protected_cats,
        category_overrides=category_overrides,
        evidence_flags=evidence_flags,
        expense_facts=expense_facts,
    )
    for entry in forecast.entries:
        processed_events.append({
            "date": entry.date,
            "delta": entry.amount,
            "flag": "INCOME_FORECAST" if entry.kind == "income" else "ESSENTIAL_FORECAST",
            "category": entry.category,
            "source": entry.source,
        })

    # 2. Compute daily balances (including forecasts) and derive safe amount from the minimum balance
    daily_balances = []
    curr_bal = start_bal
    for i in range(91):
        d = req_date + timedelta(days=i)
        try:
            day_delta = sum((e["delta"] or 0) for e in processed_events if e["date"] == d)
        except DecimalException:
            # Dated deltas that cannot be summed are unvaluable money.
            fail_reasons.append("AMOUNT_INVALID")
            break
        try:
            curr_bal += day_delta
        except DecimalException:
            # The opening balance cannot absorb a dated delta: it is not
            # representable in the ledger arithmetic.
            fail_reasons.append("BALANCE_INVALID")
            break
        daily_balances.append(curr_bal)
    if fail_reasons:
        return _fail_closed_result(fail_reasons[0])

    # Safe amount based on the minimum balance over the horizon
    try:
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
    except DecimalException:
        # Balance/request arithmetic that overflows the context is unvaluable
        # too: fail closed instead of raising into the caller.
        fail_reasons.append("BALANCE_INVALID")
        return _fail_closed_result(fail_reasons[0])

    # Return results
    return {
        "safe_amount": safe_amount,
        "earliest_date": earliest_date,
        "ledger": daily_balances,
        "fail_closed": False,
        "fail_closed_reason": None,
        "processed_events": processed_events,
        "forecast": [
            {
                "date": entry.date.isoformat(),
                "amount": str(entry.amount),
                "kind": entry.kind,
                "category": entry.category,
                "source": entry.source,
            }
            for entry in forecast.entries
        ],
        "forecast_warnings": list(forecast.warnings),
    }
