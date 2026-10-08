"""Determinism pin (T3): solve_request twice on a fixed fixture → machine columns byte-identical.

Excludes decision_explanation (LLM prose) per submission-gate scoping; compares
all machine columns. Deep-copies mutable inputs so solver-side mutation cannot
leak between runs.
"""
import copy

import pandas as pd

from tests.conftest import DATASET

MACHINE_COLS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def _load_fixture(rid="request_26"):
    from code.solver import solve_request  # local import keeps collection light

    requests = pd.read_csv(DATASET / "requests.csv", dtype=str, keep_default_na=False)
    profiles = pd.read_csv(DATASET / "financial_profiles.csv", dtype=str, keep_default_na=False)
    events = pd.read_csv(DATASET / "financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
    fx = pd.read_csv(DATASET / "exchange_rates.csv", dtype=str, keep_default_na=False).to_dict("records")
    options = pd.read_csv(DATASET / "request_payment_options.csv", dtype=str, keep_default_na=False)
    messages = pd.read_csv(DATASET / "messages.csv", dtype=str, keep_default_na=False)
    req = requests[requests["request_id"] == rid].iloc[0].to_dict()
    profile = profiles[profiles["user_id"] == req["user_id"]].iloc[0].to_dict()
    user_events = [e for e in events if e["user_id"] == req["user_id"]]
    return solve_request, req, profile, user_events, fx, options, messages


def test_solve_request_deterministic_machine_cols():
    solve_request, req, profile, user_events, fx, options, messages = _load_fixture("request_26")

    def run_once():
        return solve_request(
            req["request_id"], req["user_id"], req["request_date"], req["requested_amount"],
            copy.deepcopy(profile), copy.deepcopy(user_events), copy.deepcopy(fx),
            options.copy(deep=True), messages.copy(deep=True),
            req.get("desired_completion_date"), req.get("allows_partial_payment"),
        )

    a, b = run_once(), run_once()
    for col in MACHINE_COLS:
        assert str(a[col]) == str(b[col]), f"non-deterministic {col}: {a[col]!r} vs {b[col]!r}"


def test_solve_request_deterministic_second_fixture():
    """Second pin on a different shape (partial-eligible gold request_19)."""
    # request_19 lives in sample_requests, not requests.csv — load from gold instead
    from code.solver import solve_request
    import pandas as pd
    from tests.conftest import DATASET as DS
    gold = pd.read_csv(DS / "sample_requests.csv", dtype=str, keep_default_na=False)
    profiles = pd.read_csv(DS / "financial_profiles.csv", dtype=str, keep_default_na=False)
    events = pd.read_csv(DS / "financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
    fx2 = pd.read_csv(DS / "exchange_rates.csv", dtype=str, keep_default_na=False).to_dict("records")
    options2 = pd.read_csv(DS / "request_payment_options.csv", dtype=str, keep_default_na=False)
    messages2 = pd.read_csv(DS / "messages.csv", dtype=str, keep_default_na=False)
    req2 = gold[gold["request_id"] == "request_19"].iloc[0].to_dict()
    prof2 = profiles[profiles["user_id"] == req2["user_id"]].iloc[0].to_dict()
    ev2 = [e for e in events if e["user_id"] == req2["user_id"]]

    def run_once():
        return solve_request(
            req2["request_id"], req2["user_id"], req2["request_date"], req2["requested_amount"],
            copy.deepcopy(prof2), copy.deepcopy(ev2), copy.deepcopy(fx2),
            options2.copy(deep=True), messages2.copy(deep=True),
            req2.get("desired_completion_date"), req2.get("allows_partial_payment"),
        )

    a, b = run_once(), run_once()
    for col in MACHINE_COLS:
        assert str(a[col]) == str(b[col]), f"non-deterministic {col}: {a[col]!r} vs {b[col]!r}"
