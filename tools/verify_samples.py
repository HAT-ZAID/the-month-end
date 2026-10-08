"""P2 Solver Verification: launder a launder-logic solver against sample-25 gold.

This script implements the fused launder pipeline (P1 modules) and uses the
harness to verify output against sample_requests.csv.
"""
from __future__ import annotations

import pandas as pd
from decimal import Decimal
from code.solver import solve_request
from tools.evaluation.harness import run_audit


def run_verification():
    # Load dataset
    requests = pd.read_csv("dataset/sample_requests.csv", dtype=str)
    profiles = pd.read_csv("dataset/financial_profiles.csv", dtype=str)
    events = pd.read_csv("dataset/financial_events.csv", dtype=str).to_dict("records")
    fx = pd.read_csv("dataset/exchange_rates.csv", dtype=str).to_dict("records")
    options = pd.read_csv("dataset/request_payment_options.csv", dtype=str)
    messages = pd.read_csv("dataset/messages.csv", dtype=str)
    
    results = []
    for _, req in requests.iterrows():
        user_id = req["user_id"]
        print(f"Solving {req['request_id']}...")
        profile = profiles[profiles["user_id"] == user_id].iloc[0].to_dict()
        user_events = [e for e in events if e["user_id"] == user_id]
        
        try:
            res = solve_request(
                req["request_id"], req["user_id"], req["request_date"], req["requested_amount"], profile, user_events, fx, options, messages,
                req.get("desired_completion_date"), req.get("allows_partial_payment"),
                installment_safety="strict",  # pin strict leg safety for verification runs
            )
        except Exception as e:
            print(f"Error solving {req['request_id']}: {e}")
            res = {"request_id": req["request_id"], "amount_safe_to_pay": "0", "affordability_status": "not_affordable", "recommended_payment_method": "not_recommended", "payment_plan": "none", "earliest_date_for_full_payment": None, "spending_changes_needed": "none", "decision_explanation": f"Error: {e}"}
        results.append(res)
    
    solver_df = pd.DataFrame(results)
    gold_df = requests
    
    errors = run_audit(solver_df, gold_df)
    print(f"Audit completed. Found {len(errors)} mismatches.")
    for err in errors:
        print(err)

if __name__ == "__main__":
    run_verification()
