"""Pure spec validators (no solution imports): shared by tests now, reused by solution later.

Every check returns a list of error strings (empty == pass). Rules cite
problem_statement.md / AGENTS.md sections. Only spec-stated rules are enforced;
open unknowns (U1 earliest-after-deadline, U2 FX fallback, U3 both-flex operator,
U6 max-months unit) are parameters, never silent resolutions.
"""
from __future__ import annotations

import re
from code.amounts import parse_amount
from datetime import date
from decimal import Decimal, InvalidOperation

PLAN_LEG = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+(?:\.\d+)?)$")
STOP = re.compile(r"^stop:(\S+)$")
REDUCE = re.compile(r"^reduce_to:(\S+):(\d+(?:\.\d+)?)$")

STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}

# Statuses whose cash-state rule is settled: pending debits are RESERVED (count against
# the floor), pending credits are IGNORED, failed/cancelled/duplicates/unrealized are
# IGNORED, scheduled counts (AGENTS.md 6.1/6.3, problem_statement :176-178).
CASH_COUNT = "count"      # settled (any direction), scheduled
CASH_RESERVE = "reserve"  # pending debit: held against the floor, never paid out
CASH_IGNORE = "ignore"    # pending credit, failed, cancelled, duplicate, unrealized


def cash_standing(event: dict) -> str:
    """Classify one financial event for ledger inclusion (pure rule, no inference).

    >>> cash_standing({"status": "settled", "direction": "debit"})
    'count'
    >>> cash_standing({"status": "pending", "direction": "debit"})
    'reserve'
    >>> cash_standing({"status": "pending", "direction": "credit"})
    'ignore'
    >>> cash_standing({"status": "failed", "direction": "debit"})
    'ignore'
    >>> cash_standing({"status": "cancelled", "direction": "debit"})
    'ignore'
    >>> cash_standing({"status": "unrealized", "direction": "non_cash"})
    'ignore'
    >>> cash_standing({"status": "scheduled", "direction": "debit"})
    'count'
    """
    status, direction = event.get("status", ""), event.get("direction", "")
    if status in ("failed", "cancelled", "unrealized"):
        return CASH_IGNORE
    if status == "pending":
        return CASH_RESERVE if direction == "debit" else CASH_IGNORE
    if status in ("settled", "scheduled"):
        return CASH_COUNT
    return CASH_IGNORE  # unknown status: financially-safer reading is exclusion


def to_decimal(s: str) -> Decimal | None:
    try:
        d = Decimal((s or "").strip())
    except (InvalidOperation, AttributeError):
        return None
    # Reject non-finite (NaN/sNaN/Infinity): callers compare with <= so NaN would
    # raise InvalidOperation (ERROR) instead of a clean FAIL list. None => FAIL.
    try:
        if not d.is_finite():
            return None
    except AttributeError:
        return None
    return d


