"""Claim checking: a model may phrase things, never add facts."""
from __future__ import annotations

from code.claims import describe_rejection, verify_claims
from code.verifier import enforce_decision

ROW = {
    "request_id": "request_demo",
    "amount_safe_to_pay": "15656000",
    "affordability_status": "affordable_now",
    "recommended_payment_method": "full_payment",
    "payment_plan": "2025-08-03:15656000",
    "earliest_date_for_full_payment": "2025-08-03",
    "spending_changes_needed": "none",
    "decision_explanation": "Recommended full_payment (affordable_now).",
}


def test_grounded_prose_passes_with_every_claim_counted():
    report = verify_claims(
        "Paying 15,656,000.00 on 2025-08-03 keeps the balance above the floor; "
        "full payment is the recommended method.",
        ROW,
    )
    assert report["ok"] is True
    assert report["checked"] >= 3
    assert report["unsupported"] == []


def test_invented_amount_is_rejected_with_the_reason():
    report = verify_claims("You can safely pay 20,000,000 today.", ROW)
    assert report["ok"] is False
    (claim,) = report["unsupported"]
    assert claim["kind"] == "amount"
    assert claim["text"] == "20,000,000"
    assert "15656000" in claim["expected"][0]
    assert "20,000,000" in describe_rejection(report)


def test_invented_date_is_rejected():
    report = verify_claims("Wait until 2025-09-30 and then pay.", ROW)
    assert report["ok"] is False
    assert {item["kind"] for item in report["unsupported"]} == {"date", "method"}


def test_wrong_method_and_status_words_are_rejected():
    report = verify_claims("You should wait and pay in installments.", ROW)
    kinds = {item["kind"] for item in report["unsupported"]}
    assert kinds == {"method"}


def test_literal_contract_tokens_are_checked():
    report = verify_claims("The kernel says not_affordable here.", ROW)
    assert report["ok"] is False
    (claim,) = report["unsupported"]
    assert claim["kind"] == "token"


def test_actions_must_match_the_decision():
    report = verify_claims("Stop stop:event_2322 to make room.", ROW)
    assert report["ok"] is False
    assert report["unsupported"][0]["kind"] == "action"


def test_small_counts_are_not_treated_as_amounts():
    report = verify_claims("It takes 2 payments and 3 days.", ROW)
    assert report["ok"] is True
    assert report["checked"] == 0


def test_extra_facts_from_the_request_may_be_cited():
    report = verify_claims(
        "The 1,000.00 request dated 2026-01-05 fits.",
        {**ROW, "amount_safe_to_pay": "1000"},
        extra_numbers=("1000",),
        extra_dates=("2026-01-05",),
    )
    assert report["ok"] is True


def test_empty_prose_is_not_a_claim_and_does_not_ship():
    report = verify_claims("   ", ROW)
    assert report["ok"] is False
    assert report["rejected"] is False  # nothing was asserted, so nothing was refused


def test_verifier_keeps_kernel_text_when_the_rationale_fabricates():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "15656000",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.9,
        "rationale": "You can pay 99,000,000 and still be fine.",
    }
    result = enforce_decision(candidate, dict(ROW))
    assert result["final_row"]["decision_explanation"] == ROW["decision_explanation"]
    assert result["verification"]["rationale_source"] == "kernel"
    assert result["verification"]["claim_check"]["ok"] is False


def test_verifier_keeps_kernel_text_when_fields_disagree():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",  # wrong
        "affordability_status": "affordable_later",  # wrong
        "recommended_payment_method": "wait",  # wrong
        "payment_plan": "none",  # wrong
        "earliest_date_for_full_payment": "",  # wrong
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": "The transfer of 15,656,000 clears on 2025-08-03 in one payment.",
    }
    result = enforce_decision(candidate, dict(ROW))
    # Grounded or not, prose from a candidate that disagreed with the plan does
    # not ship: the explanation would describe a row we are not emitting.
    assert result["verification"]["rationale_source"] == "kernel"
    assert result["verification"]["agent_agreement"] is False
    assert result["verification"]["claim_check"]["ok"] is True
    assert result["final_row"]["decision_explanation"] == ROW["decision_explanation"]
    # machine fields still come from the kernel
    assert result["final_row"]["affordability_status"] == "affordable_now"
    assert result["final_row"]["amount_safe_to_pay"] == "15656000"

