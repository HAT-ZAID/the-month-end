"""Assertions added during the release review rounds.

Every case here pins a hole a reviewer found, one sentence at a time, so they
are grouped by family and driven from a table: one test per invariant instead
of one test per wording. The semantic tests stay in ``tests/test_claims.py``;
this module is the regression net around them.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from code.claims import verify_claims
from code.trace import sanitize
from code.verifier import enforce_decision

REPO = Path(__file__).resolve().parents[2]

ROW = {
    "request_id": "request_demo",
    "amount_safe_to_pay": "15656000",
    "affordability_status": "affordable_now",
    "recommended_payment_method": "full_payment",
    "payment_plan": "2025-08-03:15656000",
    "earliest_date_for_full_payment": "2025-08-03",
    "spending_changes_needed": "none",
    "decision_explanation": "Pay the full amount, which the plan supports.",
}


def _row(**changes) -> dict:
    row = dict(ROW)
    row.update(changes)
    return row


# --- figures ---------------------------------------------------------------

FIGURE_CASES = [
    # sentence, row changes, expected ok
    ("You can pay 2000 of your balance today.", {}, False),
    ("The ceiling is 200 today.", {}, False),
    ("$500 is due now.", {}, False),
    ("You can commit ZAR50000 today.", {}, False),
    ("You can commit R88000 today.", {}, False),
    ("You can commit 88000ZAR today.", {}, False),
    ("You can commit Rp 2.000.000 today.", {}, False),
    ("You only need 50k.", {}, False),
    ("You can pay 5000 monthly.", {}, False),
    ("You can pay twenty thousand today.", {}, False),
    # the amount is a count, but the method it names is not the row's
    ("Pay 500 in 2 installments.", {}, False),
    ("We resolved 3 of 5 samples.", {}, True),
    ("Pay in twenty-two installments.", {}, False),
    ("About 25% of the balance is safe.", {}, True),
    ("paid 500   of 2000", {}, False),
]


@pytest.mark.parametrize("sentence,changes,expected", FIGURE_CASES)
def test_figure_claims(sentence, changes, expected):
    assert verify_claims(sentence, _row(**changes))["ok"] is expected


# --- dates -----------------------------------------------------------------

DATE_CASES = [
    ("The safest date is 12 September 2025.", {}, False),
    ("The payment is due March 14.", {}, False),
    ("Make the payment on the 14th.", {}, False),
    ("Pay tomorrow instead.", {}, False),
    ("Pay in 3 weeks.", {}, False),
    ("Pay by the end of May.", {}, False),
    ("Pay tomorrow; the deadline is 2025-08-03", {}, False),
    ("The safest date is 2025-08-03.", {}, True),
    ("12 September is the safest day.", {"earliest_date_for_full_payment": "2025-09-12"}, True),
    ("Make the payment on the 3rd.", {"earliest_date_for_full_payment": "2025-08-03"}, True),
    ("The 1st installment is due.", {}, False),
    # a date claim the row does not support, and no amount complaint
    ("March 14 is the day.", {}, False),
]


@pytest.mark.parametrize("sentence,changes,expected", DATE_CASES)
def test_date_claims(sentence, changes, expected):
    assert verify_claims(sentence, _row(**changes))["ok"] is expected


# --- status and method -----------------------------------------------------

STATUS_CASES = [
    ("You can afford this in full.", {"affordability_status": "not_affordable"}, False),
    ("Pay the full amount now.", {"recommended_payment_method": "wait"}, False),
    ("Pay in one installment.", {"recommended_payment_method": "full_payment"}, False),
    ("You cannot afford this today.", {}, False),
    ("This is affordable.", {"affordability_status": "not_affordable"}, False),
    ("This is affordable.", {"affordability_status": "affordable_later"}, True),
    ("Not affordable today, but yes later.", {"affordability_status": "affordable_later",
                                              "recommended_payment_method": "wait",
                                              "earliest_date_for_full_payment": "2026-05-15"}, True),
    ("The first safe full payment is 2025-09-12.",
     {"recommended_payment_method": "wait",
      "earliest_date_for_full_payment": "2025-09-12"}, True),
    ("Pay in full now; 2025-09-12 is the deadline.",
     {"recommended_payment_method": "wait",
      "earliest_date_for_full_payment": "2025-09-12"}, False),
]


@pytest.mark.parametrize("sentence,changes,expected", STATUS_CASES)
def test_status_and_method_claims(sentence, changes, expected):
    assert verify_claims(sentence, _row(**changes))["ok"] is expected


# --- denying a required spending change ------------------------------------

DENIAL_CASES = [
    "No spending changes are needed.",
    "No spending cuts are needed.",
    "This works without spending changes.",
    "You need not stop any spending.",
    "You do not need to reduce anything.",
    "You do not need to make any cuts.",
    "You won't need to cut anything.",
    "You will not have to reduce anything.",
    "Your spending is unchanged.",
    "Nothing changes in your spending.",
    "Nothing is cut from your plan.",
    "Spending can stay exactly as it is.",
    "No payments need to be cancelled.",
    "Neither stop nor reduce is needed.",
    "Cancel nothing; the plan already fits.",
    "There is nothing to trim.",
    "Keep your budget as is.",
    "You can leave your spending alone.",
    "Your subscriptions can stay.",
    "Spending remains untouched.",
    "Cutting back is unnecessary.",
    "No action on spending is required.",
    "spending_changes_needed: none.",
]


@pytest.mark.parametrize("sentence", DENIAL_CASES)
def test_denying_a_required_change_is_a_claim(sentence):
    row = _row(spending_changes_needed="stop:event_9")
    assert verify_claims(sentence, row)["ok"] is False


@pytest.mark.parametrize(
    "sentence",
    [
        "The current balance is unchanged.",
        "You have to stop that payment.",
        "The budget remains above the minimum after the change.",
    ],
)
def test_a_statement_about_the_changed_plan_is_not_a_denial(sentence):
    row = _row(spending_changes_needed="stop:event_9")
    assert verify_claims(sentence, row)["ok"] is True


# --- the cents the CSV prints ---------------------------------------------

def test_the_claim_check_accepts_the_cents_the_csv_prints():
    row = _row(amount_safe_to_pay="110423.74250000000000000000")
    assert verify_claims("You can safely pay 110423.74 today.", row)["ok"] is True
    assert verify_claims("You can safely pay 110423.7425 today.", row)["ok"] is False


def test_a_plan_leg_and_a_change_amount_are_compared_at_cents():
    row = _row(
        payment_plan="2024-12-15:32036.45|2025-01-15:17413.55",
        spending_changes_needed="reduce_to:event_5:250.999",
    )
    assert verify_claims(
        "Pay 32036.45 today and 17413.55 on 2025-01-15.", row
    )["ok"] is True
    # the CSV prints the action at cents, so that is the citable figure
    from code.main import _money_action

    # what the CSV prints for the action is what the claim check compares
    assert _money_action("reduce_to:event_5:250.999") == "reduce_to:event_5:250.99"
    assert verify_claims("Reduce event_5 to 250.99 so the request fits.", row)["ok"] is True
    assert verify_claims("Reduce event_5 to 250.999 so the request fits.", row)["ok"] is False


# --- the disagreement marker ----------------------------------------------

PREFIXES = ["", "> ", "- ", "1. ", "> 1. ", "\u2022 ", "\u25aa ", "\u00b7 ", "\u276f ",
            "\u2192 ", "\u203b ", "\u00bb ", "**", "_", "\u200b", "\u2060", "\uff1a"]


def _agreeing_candidate(prefix: str) -> dict:
    candidate = {key: ROW[key] for key in (
        "request_id", "amount_safe_to_pay", "affordability_status",
        "recommended_payment_method", "payment_plan",
        "earliest_date_for_full_payment", "spending_changes_needed",
    )}
    text = "Disagreements" + ("\uff1a" if prefix == "\uff1a" else ":") + " the plan holds."
    candidate.update(
        {"evidence_sufficient": True, "confidence": 0.4,
         "rationale": prefix + text if prefix != "\uff1a" else text}
    )
    return candidate


@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_decorated_marker_cannot_ship_as_candidate_prose(prefix):
    result = enforce_decision(
        _agreeing_candidate(prefix), dict(ROW), disagreement_note=True
    )
    assert result["verification"]["rationale_source"] in {"kernel", "disagreement"}


# --- trace redaction -------------------------------------------------------

SECRET_CASES = [
    "401 Received API Key = gwk_live_7f3a9c2e rejected",
    "invalid API key for abcdef123456",
    "sigv4 Sig=deadbeefdeadbeefdeadbeef",
    "url?sv=2022&sig=abcdef123456",
    "Cookie: theme=light; sessionid=opaque12345",
    "Set-Cookie: sid=abcdef123456",
    "sent cookie=session%3Dabcdef upstream",
    "connect failed for gwuser:gwpass@gateway.example.test:443",
    "the password is correct horse battery staple",
    'password="correct horse battery staple"',
    "password=hunter2xyz",
    "AIza" + "S" * 33,
    "AccountKey=abcdefghijkl==",
    "sk-" + "x" * 24,
    "Bearer abcdefghijklmnop",
    "Basic dXNlcjpwYXNz",
]


@pytest.mark.parametrize("text", SECRET_CASES)
def test_a_credential_shape_never_survives_sanitize(text):
    cleaned = sanitize({"e": text})["e"]
    assert "[REDACTED]" in cleaned, text


def test_long_and_repeated_private_keys_are_redacted():
    header = ("-" * 5) + "BEGIN RSA PRIVATE KEY" + ("-" * 5)
    footer = ("-" * 5) + "END RSA PRIVATE KEY" + ("-" * 5)
    body = "MIIE" + ("A" * 9000)
    assert "AAAAAAAA" not in sanitize({"e": header + "\n" + body + "\n" + footer})["e"]

    tokens = " ".join("sk-" + ("x%02d" % index) * 6 for index in range(60))
    assert "sk-" not in sanitize({"e": tokens})["e"]