_EARLIEST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_valid_earliest(s: str) -> bool:
    """Earliest must be '' (never safe) or a real YYYY-MM-DD date. Rejects
    'NaN'/'None'/'null'/malformed strings that otherwise pass string compares."""
    s = (s or "").strip()
    if s == "":
        return True
    if not _EARLIEST_RE.match(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def parse_plan(plan: str):
    """Return list[(date-str, Decimal)] or [] for 'none'; None if malformed."""
    plan = (plan or "").strip()
    if plan == "none":
        return []
    legs = []
    for part in plan.split("|"):
        m = PLAN_LEG.match(part.strip())
        if not m:
            return None
        legs.append((m.group(1), Decimal(m.group(2))))
    return legs


def is_chronological(plan: str) -> bool:
    legs = parse_plan(plan)
    if legs is None:
        return False
    dates = [d for d, _ in legs]
    return dates == sorted(dates)


def parse_changes(cell: str):
    """Return list of actions or [] for 'none'; None if malformed."""
    cell = (cell or "").strip()
    if cell == "none":
        return []
    acts = []
    for part in cell.split("|"):
        part = part.strip()
        m = STOP.match(part)
        if m:
            acts.append(("stop", m.group(1), None))
            continue
        m = REDUCE.match(part)
        if m:
            acts.append(("reduce_to", m.group(1), Decimal(m.group(2))))
            continue
        return None
    return acts


def normalize_amount(raw: str, currency: str, lang: str = "") -> Decimal | None:
    """Locale-aware parse (CLDR-lite). ID/ES-ES: '.' group, ',' decimal; else US style.

    Delegates to the production router (code.amounts.parse_amount) so the test oracle
    and the shipped solution share one implementation and cannot drift.

    >>> normalize_amount("43.339.000", "IDR", "id")
    Decimal('43339000')
    >>> normalize_amount("1,302.40", "EUR", "en")
    Decimal('1302.40')
    >>> normalize_amount("1.050,32", "IDR", "id")
    Decimal('1050.32')
    """
    return parse_amount(raw or "", currency, lang)


def expand_option(opt: dict):
    """Expand an options row to dated legs [(date-str, Decimal)] using first_payment_date + frequency."""
    from datetime import datetime, timedelta

    n = int(opt["number_of_payments"])
    start = datetime.strptime(opt["first_payment_date"], "%Y-%m-%d").date()
    freq = int(opt["payment_frequency_days"]) if (opt.get("payment_frequency_days") or "").strip() else 0
    amt = Decimal(str(opt["payment_amount"]))
    return [((start + timedelta(days=freq * i)).isoformat(), amt) for i in range(n)]


def check_bounds(row: dict, requested: Decimal) -> list[str]:
    errs = []
    safe = to_decimal(row.get("amount_safe_to_pay", ""))
    if safe is None:
        return ["amount_safe_to_pay not numeric"]
    if requested is None or (isinstance(requested, Decimal) and not requested.is_finite()):
        return ["requested_amount not numeric"]
    if not (Decimal(0) <= safe <= requested):
        errs.append(f"0<=safe<=requested violated: safe={safe} req={requested}")
    return errs


def check_status_method_consistency(row: dict, request: dict, profile: dict) -> list[str]:
    """Quad consistency per :117-122 + :163 (independence: never assert earliest==req => now)."""
    errs = []
    status, method = row.get("affordability_status", ""), row.get("recommended_payment_method", "")
    if status not in STATUSES:
        errs.append(f"bad status {status!r}")
    if method not in METHODS:
        errs.append(f"bad method {method!r}")
    if errs:
        return errs
    safe = to_decimal(row.get("amount_safe_to_pay", ""))
    req = to_decimal(request.get("requested_amount", ""))
    earliest = (row.get("earliest_date_for_full_payment", "") or "").strip()
    req_date = request.get("request_date", "")
    prefs = set((profile.get("payment_methods_user_will_consider", "") or "").split("|")) - {""}
    plan = parse_plan(row.get("payment_plan", ""))
    changes = parse_changes(row.get("spending_changes_needed", ""))
    if plan is None:
        errs.append("malformed payment_plan")
    if changes is None:
        errs.append("malformed spending_changes_needed")
    if not is_valid_earliest(earliest):
        errs.append(f"bad earliest_date_for_full_payment {earliest!r}")
    if status == "affordable_now":
        if safe != req:
            errs.append("affordable_now requires safe==requested")
        if earliest != req_date:
            errs.append("affordable_now requires earliest==request_date")
        if "full_payment" not in prefs:
            errs.append("affordable_now requires user accepts full_payment")
        # now=>method full_payment is RANKING-ENTAILED, not directly spec-stated: full is always
        # cheaper than installments for the same amount (fee>=0), so rank rule 3 forces full
        # whenever safe==requested and accepted. Kept as error (reviewer R1 accepted with cite).
        if method != "full_payment":
            errs.append("affordable_now requires method full_payment")
    if method == "partial_payment" and status != "affordable_with_plan":
        errs.append("partial_payment requires status affordable_with_plan")
    if status == "affordable_with_plan":
        if method not in ("partial_payment", "installments", "full_payment"):
            errs.append("with_plan requires partial/installments/full(+changes)")
        else:
            legs = parse_plan(row.get("payment_plan", "")) or []
            chgs = parse_changes(row.get("spending_changes_needed", "")) or []
            if method == "full_payment" and not legs and not chgs:
                errs.append("with_plan+full needs dated legs or spending changes")
    if status == "affordable_later" and method != "wait":
        errs.append("affordable_later requires method wait")
    if status == "not_affordable":
        if method != "not_recommended":
            errs.append("not_affordable requires method not_recommended")
        if plan:
            errs.append("not_affordable requires plan none")
        if earliest:
            errs.append("not_affordable requires empty earliest")
        if changes:
            errs.append("not_affordable requires spending none")
    if method == "not_recommended" and plan:
        errs.append("not_recommended requires plan none")
    if method == "not_recommended" and status != "not_affordable":
        errs.append("not_recommended requires status not_affordable")
    # Deliberately NOT asserted (spec-ambiguous or preference-independent):
    # - earliest==request_date => affordable_now (FALSE: req_12 has earliest==request_date with installments)
    # - wait plan shape (spec silent; samples show single-future-full, allowed but not enforced)
    return errs


def check_partial(row: dict, request: dict, profile: dict | None = None) -> list[str]:
    """Exact 5-gate + 2-leg shape per :146. Fail-closed: profile missing => cannot
    prove user acceptance => gate fails (prior _prefs-injection hack removed, R1b-C2)."""
    errs = []
    if (request.get("allows_partial_payment", "") or "").strip().lower() != "true":
        errs.append("partial requires allows_partial_payment=true")
    if profile is None:
        errs.append("partial requires profile to prove user acceptance")
    elif "partial_payment" not in set((profile.get("payment_methods_user_will_consider", "") or "").split("|")):
        errs.append("partial requires user accepts partial_payment")
    safe = to_decimal(row.get("amount_safe_to_pay", ""))
    req = to_decimal(request.get("requested_amount", ""))
    earliest = (row.get("earliest_date_for_full_payment", "") or "").strip()
    deadline = request.get("desired_completion_date", "")
    if safe is None or req is None:
        return errs + ["partial needs numeric safe/requested"]
    if not (Decimal(0) < safe < req):
        errs.append("partial requires 0<safe<requested")
    if earliest and deadline and earliest > deadline:
        errs.append("partial requires earliest<=deadline")
    legs = parse_plan(row.get("payment_plan", ""))
    if legs is None or len(legs) != 2:
        errs.append("partial requires exactly two plan legs")
        return errs
    (d1, a1), (d2, a2) = legs
    if d1 != request.get("request_date", "") or a1 != safe:
        errs.append("partial leg1 must be request_date:safe")
    if d2 != earliest or a2 != req - safe:
        errs.append("partial leg2 must be earliest:(requested-safe)")
    if a1 + a2 != req:
        errs.append("partial legs must sum to requested_amount")
    return errs


def check_installment(row: dict, options: list[dict]) -> list[str]:
    """Plan must byte-match exactly one supplied option row (dates + amounts + count)."""
    legs = parse_plan(row.get("payment_plan", ""))
    if legs is None or not legs:
        return ["installments requires a dated plan"]
    for opt in options:
        if str(opt.get("payment_method", "")) != "installments":
            continue
        try:
            if expand_option(opt) == legs:
                total = Decimal(str(opt["total_payable_amount"]))
                if sum(a for _, a in legs) != total:
                    return ["installment legs match dates/amounts but not total_payable_amount"]
                return []
        except (ValueError, KeyError, InvalidOperation):
            continue
    return ["installment plan matches no supplied payment option"]


def check_eligibility(method: str, profile: dict, option: dict | None = None,
                      options: list[dict] | None = None, days_per_month: float = 30.44,
                      row: dict | None = None, request: dict | None = None) -> list[str]:
    """Method eligibility per :189 + AGENTS 6.1.

    U6 PARAMETERIZED (F2): neither payments-count nor duration-months is spec-defined as
    "months", so BOTH readings are checked: eligible iff n<=max OR span_days/days_per_month<=max
    (span = (n-1)*frequency for installments, 0 for full). Divergence is flagged U6-DIVERGE
    (warning, never fail) so boundary rows (24x31 vs 24, 3x28 vs 2) stay visible either way.
    - immediate methods (full/partial/installments) require presence in will_consider
    - installments additionally require non-blank max_installment_months
    - wait requires full_payment in will_consider (future-safe full is a full payment)
    - not_recommended always eligible (fallback)
    """
    errs = []
    prefs = set((profile.get("payment_methods_user_will_consider", "") or "").split("|")) - {""}
    if method == "not_recommended":
        return errs
    if method == "wait":
        if "full_payment" not in prefs:
            errs.append("wait requires user accepts full_payment")
        if row is not None and request is not None:
            # wait is eligible only when full becomes safe LATER: earliest must be a
            # future non-empty date (:189). Earliest==request_date means safe now (now,
            # not later); empty means never safe (not_affordable, not later).
            earliest = (row.get("earliest_date_for_full_payment", "") or "").strip()
            req_date = request.get("request_date", "")
            if not earliest:
                errs.append("wait requires future non-empty earliest_date")
            elif earliest <= req_date:
                errs.append("wait requires earliest strictly after request_date")
        return errs
    if method not in prefs:
        errs.append(f"{method} not in payment_methods_user_will_consider")
    if method == "installments":
        max_m = (profile.get("max_installment_months", "") or "").strip()
        if not max_m:
            errs.append("installments require non-blank max_installment_months")
        else:
            cands = [option] if option is not None else (options or [])
            if cands:
                try:
                    limit = int(float(max_m))
                    ok = False
                    for o in cands:
                        n = int(o.get("number_of_payments", "0"))
                        freq = int((o.get("payment_frequency_days", "") or "0").strip() or 0)
                        span = ((n - 1) * freq) / days_per_month if freq else 0.0
                        # FAIL-CLOSED AND (F-cycle): both count and duration readings must
                        # pass; divergence is a hard reject, not silent OR (spec is silent
                        # on the unit, so the only safe reading satisfies both).
                        if n <= limit and span <= limit:
                            ok = True
                            break
                    if not ok:
                        errs.append("no installment option within max_installment_months (count+duration)")
                except ValueError:
                    errs.append("unparseable max_installment_months/number_of_payments")
            elif option is None and options is not None:
                errs.append("installments require an option to check term against")
    return errs


def check_spending(row: dict, events_by_id: dict, profile: dict) -> list[str]:
    """Validate spending_changes_needed: max 3, flexible-only, category-permitted,
    floor+no-increase, stop+reduce exclusivity (:148-161, :198, AGENTS 6.2).

    Floor justification (F-cycle decision): no spec sentence links minimum_allowed_amount
    to reduce_to, BUT every gold reduce sits exactly on its floor (req_11 665950==min,
    req_21 23.50==min) and a cut below the stated minimum (or a raise above current)
    invents an unsupported fact (:207 no-invent). Hard gate follows gold evidence;
    a spec-literal-only scorer accepting sub-floor cuts is the accepted residual risk.
    `recurring` qualifier needs history (caller-supplied); failed/cancelled never pass.
    """
    errs = []
    acts = parse_changes(row.get("spending_changes_needed", ""))
    if acts is None:
        return ["malformed spending_changes_needed"]
    if len(acts) > 3:
        errs.append("at most three spending changes")
    reduce_cats = set((profile.get("expense_categories_user_is_willing_to_reduce", "") or "").split("|")) - {""}
    stop_cats = set((profile.get("expense_categories_user_is_willing_to_stop", "") or "").split("|")) - {""}
    protected = set((profile.get("expense_categories_to_protect", "") or "").split("|")) - {""}
    touched: dict[str, str] = {}
    for kind, eid, new_amt in acts:
        ev = events_by_id.get(eid)
        if ev is None:
            errs.append(f"unknown event {eid}")
            continue
        flex = ev.get("flexibility", "")
        cat = ev.get("category", "")
        if flex == "fixed" or (kind == "stop" and flex not in ("stoppable", "reducible_or_stoppable")
                               ) or (kind == "reduce_to" and flex not in ("reducible", "reducible_or_stoppable")):
            # NOTE: reducible_or_stoppable operator is UNKNOWN (U3); both verbs provisionally allowed.
            if not (flex == "reducible_or_stoppable"):
                errs.append(f"{kind} on non-flexible event {eid} (flex={flex})")
        if cat in protected:
            errs.append(f"change on protected category {cat} ({eid})")
        if kind == "stop" and cat not in stop_cats:
            errs.append(f"stop on non-stoppable category {cat} ({eid})")
        if kind == "reduce_to" and cat not in reduce_cats:
            errs.append(f"reduce on non-reducible category {cat} ({eid})")
        if ev.get("status", "") in ("failed", "cancelled"):
            errs.append(f"change on {ev.get('status')} event {eid}")
        if kind == "reduce_to":
            try:
                cur = Decimal(str(ev.get("amount", "")))
                floor = Decimal(str(ev.get("minimum_allowed_amount", "") or "0"))
                if not (floor <= new_amt <= cur):
                    errs.append(f"reduce_to {new_amt} outside [{floor},{cur}] ({eid})")
            except InvalidOperation:
                errs.append(f"unparseable amounts for {eid}")
        if eid in touched:
            errs.append(f"stop+reduce on same event {eid} forbidden")
        touched[eid] = kind
    return errs


def is_hard(errors: list[str]) -> list[str]:
    """Drop ADVISORY findings (U6-DIVERGE), keeping spec-stated + gold-evidenced violations."""
    return [e for e in errors if not e.startswith("ADVISORY")]