def test_the_agent_path_may_say_where_it_disagrees():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",  # wrong
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": "I read the balance differently, so amount_safe_to_pay is 1.",
    }
    result = enforce_decision(candidate, dict(ROW), disagreement_note=True)
    text = result["final_row"]["decision_explanation"]
    assert result["verification"]["rationale_source"] == "disagreement"
    assert text.startswith("Disagreements:")
    assert "amount_safe_to_pay" in text
    assert result["verification"]["disagreement_note"] == text
    # machine fields still come from the kernel
    assert result["final_row"]["amount_safe_to_pay"] == "15656000"


def test_a_note_that_names_the_wrong_field_is_replaced_by_the_real_diff():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",  # the only real difference
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": "I disagree about payment_plan only.",
    }
    result = enforce_decision(candidate, dict(ROW), disagreement_note=True)
    text = result["final_row"]["decision_explanation"]
    assert text.startswith("Disagreements:")
    assert "payment_plan" not in text
    assert "amount_safe_to_pay" in text


def test_the_batch_path_never_ships_a_disagreement_note():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",
        "affordability_status": "affordable_later",
        "recommended_payment_method": "wait",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": "Spend differently.",
    }
    result = enforce_decision(candidate, dict(ROW))
    assert result["verification"]["rationale_source"] == "kernel"
    assert result["final_row"]["decision_explanation"] == ROW["decision_explanation"]


def test_context_facts_cover_what_the_model_was_shown():
    from code.agent import _context_facts

    amounts, dates = _context_facts(
        {"request_id": "request_demo"},
        {
            "request": {
                "requested_amount": "20500",
                "request_date": "2026-06-01",
                "desired_completion_date": "2026-07-05",
            },
            "profile": {
                "current_available_balance": "1048.86",
                "minimum_balance_to_keep": "1100",
            },
        },
    )
    assert set(amounts) == {"20500", "1048.86", "1100"}
    assert set(dates) == {"2026-06-01", "2026-07-05"}


def test_a_rationale_may_cite_the_request_amount_it_was_given():
    from code.agent import _context_facts

    amounts, dates = _context_facts(
        {},
        {"request": {"requested_amount": "6670", "request_date": "2026-07-05"}},
    )
    report = verify_claims(
        "The 20,500 request is affordable now: 6,670.00 is safe today.",
        {**ROW, "amount_safe_to_pay": "6670", "request_id": "request_demo"},
        extra_numbers=amounts,
        extra_dates=dates,
    )
    # 20,500 was never supplied, so it stays rejected even with the extras.
    assert report["ok"] is False


def test_request_facts_use_the_keyword_only_tool_api():
    """A positional call would raise inside the loop and silently widen nothing."""

    class KeywordOnly:
        def __init__(self):
            self.calls = []

        def call(self, name, **kwargs):
            self.calls.append((name, kwargs))
            if not kwargs.get("request_id"):
                raise TypeError("request_id is keyword only")
            return {"ok": True, "data": {"request": {"requested_amount": "6670"}}}

    from code.agent import _request_facts

    tools = KeywordOnly()
    facts = _request_facts(tools, "request_demo")
    assert tools.calls == [("get_request_context", {"request_id": "request_demo"})]
    assert facts["request"]["requested_amount"] == "6670"


def test_request_facts_are_empty_when_the_read_fails():
    from code.agent import _request_facts

    class Broken:
        def call(self, name, **kwargs):
            raise RuntimeError("store unavailable")

    assert _request_facts(Broken(), "request_demo") == {}
    assert _request_facts(object(), "request_demo") == {}

