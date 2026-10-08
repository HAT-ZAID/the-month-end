"""P3 Payment Options Module: handles the parsing and filtering of available payment options.

According to AGENTS.md §6.1 and §6.2:
- A request has 2-4 options in request_payment_options.csv.
- Options must be filtered by user preferences and max_installment_months.
"""
from __future__ import annotations

import pandas as pd
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path


def get_valid_options(request_id: str, profile: dict, options_df: pd.DataFrame) -> list[dict]:
    """Filters payment options based on user profile constraints.
    
    Constraints:
    - max_installment_months: if profile value is blank/NaN, installments are rejected.
    - Otherwise, option's number_of_payments * payment_frequency_days must be within limit.
    """
    req_options = options_df[options_df["request_id"] == request_id].to_dict("records")
    
    # User preference for installments
    max_months_str = profile.get("max_installment_months")
    can_do_installments = False
    max_months = 0
    
    if max_months_str and str(max_months_str).strip().lower() != "nan":
        try:
            max_months = int(float(max_months_str))
            can_do_installments = True
        except ValueError:
            can_do_installments = False

    valid_options = []
    for opt in req_options:
        method = opt["payment_method"]
        opt_id = opt["payment_option_id"]
        amount = Decimal(str(opt["payment_amount"]))
        
        # Handle NaN gracefully
        try:
            num_payments = int(float(opt["number_of_payments"]))
        except (ValueError, TypeError, OverflowError):
            num_payments = 0
            
        try:
            first_date_str = str(opt["first_payment_date"])
        except (ValueError, TypeError):
            continue
            
        freq_str = opt.get("payment_frequency_days")
        try:
            freq_days = int(float(freq_str)) if freq_str and str(freq_str).strip().lower() != "nan" else 30
        except (ValueError, TypeError):
            freq_days = 30
        
        if method == "installments":
            # Check if user accepts installments and if duration is within limit
            if not can_do_installments:
                continue
            
            # Estimate total months = num_payments * freq_days / 30
            try:
                total_days = num_payments * int(float(freq_days or "0"))
                total_months = total_days // 30
                if total_months > max_months:
                    continue
            except (ValueError, TypeError):
                continue
        
        # Build payment plan string
        payment_plan = _build_payment_plan(opt, first_date_str)

        try:
            total_payable = Decimal(str(opt.get("total_payable_amount", "")))
            if total_payable.is_nan():
                raise ValueError("NaN total_payable")
        except Exception:
            total_payable = amount * num_payments if num_payments else amount
        try:
            first_date = date.fromisoformat(first_date_str)
            completion_date = (first_date + timedelta(days=(num_payments - 1) * freq_days)).isoformat() if num_payments else first_date_str
        except Exception:
            completion_date = first_date_str
        
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
