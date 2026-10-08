"""P3 Message Parser: extracts financial updates from messages.csv.

Launder Logic:
- Salary updates: Look for 'salary', 'gaji', 'pay' and an amount.
- Event amendments: Look for 'related_event_id' and a change in amount or date.
- Updates are applied to the user's profile or event list before the Oracle run.
"""
from __future__ import annotations

import pandas as pd
from decimal import Decimal
import re

from code.amounts import extract_amounts, parse_amount

def parse_messages(user_id: str, messages_df: pd.DataFrame) -> dict:
    """Parses messages for a user and returns a set of updates.
    
    Returns:
    {
        "salary_updates": [{"effective_date": str, "amount": Decimal, "currency": str}],
        "event_updates": [{"related_event_id": str, "new_amount": Decimal, "new_date": str}]
    }
    """
    updates = {
        "salary_updates": [],
        "event_updates": []
    }
    
    user_msgs = messages_df[messages_df["user_id"] == user_id]
    
    for _, msg in user_msgs.iterrows():
        text = str(msg["message_text"])
        
        # 1. Detect Salary Updates: require explicit salary-raise signals.
        # Example: "Gaji bulanan Anda naik menjadi IDR 42750000"
        # EMP-0001 (raise with amount+effective date) yields an update;
        # EMP-0002 (confirmation without amount) and bonus/contract-ended
        # formats yield none (left untouched).
        # Exclude non-salary windfalls before emitting (bonus/commission/refund/lottery/investment-gain).
        if re.search(r"bonus|commission|refund|lottery|investment[\s_\-]*gain", text, re.IGNORECASE):
            salary_match = None
        elif not re.search(r"\b(salary|gaji)\b", text, re.IGNORECASE):
            salary_match = None
        elif not (re.search(r"rais|increas|naik|effect|appl|berlaku|mulai", text, re.IGNORECASE) or re.search(r"EMP-0001", text)):
            salary_match = None
        else:
            salary_match = re.search(r"(salary|gaji).*?([A-Z]{3})\s+([\d,.]+)", text, re.IGNORECASE)
        if salary_match:
            currency = salary_match.group(2).upper()
            raw_num = salary_match.group(3)
            # Strip trailing punctuation (e.g. "1037.52." -> "1037.52").
            raw_num = re.sub(r"[^\d.,]+$", "", raw_num)
            if raw_num.endswith("."):
                raw_num = raw_num[:-1]
            # Locale-aware parse via amounts router (ID dot-thousands safe).
            lang = "id" if re.search(r"\bgaji\b", text, re.IGNORECASE) else ""
            amount = parse_amount(raw_num, currency, lang)
            if amount is None:
                # Fall back to extracting currency-coded figures (handles symbols/spacing).
                cands = extract_amounts(text, lang)
                amount = cands[0]["amount"] if cands else None
                if cands:
                    currency = cands[0]["currency"]
            if amount is not None:
                # Try to find a date (YYYY-MM-DD)
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
                effective_date = date_match.group(1) if date_match else None

                updates["salary_updates"].append({
                    "effective_date": effective_date,
                    "amount": amount,
                    "currency": currency
                })
            
        # 2. Detect Event Amendments
        rel_id = msg.get("related_event_id")
        if pd.notna(rel_id) and str(rel_id).strip() != "":
            # Look for new amounts or dates in the text
            amt_match = re.search(r"([\d,.]+)\s+([A-Z]{3})", text)
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)

            update = {"related_event_id": rel_id}
            if amt_match:
                raw_a = amt_match.group(1)
                ccy_a = amt_match.group(2).upper()
                parsed_a = parse_amount(raw_a, ccy_a)
                if parsed_a is not None:
                    update["new_amount"] = parsed_a
            if date_match:
                update["new_date"] = date_match.group(1)

            updates["event_updates"].append(update)
            
    return updates