def test_prose_from_an_invalid_candidate_never_ships():
    candidate = {
        "request_id": "request_OTHER",
        "amount_safe_to_pay": "15656000",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.9,
        "rationale": "This other purchase is fine.",
    }
    result = enforce_decision(candidate, dict(ROW))
    assert result["verification"]["candidate_valid"] is False
    assert result["verification"]["rationale_source"] == "kernel"
    assert result["final_row"]["decision_explanation"] == ROW["decision_explanation"]


def test_an_invented_figure_never_ships_in_a_disagreement_note():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": (
            "I disagree with amount_safe_to_pay: you can pay 99000000 on 2030-01-01."
        ),
    }
    result = enforce_decision(candidate, dict(ROW), disagreement_note=True)
    text = result["final_row"]["decision_explanation"]
    assert result["verification"]["rationale_source"] == "disagreement"
    # The invented amount and date are neither in the kernel row nor in the
    # candidate, so the note falls back to the marked sentence.
    assert "99000000" not in text
    assert "2030-01-01" not in text
    assert "amount_safe_to_pay" in text


def test_a_disagreement_note_may_quote_the_candidate_s_own_figures():
    candidate = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2025-08-03:15656000",
        "earliest_date_for_full_payment": "2025-08-03",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.4,
        "rationale": "I counted amount_safe_to_pay as 1 after the 2025-08-03 bills.",
    }
    result = enforce_decision(candidate, dict(ROW), disagreement_note=True)
    text = result["final_row"]["decision_explanation"]
    assert text.startswith("Disagreements:")
    # The model's own figure and date are allowed: this is its record of what
    # it thought, not a claim about the ledger.
    assert "I counted amount_safe_to_pay as 1" in text
def test_a_small_figure_beside_a_money_word_is_still_a_claim():
    report = verify_claims("You can safely pay 500 today, keeping a 900 buffer.", dict(ROW))
    # Both figures sit beside a money word, so they are claims about the row
    # and neither appears in it.
    assert report["ok"] is False
    assert report["checked"] >= 1


def test_the_batch_row_never_takes_a_disagreement_note():
    from code.main import _preferred_explanation

    template = "Kernel template text."
    note = "Disagreements: the proposal disagrees with the computed ledger (fields: amount_safe_to_pay)"
    res = {"decision_explanation": note}
    assert _preferred_explanation(template, res, None) == template
    assert (
        _preferred_explanation(template, res, {"verification": {"rationale_source": "disagreement"}})
        == template
    )
    assert _preferred_explanation(template, res, {"verification": {"rationale_source": "kernel"}}) == template
    assert (
        _preferred_explanation(template, res, {"verification": {"rationale_source": "candidate"}})
        == note
    )
def test_a_currency_marked_figure_is_a_claim_without_a_cue_word():
    report = verify_claims("$500 is due now.", dict(ROW))
    assert report["checked"] >= 1
    assert report["ok"] is False


def test_a_unit_count_is_not_a_money_claim():
    report = verify_claims("Pay 500 in 2 installments.", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == ["500"]


def test_an_agreeing_candidate_cannot_borrow_the_disagreement_marker():
    row = dict(ROW)
    fields = (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
    )
    candidate = {key: row[key] for key in fields}
    candidate["evidence_sufficient"] = True
    candidate["confidence"] = 0.9
    candidate["rationale"] = "Disagreements: none. The plan holds against the ledger."
    result = enforce_decision(candidate, row, disagreement_note=True)
    explanation = result["final_row"]["decision_explanation"]
    # The marker is the only in-band signal separating an unendorsed note from
    # a decision, so an agreeing candidate may not use it.
    assert not explanation.startswith("Disagreements:")
    assert result["verification"]["rationale_source"] == "kernel"


def test_a_figure_with_no_money_word_near_it_is_still_a_claim():
    report = verify_claims("The ceiling is 200 today.", dict(ROW))
    assert report["checked"] >= 1
    assert report["ok"] is False
def test_both_figures_in_a_count_expression_stay_out_of_amount_claims():
    report = verify_claims("We resolved 3 of 5 samples.", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == []


def test_a_price_beside_a_count_still_counts():
    report = verify_claims("We resolved 3 of 5 samples for 2000.", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == ["2000"]
