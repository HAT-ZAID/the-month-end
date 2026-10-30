"""Contract + invariant gates on output rows (gold self-check now, solution output later)."""
import pandas as pd
import pytest
from tests.conftest import OUTPUT_COLS
from tests.spec_checks import (check_bounds, check_eligibility, check_installment, check_partial,
                               check_spending, check_status_method_consistency, expand_option,
                               is_chronological, is_hard, parse_changes, parse_plan, to_decimal)


def _row(df, rid):
    return df[df["request_id"] == rid].iloc[0].to_dict()


def test_output_columns_exact_order(root_output_df, requests_df):
    if root_output_df is None:  # template scaffold check instead
        tpl = pd.read_csv("dataset/output.csv", dtype=str, keep_default_na=False)
        assert list(tpl.columns) == OUTPUT_COLS
        pytest.skip("no predictions yet")
    assert list(root_output_df.columns) == OUTPUT_COLS
    assert set(root_output_df["request_id"]) == set(requests_df["request_id"])
    assert not root_output_df["request_id"].duplicated().any()


def test_bounds_on_gold_and_built(gold_df, root_output_df, requests_df):
    """0<=safe<=requested on gold rows AND every built row (joined to true requested)."""
    for _, r in gold_df.iterrows():
        assert check_bounds(r.to_dict(), to_decimal(r["requested_amount"])) == []
    if root_output_df is None:
        pytest.skip("no predictions yet")
    req_amt = dict(zip(requests_df["request_id"], requests_df["requested_amount"]))
    for _, r in root_output_df.iterrows():
        row = r.to_dict()
        assert check_bounds(row, to_decimal(req_amt[row["request_id"]])) == [], row["request_id"]


def test_status_method_consistency_on_gold(gold_df, profile_by_user):
    prof = {u: profile_by_user[u] for u in gold_df["user_id"].unique()}
    errors = []
    for _, r in gold_df.iterrows():
        row, req = r.to_dict(), r.to_dict()
        errs = check_status_method_consistency(row, req, prof[r["user_id"]])
        if errs:
            errors.append((r["request_id"], errs))
    assert errors == [], f"gold rows failing consistency: {errors[:5]}"


def test_partial_req19_exact(gold_df, profile_by_user, options_by_request):
    r = _row(gold_df, "request_19")
    assert check_partial(r, r, profile_by_user["user_19"]) == []
    assert check_status_method_consistency(r, r, profile_by_user["user_19"]) == []
    assert check_eligibility("partial_payment", profile_by_user["user_19"]) == []


def test_installments_byte_match_gold(gold_df, options_by_request):
    for rid in ["request_02", "request_07", "request_12", "request_17", "request_22"]:
        r = _row(gold_df, rid)
        assert r["recommended_payment_method"] == "installments"
        assert check_installment(r, options_by_request[rid]) == [], rid


def test_not_affordable_triple_on_gold(gold_df):
    nad = gold_df[gold_df["affordability_status"] == "not_affordable"]
    assert len(nad) == 7
    for _, r in nad.iterrows():
        assert r["recommended_payment_method"] == "not_recommended", r["request_id"]
        assert r["payment_plan"] == "none", r["request_id"]
        assert r["earliest_date_for_full_payment"] == "", r["request_id"]
        assert r["spending_changes_needed"] == "none", r["request_id"]


def test_earliest_independence_req12(gold_df):
    """:163 — earliest==request_date must NOT imply affordable_now/full (req_12 proves it)."""
    r = _row(gold_df, "request_12")
    assert r["earliest_date_for_full_payment"] == r["request_date"]
    assert r["affordability_status"] == "affordable_with_plan"
    assert r["recommended_payment_method"] == "installments"


def test_plans_chronological_on_gold(gold_df):
    for _, r in gold_df.iterrows():
        if r["payment_plan"] != "none":
            assert is_chronological(r["payment_plan"]), r["request_id"]


def test_spending_syntax_on_gold(gold_df):
    for _, r in gold_df.iterrows():
        assert parse_changes(r["spending_changes_needed"]) is not None, r["request_id"]
    r21 = _row(gold_df, "request_21")
    acts = parse_changes(r21["spending_changes_needed"])
    assert len(acts) == 2 and acts[0][0] == "stop" and acts[1][0] == "reduce_to"
    assert acts[0][1] != acts[1][1], "stop+reduce same event forbidden"


