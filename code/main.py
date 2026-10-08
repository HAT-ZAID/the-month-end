"""Main solver for the Buy or Wait? problem.

Reads the dataset, processes each request using the solver pipeline,
and writes output.csv.

Run with: python -m code.main (from repo root)
"""
import sys
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

# Allow `python code/main.py` as well as `python -m code.main`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from code.solver import solve_request
    from code.ocr import configure_tesseract
    from code.evidence import resolve_event_amounts
    from code.explanations import generate_explanation
    from code.usage import new_usage, write_usage_report
else:
    from code.solver import solve_request
    from code.ocr import configure_tesseract
    from code.evidence import resolve_event_amounts
    from code.explanations import generate_explanation
    from code.usage import new_usage, write_usage_report

import pandas as pd

def configure_runtime():
    return configure_tesseract()


def _validated_amount(value) -> bool:
    try:
        amount = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount >= 0


def _fail_closed_result(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "amount_safe_to_pay": "0",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
        "decision_explanation": "Unresolved linked image evidence; no payment is recommended.",
    }


def _money(value) -> str:
    """Serialize a monetary figure at cent precision.

    Ground truth writes money at two decimals at most, so the solver's full
    Decimal precision must not reach output.csv or the explanation packet.
    Rounding down keeps 0 <= amount <= requested_amount true by construction.
    """
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return str(value)
    if not amount.is_finite():
        return str(value)
    text = format(amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _money_plan(plan: str, safe_amount: str, requested_amount: str, partial: bool = False) -> str:
    """Serialize payment_plan at cent precision.

    For partial_payment the plan keeps its contract invariants: leg one equals
    amount_safe_to_pay and the two legs sum exactly to requested_amount, so the
    second leg absorbs the rounding of the first.
    """
    if not plan or plan == "none":
        return plan
    legs = plan.split("|")
    if partial and len(legs) == 2 and ":" in legs[0] and ":" in legs[1]:
        try:
            requested = Decimal(str(requested_amount).strip())
            safe = Decimal(safe_amount)
            if requested.is_finite() and safe.is_finite() and 0 <= safe <= requested:
                return (
                    f"{legs[0].split(':', 1)[0]}:{safe_amount}"
                    f"|{legs[1].split(':', 1)[0]}:{_money(requested - safe)}"
                )
        except (InvalidOperation, ValueError):
            pass
    return "|".join(
        f"{leg.split(':', 1)[0]}:{_money(leg.split(':', 1)[1])}"
        if ":" in leg else leg
        for leg in legs
    )


def main():
    ocr_path = configure_runtime()
    REPO_ROOT = Path(__file__).resolve().parent.parent
    # Load data (repo-root anchored so any CWD works; no CLI change)
    requests = pd.read_csv(REPO_ROOT / "dataset/requests.csv", dtype=str)
    profiles = pd.read_csv(REPO_ROOT / "dataset/financial_profiles.csv", dtype=str)
    events = pd.read_csv(REPO_ROOT / "dataset/financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
    images = pd.read_csv(REPO_ROOT / "dataset/images.csv", dtype=str, keep_default_na=False)
    fx = pd.read_csv(REPO_ROOT / "dataset/exchange_rates.csv", dtype=str).to_dict("records")
    options = pd.read_csv(REPO_ROOT / "dataset/request_payment_options.csv", dtype=str)
    messages = pd.read_csv(REPO_ROOT / "dataset/messages.csv", dtype=str)
    events, evidence_warnings = resolve_event_amounts(
        images,
        events,
        REPO_ROOT / "dataset/media/images",
        ocr_available=ocr_path is not None,
    )

    # Prepare output rows
    output_rows = []
    usage = new_usage()
    for _, req in requests.iterrows():
        user_id = req["user_id"]
        profile = profiles[profiles["user_id"] == user_id].iloc[0].to_dict()
        user_events = [e for e in events if e["user_id"] == user_id]
        user_warnings = [w for w in evidence_warnings if w.get("event_id") in {e.get("event_id") for e in user_events}]
        all_amounts_validated = all(_validated_amount(e.get("amount")) for e in user_events)

        # Solve the request
        if user_warnings or not all_amounts_validated:
            res = _fail_closed_result(req["request_id"])
        else:
            res = solve_request(
                req["request_id"], user_id, req["request_date"], req["requested_amount"],
                profile, user_events, fx, options, messages,
                req.get("desired_completion_date"), req.get("allows_partial_payment"),
                installment_safety="strict",  # pin strict leg safety for production runs
            )

        # Format as output row
        safe_amount = _money(res["amount_safe_to_pay"])
        payment_plan = _money_plan(
            res["payment_plan"], safe_amount, req["requested_amount"],
            partial=res["recommended_payment_method"] == "partial_payment",
        )
        output_row = {
            "request_id": res["request_id"],
            "amount_safe_to_pay": safe_amount,
            "affordability_status": res["affordability_status"],
            "recommended_payment_method": res["recommended_payment_method"],
            "payment_plan": payment_plan,
            "earliest_date_for_full_payment": res["earliest_date_for_full_payment"],
            "spending_changes_needed": res["spending_changes_needed"],
            "decision_explanation": generate_explanation({
                "requested_amount": str(req["requested_amount"]),
                "request_type": str(req["request_type"]),
                "amount_safe_to_pay": safe_amount,
                "affordability_status": res["affordability_status"],
                "recommended_payment_method": res["recommended_payment_method"],
                "payment_plan": payment_plan,
                "earliest_date_for_full_payment": res["earliest_date_for_full_payment"] or "",
                "minimum_balance": profile.get("minimum_balance_to_keep", ""),
                "spending_changes_needed": res["spending_changes_needed"],
                "evidence_warnings": user_warnings,
                "_usage": usage,
            })
        }
        output_rows.append(output_row)

    # Create DataFrame and write to CSV (LF line endings per submission contract)
    output_df = pd.DataFrame(output_rows)
    output_df.to_csv(REPO_ROOT / "output.csv", index=False, lineterminator="\n")
    write_usage_report(REPO_ROOT / "evaluation" / "usage_report.md", usage, len(output_rows))
    write_usage_report(REPO_ROOT / "tools" / "evaluation" / "usage_report.md", usage, len(output_rows))
    print(f"Wrote {len(output_df)} rows to output.csv")

if __name__ == "__main__":
    # `--help` must describe the entrypoint instead of silently running the
    # full 250-request pipeline, which would exceed the documented 120s
    # entrypoint timeout under test.
    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        print(__doc__ or "usage: python -m code.main")
        sys.exit(0)
    main()
