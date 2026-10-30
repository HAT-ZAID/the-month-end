"""P3 Payment Options Module: handles the parsing and filtering of available payment options.

According to AGENTS.md §6.1 and §6.2:
- A request has 2-4 options in request_payment_options.csv.
- Options must be filtered by user preferences and max_installment_months.

Task 4 option math:
- ``max_installment_months`` is compared exactly (``total_days > max_months * 30``),
  never through ``total_days // 30`` which hid a plan just over the limit; the
  payment count must also fit the stated months (fail-closed AND reading).
- A supplied installment row must be internally consistent: its generated legs
  must add up to ``total_payable_amount`` (within 0.01) and its count/dates must
  be usable.  An inconsistent row is rejected rather than repaired.
"""
from __future__ import annotations

import pandas as pd
from decimal import Decimal
from datetime import date, timedelta

DAYS_PER_INSTALLMENT_MONTH = 30
TOTAL_TOLERANCE = Decimal("0.01")


def _decimal(value) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def _int(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return default
        return int(float(text))
    except (ValueError, TypeError, OverflowError):
        return default


def get_valid_options(request_id: str, profile: dict, options_df: pd.DataFrame) -> list[dict]:
    """Filters payment options based on user profile constraints.

    Constraints:
    - max_installment_months: if profile value is blank/NaN, installments are rejected.
    - Otherwise the exact plan duration (payments * frequency days) must fit
      max_months * 30 days and the payment count must fit max_months.
    """
    req_options = options_df[options_df["request_id"] == request_id].to_dict("records")

    # User preference for installments
    max_months_str = profile.get("max_installment_months")
    can_do_installments = False
    max_months = 0

    if max_months_str is not None and str(max_months_str).strip().lower() not in ("", "nan", "none"):
        try:
            max_months = int(float(max_months_str))
            can_do_installments = True
        except (ValueError, TypeError):
            can_do_installments = False

    valid_options = []
    for opt in req_options:
        method = opt["payment_method"]
        opt_id = opt["payment_option_id"]
        amount = _decimal(opt.get("payment_amount"))
        if amount is None:
            continue

        num_payments = _int(opt.get("number_of_payments"), default=0)

        try:
            first_date_str = str(opt["first_payment_date"]).strip()
        except (ValueError, TypeError, KeyError):
            continue
        try:
            first_date = date.fromisoformat(first_date_str)
        except ValueError:
            continue

        freq_days = _int(opt.get("payment_frequency_days"), default=30)
        if freq_days is None or freq_days < 0:
            continue

        if method == "installments":
            # Check if user accepts installments and if duration is within limit
            if not can_do_installments:
                continue
            if num_payments < 1:
                continue
            # Exact duration, not a floored month count: 45 days is over 1 month.
            total_days = num_payments * freq_days
            if total_days > max_months * DAYS_PER_INSTALLMENT_MONTH:
                continue
            if num_payments > max_months:
                continue

        # Build payment plan string
        payment_plan = _build_payment_plan(opt, first_date_str)

        legs_sum = amount * num_payments if num_payments else amount
        total_payable = _decimal(opt.get("total_payable_amount"))
        if total_payable is None:
            total_payable = legs_sum
        elif method == "installments":
            # The supplied row must describe the plan it claims to describe.
            if len(payment_plan.split("|")) != num_payments:
                continue
            if abs(legs_sum - total_payable) > TOTAL_TOLERANCE:
                continue

        completion_date = (
            (first_date + timedelta(days=(num_payments - 1) * freq_days)).isoformat()
            if num_payments
            else first_date_str
        )

        valid_options.append({
            "option_id": opt_id,
            "payment_method": method,
            "amount": amount,
            "total_payable": total_payable,
            "currency": profile.get("home_currency", "USD"),
            "installment_months": num_payments,
            "payment_plan": payment_plan,
            "number_of_payments": num_payments,
            "first_payment_date": first_date_str,
            "completion_date": completion_date,
        })

    return valid_options


def _build_payment_plan(opt: dict, first_date_str: str) -> str:
    """Builds a chronological 'YYYY-MM-DD:amount|...' payment plan string."""
    method = opt["payment_method"]
    amount = Decimal(str(opt["payment_amount"]))
    num_payments = int(float(opt["number_of_payments"]))
    
    try:
        freq_days = int(float(opt["payment_frequency_days"]))
    except (ValueError, TypeError, OverflowError):
        freq_days = 30
    
    first_date = date.fromisoformat(first_date_str)
    parts = []
    for i in range(num_payments):
        pay_date = first_date + timedelta(days=i * freq_days)
        parts.append(f"{pay_date.isoformat()}:{amount}")
    
    return "|".join(parts)