def test_synthetic_partial_violations_caught():
    """Synthetic 5-gate negatives: wrong sum, closed flag, missing profile, wrong prefs."""
    base = {"amount_safe_to_pay": "100", "affordability_status": "affordable_with_plan",
            "recommended_payment_method": "partial_payment", "payment_plan": "2024-09-04:100|2024-09-15:50",
            "earliest_date_for_full_payment": "2024-09-15", "spending_changes_needed": "none",
            "decision_explanation": "x"}
    req = {"request_id": "r", "request_date": "2024-09-04", "requested_amount": "150",
           "desired_completion_date": "2024-10-04", "allows_partial_payment": "true"}
    prof = {"payment_methods_user_will_consider": "partial_payment"}
    assert check_partial(base, req, prof) == []
    bad = dict(base, payment_plan="2024-09-04:100|2024-09-15:60")
    assert check_partial(bad, req, prof) != []  # sums to 160, not 150
    bad2 = dict(req, allows_partial_payment="false")
    assert check_partial(base, bad2, prof) != []
    assert check_partial(base, req, None) != []  # missing profile: fail-closed
    assert check_partial(base, req, {"payment_methods_user_will_consider": "full_payment"}) != []


def test_all_built_rows_pass_every_gate(root_output_df, requests_df, profile_by_user,
                                        options_by_request, events_df):
    """Generic built-output gates (R1b-C1): every predicted row passes bounds, quad
    consistency, method-specific checks, eligibility, chronological plans, spending gates.
    Skipped until root output.csv holds predictions."""
    if root_output_df is None:
        pytest.skip("no predictions yet")
    req_by_id = {r["request_id"]: r.to_dict() for _, r in requests_df.iterrows()}
    user_by_req = {r["request_id"]: r["user_id"] for _, r in requests_df.iterrows()}
    events_by_id = {r["event_id"]: r.to_dict() for _, r in events_df.iterrows()}
    opt_by_id = {}
    for rid, opts in options_by_request.items():
        for o in opts:
            opt_by_id[o["payment_option_id"]] = o
    failures = []
    for _, r in root_output_df.iterrows():
        row, rid = r.to_dict(), r["request_id"]
        req, prof = req_by_id[rid], profile_by_user[user_by_req[rid]]
        errs = check_bounds(row, to_decimal(req["requested_amount"]))
        errs += check_status_method_consistency(row, req, prof)
        errs += is_hard(check_spending(row, events_by_id, prof))
        if not is_chronological(row.get("payment_plan", "")):
            errs.append("plan not chronological")
        method = row.get("recommended_payment_method", "")
        if method == "partial_payment":
            errs += check_partial(row, req, prof)
        if method == "installments":
            errs += check_installment(row, options_by_request.get(rid, []))
            matched = [o for o in options_by_request.get(rid, [])
                       if o.get("payment_method") == "installments"
                       and expand_option(o) == parse_plan(row.get("payment_plan", ""))]
            errs += is_hard(check_eligibility(method, prof, option=matched[0] if matched else None,
                                              options=options_by_request.get(rid, [])))
        elif method == "wait":
            errs += is_hard(check_eligibility(method, prof, row=row, request=req))
        else:
            errs += is_hard(check_eligibility(method, prof))
        if errs:
            failures.append((rid, errs))
    assert failures == [], f"built rows failing gates: {failures[:5]}"


def test_eligibility_matrix_on_profiles(profile_by_user, options_by_request):
    """user_02 (partial|installments, max 7) must NOT get wait; blank-max users must NOT
    get installments; req_02's 18-installment option is ineligible for max-7 users."""
    u02 = profile_by_user["user_02"]
    assert check_eligibility("wait", u02) != []
    assert check_eligibility("installments", u02) == []
    assert check_eligibility("full_payment", u02) != []
    o18 = [o for o in options_by_request["request_02"] if o["number_of_payments"] == "18"][0]
    assert check_eligibility("installments", u02, o18) != []
    o3 = [o for o in options_by_request["request_02"] if o["number_of_payments"] == "3"][0]
    assert check_eligibility("installments", u02, o3) == []
    # U6 boundary fixtures (fail-closed AND): divergent rows are ineligible either way.
    o8x1 = {"payment_method": "installments", "number_of_payments": "8",
            "payment_frequency_days": "1"}  # count 8>7 fails, span 0.23 passes
    assert check_eligibility("installments", {"payment_methods_user_will_consider": "installments",
                                              "max_installment_months": "7"}, o8x1) != []
    o2x90 = {"payment_method": "installments", "number_of_payments": "2",
             "payment_frequency_days": "90"}  # count 2<=2 passes, span 2.96 fails
    assert check_eligibility("installments", {"payment_methods_user_will_consider": "installments",
                                              "max_installment_months": "2"}, o2x90) != []
    u01 = profile_by_user["user_01"]  # full_payment only, blank max months
    assert check_eligibility("installments", u01) != []
    assert check_eligibility("not_recommended", u01) == []
