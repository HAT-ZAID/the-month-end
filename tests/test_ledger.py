"""Ledger semantics: dataset cash-state facts + behavioral classifier tests.

Rule basis (problem_statement :176-178, AGENTS 6.1/6.3): settled/scheduled count;
pending debits RESERVED; pending credits/failed/cancelled/duplicates/unrealized IGNORED;
salary on settlement_date; linked_event_id never double-counted.
"""
from decimal import Decimal

from tests.spec_checks import CASH_COUNT, CASH_IGNORE, CASH_RESERVE, cash_standing


def _ev(status, direction):
    return {"status": status, "direction": direction}


def test_cash_standing_matrix():
    """Behavioral: every status×direction cell classifies per the cash-state rules."""
    assert cash_standing(_ev("settled", "debit")) == CASH_COUNT
    assert cash_standing(_ev("settled", "credit")) == CASH_COUNT
    assert cash_standing(_ev("scheduled", "debit")) == CASH_COUNT
    assert cash_standing(_ev("pending", "debit")) == CASH_RESERVE
    assert cash_standing(_ev("pending", "credit")) == CASH_IGNORE
    assert cash_standing(_ev("failed", "debit")) == CASH_IGNORE
    assert cash_standing(_ev("cancelled", "debit")) == CASH_IGNORE
    assert cash_standing(_ev("unrealized", "non_cash")) == CASH_IGNORE
    assert cash_standing(_ev("weird", "debit")) == CASH_IGNORE  # unknown: safer exclusion


def _balance(start, events):
    """Tiny ledger: settled/scheduled move cash; pending debits reserve; rest ignored."""
    bal, reserved = Decimal(start), Decimal(0)
    for e in events:
        s = cash_standing(e)
        amt = Decimal(str(e["amount"]))
        if s == CASH_COUNT:
            bal += amt if e["direction"] == "credit" else -amt
        elif s == CASH_RESERVE:
            reserved += amt
    return bal, reserved


def test_pending_credit_excluded_pending_debit_reserved():
    """The highest-EV cash bug: counting initiated refunds as cash, dropping held auths."""
    evs = [{"status": "settled", "direction": "credit", "amount": "1000"},
           {"status": "pending", "direction": "credit", "amount": "500"},   # initiated refund: ignored
           {"status": "pending", "direction": "debit", "amount": "200"}]    # held auth: reserved
    bal, reserved = _balance("0", evs)
    assert bal == Decimal("1000") and reserved == Decimal("200")


def test_failed_cancelled_unrealized_never_move_cash():
    evs = [{"status": "failed", "direction": "debit", "amount": "300"},
           {"status": "cancelled", "direction": "debit", "amount": "400"},
           {"status": "unrealized", "direction": "non_cash", "amount": "5000"}]
    bal, reserved = _balance("0", evs)
    assert (bal, reserved) == (Decimal("0"), Decimal("0"))


def test_real_fixture_ids_classify(events_df):
    """Real rows through the classifier: failed event_438 + cancelled event_100 ignored,
    settled event_101 counted, unrealized valuations ignored."""
    by_id = {r["event_id"]: r.to_dict() for _, r in events_df.iterrows()}
    assert cash_standing(by_id["event_438"]) == CASH_IGNORE
    assert cash_standing(by_id["event_100"]) == CASH_IGNORE
    assert cash_standing(by_id["event_101"]) == CASH_COUNT
    assert cash_standing(by_id["event_99"]) == CASH_COUNT  # settled refund: cash
    assert cash_standing(by_id["event_1785"]) == CASH_IGNORE  # pending refund: not cash


def test_status_counts_match_contract(events_df):
    c = events_df["status"].value_counts().to_dict()
    assert c.get("settled", 0) == 25148
    assert c.get("pending", 0) == 71
    assert c.get("scheduled", 0) == 70
    assert c.get("cancelled", 0) == 22
    assert c.get("failed", 0) == 21
    assert c.get("unrealized", 0) == 10


def test_pending_debits_exist_to_reserve(events_df):
    pend_deb = events_df[(events_df["status"] == "pending") & (events_df["direction"] == "debit")]
    assert len(pend_deb) > 0, "pending debits must exist for reserve tests"


def test_pending_credits_exist_to_exclude(events_df):
    pend_cred = events_df[(events_df["status"] == "pending") & (events_df["direction"] == "credit")]
    assert len(pend_cred) > 0, "pending credits must exist for exclusion tests"


def test_unrealized_are_non_cash_investment_valuations(events_df):
    unr = events_df[events_df["status"] == "unrealized"]
    assert (unr["event_type"] == "investment_valuation").all()
    assert (unr["direction"] == "non_cash").all()


def test_failed_cancelled_examples_present(events_df):
    assert (events_df["status"] == "failed").any()
    assert (events_df["status"] == "cancelled").any()
    # failed utility debit + cancelled auth superseded by settled retry (no double-count fixtures)
    assert "event_438" in set(events_df["event_id"])
    assert {"event_100", "event_101"} <= set(events_df["event_id"])


def test_linked_refund_pairs_present(events_df):
    assert {"event_98", "event_99"} <= set(events_df["event_id"])


def test_settled_bonus_commission_examples_present(events_df, messages_df):
    # settled windfalls count; pending bonus/commission must not (per-message gating tested post-build)
    assert (messages_df["message_text"].str.contains("not been approved|not.*earned|initiated but has not reached")).any()


def test_salary_settlement_amend_message_present(messages_df):
    m5 = messages_df[messages_df["message_id"] == "message_05"]
    assert len(m5) == 1 and "2024-09-23" in m5.iloc[0]["message_text"]
    assert "replaces the payroll date" in m5.iloc[0]["message_text"]


def test_contract_end_message_present(messages_df):
    m9 = messages_df[messages_df["message_id"] == "message_09"]
    assert len(m9) == 1 and "has ended" in m9.iloc[0]["message_text"]


def test_flexibility_distribution(events_df):
    c = events_df["flexibility"].value_counts().to_dict()
    assert c.get("fixed", 0) == 21138
    assert c.get("reducible", 0) == 2682
    assert c.get("stoppable", 0) == 1297
    assert c.get("reducible_or_stoppable", 0) == 225


def test_reducible_rows_carry_minimum(events_df):
    red = events_df[events_df["flexibility"].isin(["reducible", "reducible_or_stoppable"])]
    assert (red["minimum_allowed_amount"] != "").all()


def test_req06_stop_event_is_stoppable_streaming(events_df):
    e = events_df[events_df["event_id"] == "event_476"]
    assert len(e) == 1 and e.iloc[0]["flexibility"] == "stoppable"


def test_req11_reduce_event_floor(events_df):
    e = events_df[events_df["event_id"] == "event_989"]
    assert len(e) == 1
    assert e.iloc[0]["flexibility"] == "reducible"
    assert float(e.iloc[0]["minimum_allowed_amount"]) == 665950
