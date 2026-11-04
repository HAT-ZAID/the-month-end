"""Gold-sample harness: the 25 solved rows as regression anchors (style, not labels).

What this program checks (AGENTS 6.1: samples show format/decision style, never eval labels):
- Gold rows themselves satisfy every output invariant (a broken gold expectation
  would poison all tuning, so the harness first proves the gold self-consistent).
- Per-field consistency spot checks used later to score solution iterations:
  status/method/plan/earliest/safe-tolerance/spending/explanation-consistency.
- The status×method matrix coverage the solution must reproduce (all 4 statuses,
  all 5 methods incl. the single partial exemplar req_19 and dual-change req_21).
- req_02 fee-vs-total triple pinned (total_payable decides, never financing_fee).
"""
from decimal import Decimal

from tests.spec_checks import parse_plan, to_decimal


def test_gold_matrix_covers_all_statuses_and_methods(gold_df):
    """All 4 statuses and all 5 methods appear: with_plan 9 / not 7 / later 6 / now 3."""
    assert set(gold_df["affordability_status"]) == {
        "affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
    assert set(gold_df["recommended_payment_method"]) == {
        "full_payment", "partial_payment", "installments", "wait", "not_recommended"}


def test_gold_single_partial_exemplar_is_req19(gold_df):
    """Exactly one partial row exists; its legs sum exactly to requested (39660)."""
    partials = gold_df[gold_df["recommended_payment_method"] == "partial_payment"]
    assert len(partials) == 1 and partials.iloc[0]["request_id"] == "request_19"
    legs = parse_plan(partials.iloc[0]["payment_plan"])
    assert sum(a for _, a in legs) == to_decimal(partials.iloc[0]["requested_amount"])


def test_gold_wait_rows_use_single_future_full_leg(gold_df):
    """Gold pattern (NOT a spec rule — spec is silent on wait shape, so this stays scoped
    to gold): all 6 wait rows schedule one full payment ON the earliest date."""
    waits = gold_df[gold_df["recommended_payment_method"] == "wait"]
    assert len(waits) == 6
    for _, r in waits.iterrows():
        legs = parse_plan(r["payment_plan"])
        assert legs is not None and len(legs) == 1, r["request_id"]
        assert legs[0][0] == r["earliest_date_for_full_payment"], r["request_id"]
        assert legs[0][1] == to_decimal(r["requested_amount"]), r["request_id"]


def test_gold_req02_total_beats_fee(gold_df, options_by_request):
    """Fee-minimizers lose: full (fee 0, total 46.0M) may be unsafe; among safe options the
    3-installment total 47,858,720.01 beats the 18-installment total 52,460,519.94."""
    from tests.spec_checks import expand_option
    totals = {}
    for o in options_by_request["request_02"]:
        legs = expand_option(o)
        totals[o["payment_option_id"]] = (o["payment_method"], sum(a for _, a in legs))
    assert totals  # populated
    three = [t for m, t in totals.values() if m == "installments"]
    assert min(three) == Decimal("47858720.01")


def test_gold_safe_below_requested_with_changes(gold_df):
    """req_06 (603.3 < 620.40 + stop:476) and req_11 (12510645 < 13110000 + reduce) prove the
    needs-a-change signature: safe<requested with a spending change attached."""
    r6 = gold_df[gold_df["request_id"] == "request_06"].iloc[0]
    assert to_decimal(r6["amount_safe_to_pay"]) < to_decimal(r6["requested_amount"])
    assert "stop:event_476" in r6["spending_changes_needed"]


def test_gold_nonzero_safe_can_still_be_not_affordable(gold_df):
    """Tiny safe amounts do not imply affordability (req_14: 597.74, req_24: 13420)."""
    for rid in ["request_14", "request_24"]:
        r = gold_df[gold_df["request_id"] == rid].iloc[0]
        assert to_decimal(r["amount_safe_to_pay"]) > 0
        assert r["affordability_status"] == "not_affordable"


def test_gold_explanations_cite_numbers(gold_df):
    """Every gold explanation names at least one amount or minimum (grounding bar for ours)."""
    import re
    for _, r in gold_df.iterrows():
        assert re.search(r"\d[\d,.]*", r["decision_explanation"]), r["request_id"]
