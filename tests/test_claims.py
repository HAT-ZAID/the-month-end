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
def test_a_truncated_prefix_cannot_exempt_a_large_figure():
    # "paid 500   of 2000" pads past the eight-character window the count rule
    # used to inspect, so a slice could start mid-number and look like "00 of".
    report = verify_claims("paid 500   of 2000", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert "2000" in amounts


def test_a_large_figure_before_of_is_still_money():
    report = verify_claims("You can pay 2000 of your balance today.", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == ["2000"]


def test_a_figure_glued_to_a_currency_code_is_money():
    report = verify_claims("You can commit ZAR50000 today.", dict(ROW))
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == ["50000"]


def test_dot_grouped_thousands_are_read_as_one_figure():
    row = dict(ROW)
    row["amount_safe_to_pay"] = "2000000.00"
    report = verify_claims("You can commit Rp 2.000.000 today.", row)
    assert report["ok"] is True


def test_a_spending_change_amount_can_be_cited():
    row = dict(ROW)
    row["spending_changes_needed"] = "reduce_to:event_5:250"
    report = verify_claims("Reduce event_5 to 250 so the request fits.", row)
    amounts = [item["text"] for item in report["unsupported"] if item["kind"] == "amount"]
    assert amounts == []


def test_a_written_date_is_checked_and_not_read_as_money():
    row = dict(ROW)
    row["earliest_date_for_full_payment"] = "2025-09-12"
    report = verify_claims("The safest date is the 12th of September 2025.", row)
    assert [item for item in report["unsupported"] if item["kind"] == "amount"] == []
    assert report["ok"] is True
    wrong = verify_claims("The safest date is 15 September 2025.", row)
    assert [item["kind"] for item in wrong["unsupported"]] == ["date"]


def test_a_written_date_without_a_year_reads_against_the_row():
    row = dict(ROW)
    row["earliest_date_for_full_payment"] = "2025-09-12"
    assert verify_claims("12 September is the safest day.", row)["ok"] is True
    assert verify_claims("13 September is the safest day.", row)["ok"] is False


def test_a_figure_glued_to_a_letter_on_either_side_is_still_money():
    from code.claims import _money_claims

    assert [value for _, value in _money_claims("You can commit R88000 today.")] == [88000]
    assert [value for _, value in _money_claims("You can commit 88000ZAR today.")] == [88000]
    assert [value for _, value in _money_claims("You can commit R50,000 today.")] == [50000]
    report = verify_claims("You can commit R88000 today.", dict(ROW))
    assert report["checked"] == 1
    assert report["ok"] is False


def test_a_shorthand_multiplier_is_a_figure():
    from code.claims import _money_claims

    assert [value for _, value in _money_claims("You only need 50k.")] == [50000]
    assert [value for _, value in _money_claims("about 1.5m is left over")] == [1500000]


def test_a_modal_may_is_not_a_date():
    report = verify_claims("You may 2 payments need to be made to fit this in.", dict(ROW))
    assert [item for item in report["unsupported"] if item["kind"] == "date"] == []


def test_denying_a_required_change_is_a_claim():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    report = verify_claims("No spending changes are needed.", row)
    assert report["checked"] == 1
    assert [item["kind"] for item in report["unsupported"]] == ["changes"]

    quiet = dict(ROW)
    quiet["spending_changes_needed"] = "none"
    assert verify_claims("No spending changes are needed.", quiet)["ok"] is True


def test_a_stacked_marker_prefix_is_still_the_marker():
    # The candidate agrees with the row and makes no numeric claim, so the only
    # thing that can demote it is the marker behind the prefixes.
    candidate = {
        key: ROW[key]
        for key in (
            "request_id",
            "amount_safe_to_pay",
            "affordability_status",
            "recommended_payment_method",
            "payment_plan",
            "earliest_date_for_full_payment",
            "spending_changes_needed",
        )
    }
    candidate.update(
        {
            "evidence_sufficient": True,
            "confidence": 0.4,
            "rationale": "> - Disagreements: the plan holds against the ledger.",
        }
    )
    result = enforce_decision(candidate, dict(ROW), disagreement_note=True)
    assert result["verification"]["agent_agreement"] is True
    assert result["verification"]["rationale_source"] == "kernel"


def test_denying_a_required_change_without_the_word_any_is_a_claim():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    report = verify_claims("This works without spending changes.", row)
    assert report["ok"] is False
    assert [item["kind"] for item in report["unsupported"]] == ["changes"]


def _change_row():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    return row


def test_every_wording_that_denies_a_change_is_a_claim():
    row = _change_row()
    for sentence in (
        "No spending cuts are needed.",
        "You need not stop any spending.",
        "You do not need to reduce anything.",
        "spending_changes_needed: none.",
        "Your spending stays as it is.",
        "Nothing is cut from your plan.",
    ):
        report = verify_claims(sentence, row)
        assert report["ok"] is False, sentence


def test_a_decision_stated_in_ordinary_english_is_a_claim():
    row = dict(ROW)
    row["affordability_status"] = "not_affordable"
    row["recommended_payment_method"] = "not_recommended"
    assert verify_claims("You can afford this in full.", row)["ok"] is False
    assert verify_claims("Pay the full amount now.", row)["ok"] is False
    assert verify_claims("Pay in one installment.", row)["ok"] is False

    now = dict(ROW)
    now["affordability_status"] = "affordable_now"
    now["recommended_payment_method"] = "full_payment"
    assert verify_claims("You cannot afford this today.", now)["ok"] is False
    assert verify_claims("This is unaffordable right now.", now)["ok"] is False


def test_the_claim_check_accepts_the_cents_the_csv_prints():
    row = dict(ROW)
    row["amount_safe_to_pay"] = "110423.74250000000000000000"
    assert verify_claims("You can safely pay 110423.74 today.", row)["ok"] is True
    fine = verify_claims("You can safely pay 110423.7425 today.", row)
    assert fine["ok"] is False


def test_a_month_and_a_day_are_not_read_as_money():
    row = dict(ROW)
    assert [item for item in verify_claims(
        "March 14 is the day.", row
    )["unsupported"] if item["kind"] == "amount"] == []


def test_a_spelled_date_without_a_year_is_verified():
    row = dict(ROW)
    row["earliest_date_for_full_payment"] = "2025-09-12"
    wrong = verify_claims("The payment is due March 14.", row)
    assert [item["kind"] for item in wrong["unsupported"]] == ["date"]
    right = verify_claims("The payment is due September 12.", row)
    assert right["ok"] is True


def test_an_ordinal_suffix_is_a_date_not_an_amount():
    report = verify_claims("Make the payment on the 14th.", dict(ROW))
    assert [item for item in report["unsupported"] if item["kind"] == "amount"] == []


def test_a_modal_month_leaves_its_number_to_the_amount_check():
    report = verify_claims("Paying 14 may be too early.", dict(ROW))
    assert [item["text"] for item in report["unsupported"] if item["kind"] == "amount"] == [
        "14"
    ]


def test_a_day_only_ordinal_is_checked_against_the_row_dates():
    row = dict(ROW)
    row["earliest_date_for_full_payment"] = "2025-08-03"
    assert verify_claims("Make the payment on the 3rd.", row)["ok"] is True
    wrong = verify_claims("Make the payment on the 14th.", row)
    assert [item["kind"] for item in wrong["unsupported"]] == ["date"]


def test_an_ordinal_count_is_not_a_date():
    report = verify_claims("The 1st installment is due.", dict(ROW))
    assert [item for item in report["unsupported"] if item["kind"] == "date"] == []


def test_the_shape_of_a_denial_is_a_claim_not_a_list_of_words():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    for sentence in (
        "You won't need to cut anything.",
        "You will not have to reduce anything.",
        "Your spending is unchanged.",
        "Nothing changes in your spending.",
        "You do not have to stop that payment.",
        "You do not need to make any cuts.",
        "Spending can stay exactly as it is.",
        "No payments need to be cancelled.",
    ):
        assert verify_claims(sentence, row)["ok"] is False, sentence


def test_a_correct_wait_explanation_still_ships():
    row = dict(ROW)
    row["affordability_status"] = "affordable_later"
    row["recommended_payment_method"] = "wait"
    row["earliest_date_for_full_payment"] = "2026-05-15"
    assert verify_claims("Not affordable today, but yes later.", row)["ok"] is True
    # A sentence that states both polarities in one clause is treated as a
    # denial: the conservative reading costs an explanation, not money.
    assert verify_claims(
        "You cannot afford this today, but you can on 2026-05-15.", row
    )["ok"] is False


def test_a_plan_leg_is_compared_at_the_cents_the_csv_prints():
    row = dict(ROW)
    row["payment_plan"] = "2024-12-15:32036.45|2025-01-15:17413.55"
    assert verify_claims(
        "Pay 32036.45 today and 17413.55 on 2025-01-15.", row
    )["ok"] is True


def test_the_bare_adjective_is_affordable_in_any_form():
    later = dict(ROW)
    later["affordability_status"] = "affordable_later"
    later["recommended_payment_method"] = "wait"
    assert verify_claims("This is affordable.", later)["ok"] is True

    plan = dict(ROW)
    plan["affordability_status"] = "affordable_with_plan"
    assert verify_claims("You can afford this.", plan)["ok"] is True

    never = dict(ROW)
    never["affordability_status"] = "not_affordable"
    never["recommended_payment_method"] = "not_recommended"
    assert verify_claims("This is affordable.", never)["ok"] is False


def test_a_balance_statement_is_not_a_spending_denial():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    assert verify_claims("The current balance is unchanged.", row)["ok"] is True
    assert verify_claims("You have to stop that payment.", row)["ok"] is True


def test_a_cookie_header_is_redacted_whole():
    from code.trace import sanitize

    cleaned = sanitize({"e": "Cookie: theme=light; sessionid=opaque12345"})
    assert "opaque12345" not in cleaned["e"]


def test_the_shapes_of_denial_the_reviewer_measured_are_claims():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    for sentence in (
        "Neither stop nor reduce is needed.",
        "Cancel nothing; the plan already fits.",
        "There is nothing to trim.",
        "Keep your budget as is.",
        "You can leave your spending alone.",
        "Your subscriptions can stay.",
        "Spending remains untouched.",
        "Cutting back is unnecessary.",
        "No action on spending is required.",
    ):
        assert verify_claims(sentence, row)["ok"] is False, sentence


def test_a_recurring_figure_is_not_a_count():
    for sentence in ("You can pay 5000 monthly.", "Save 3000 yearly.", "Pay 5000 weekly."):
        assert verify_claims(sentence, dict(ROW))["ok"] is False, sentence
    assert verify_claims("You can pay 5000 per month.", dict(ROW))["ok"] is False


def test_a_figure_written_in_words_is_a_claim():
    assert verify_claims("You can pay twenty thousand today.", dict(ROW))["ok"] is False
    assert (
        verify_claims("You can pay twenty-two thousand five hundred today.", dict(ROW))[
            "ok"
        ]
        is False
    )


def test_a_relative_date_is_not_a_verifiable_date():
    assert verify_claims("Pay tomorrow instead.", dict(ROW))["ok"] is False
    assert verify_claims("Pay in 3 weeks.", dict(ROW))["ok"] is False
    assert verify_claims("Pay by the end of May.", dict(ROW))["ok"] is False


def test_a_full_payment_phrase_beside_a_date_is_a_date_statement():
    row = dict(ROW)
    row["recommended_payment_method"] = "wait"
    row["earliest_date_for_full_payment"] = "2025-09-12"
    assert verify_claims("The first safe full payment is 2025-09-12.", row)["ok"] is True


def test_a_spelled_count_is_not_an_amount():
    report = verify_claims("Pay in twenty-two installments.", dict(ROW))
    assert [item for item in report["unsupported"] if item["kind"] == "amount"] == []


def test_a_percentage_is_a_count_in_both_forms():
    for sentence in ("About 25% of the balance is safe.", "About 25 percent is safe."):
        assert [item for item in verify_claims(sentence, dict(ROW))["unsupported"]
                if item["kind"] == "amount"] == []


def test_a_relative_date_is_checked_even_beside_an_iso_date():
    row = dict(ROW)
    row["earliest_date_for_full_payment"] = "2025-08-03"
    report = verify_claims("Pay tomorrow; the deadline is 2025-08-03", row)
    assert [item["kind"] for item in report["unsupported"]] == ["date"]


def test_a_nearby_date_does_not_excuse_paying_in_full_now():
    row = dict(ROW)
    row["recommended_payment_method"] = "wait"
    row["earliest_date_for_full_payment"] = "2025-09-12"
    report = verify_claims("Pay in full now; 2025-09-12 is the deadline", row)
    assert report["ok"] is False


def test_a_budget_statement_about_the_changed_plan_is_not_a_denial():
    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    assert verify_claims(
        "The budget remains above the minimum after the change.", row
    )["ok"] is True


def test_a_long_private_key_is_still_redacted():
    from code.trace import sanitize

    header = ("-" * 5) + "BEGIN RSA PRIVATE KEY" + ("-" * 5)
    footer = ("-" * 5) + "END RSA PRIVATE KEY" + ("-" * 5)
    body = "MIIE" + ("A" * 9000)
    cleaned = sanitize({"e": header + "\n" + body + "\n" + footer})
    assert "AAAAAAAA" not in cleaned["e"]


def test_many_secrets_in_one_value_are_all_redacted():
    from code.trace import sanitize

    tokens = " ".join("sk-" + ("x%02d" % index) * 6 for index in range(60))
    cleaned = sanitize({"e": tokens})
    assert "sk-" not in cleaned["e"]
