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


# --- credentials the project itself calls secrets ---------------------------

CREDENTIAL_ASSIGNMENTS = [
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    "secret_access_key=abc123def456",
    "session_token=zzz111yyy222",
    "access_key=AKIAIOSFODNN7EXAMPLE",
    "x-amz-security-token: FwoGZXIvYXdzE",
    "conn Password=hunter2xyz",
]


@pytest.mark.parametrize("text", CREDENTIAL_ASSIGNMENTS)
def test_a_credential_assignment_named_by_the_project_is_redacted(text):
    cleaned = sanitize({"e": text})["e"]
    assert "[REDACTED]" in cleaned, text


@pytest.mark.parametrize("text", ["total_tokens=12", "input_tokens=99", "max_tokens=8"])
def test_a_token_counter_is_not_a_credential(text):
    assert sanitize({"e": text})["e"] == text


def test_a_recorded_sequence_does_not_void_the_trace(tmp_path):
    from code.trace import TraceRecorder

    recorder = TraceRecorder("probe", tmp_path)
    recorder.begin("request_probe")
    event = recorder.record("concerns", [{"kind": "budget", "detail": "x", "count": 2}])
    assert event is not None
    assert event.payload == {"items": [{"kind": "budget", "detail": "x", "count": 2}]}
    assert recorder.record("verification", {"ok": True}).payload == {"ok": True}


def test_a_key_loaded_from_auth_json_is_registered(tmp_path):
    import json

    from code.jev import load_api_key
    from code.trace import sanitize

    secret = "zen-live-0f9a8b7c6d5e4f3a"
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"opencode": {"key": secret}}), encoding="utf-8")
    key, source = load_api_key({"OPENCODE_AUTH_JSON": str(auth), "OPENCODE_API_KEY": ""})
    assert key == secret, source
    assert sanitize({"e": f"403 rejected {secret}"})["e"] == "403 rejected [REDACTED]"


# --- naming the action, rather than denying it ------------------------------

ACTION_ROW = _row(
    request_id="request_34",
    amount_safe_to_pay="0",
    affordability_status="affordable_with_plan",
    recommended_payment_method="partial_payment",
    payment_plan="2025-08-03:0|2026-07-14:50000",
    earliest_date_for_full_payment="2026-07-14",
    spending_changes_needed="stop:event_3085",
    decision_explanation="Keep the plan and stop event_3085.",
)

UNNAMED_DENIALS = [
    "You can keep every subscription.",
    "Spending continues as before.",
    "Everything stays the same.",
    "Nothing needs rearranging.",
    "There is no need for any savings.",
    "Your outgoings are fine as they are.",
    "You can carry on spending as usual.",
    "No belt-tightening is required.",
    "Nothing has to give.",
    "Your recurring costs are unaffected.",
    "You will not need to skip any payment.",
    "Your commitments are safe as they are.",
    "You can keep spending exactly as you do now.",
]


def _candidate_for(row, rationale):
    candidate = {
        key: row[key]
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
        {"evidence_sufficient": True, "confidence": 0.4, "rationale": rationale}
    )
    return candidate


@pytest.mark.parametrize("rationale", UNNAMED_DENIALS)
def test_an_explanation_that_never_names_the_action_cannot_ship(rationale):
    result = enforce_decision(
        _candidate_for(ACTION_ROW, rationale), dict(ACTION_ROW), disagreement_note=True
    )
    assert result["verification"]["rationale_source"] != "candidate", rationale


@pytest.mark.parametrize(
    "rationale",
    [
        "Stop event_3085 and nothing else changes.",
        "Apart from stopping event_3085, nothing changes.",
        "The budget remains above the minimum after event_3085 is stopped.",
        "Your plan stays within the minimum balance once event_3085 is stopped.",
    ],
)
def test_an_explanation_that_names_the_action_ships(rationale):
    result = enforce_decision(
        _candidate_for(ACTION_ROW, rationale), dict(ACTION_ROW), disagreement_note=True
    )
    assert result["verification"]["rationale_source"] == "candidate", rationale


def test_a_full_payment_recommendation_is_not_a_column_reference():
    row = _row(
        recommended_payment_method="installments",
        spending_changes_needed="none",
        earliest_date_for_full_payment="2026-07-14",
    )
    assert verify_claims("You can pay in full on 2026-06-11.", row)["ok"] is False
    assert (
        verify_claims("The first safe full payment is 2026-07-14.", row)["ok"] is True
    )


def test_a_compatibility_form_of_the_marker_is_caught():
    from code.verifier import _opens_with_marker

    full_width = "\uff24\uff49\uff53\uff41\uff47\uff52\uff45\uff45\uff4d\uff45\uff4e\uff54\uff53\uff1a the plan is wrong."
    assert _opens_with_marker(full_width) is True
    assert _opens_with_marker("Disagreements: the plan is wrong.") is True
    assert _opens_with_marker("The plan holds.") is False

# --- the citation rule needs a complete identifier -------------------------

def test_a_required_action_is_satisfied_only_by_its_own_event_id():
    from code.claims import cites_every_action

    assert cites_every_action("we should stop event_9", "stop:event_9") is True
    assert cites_every_action("we should stop event_90", "stop:event_9") is False
    # a word-character suffix is a different identifier too
    assert cites_every_action("we should stop event_9_backup", "stop:event_9") is False
    assert cites_every_action("nothing here", "stop:event_9") is False
    assert cites_every_action("anything", "none") is True


@pytest.mark.parametrize(
    "sentence,expected",
    [
        # naming the action does not excuse an explicit denial beside it
        ("No spending changes are needed. Stop event_9.", False),
        ("You need not stop any spending.", False),
        # a denial scoped to *no other* changes is true on this row
        ("Stop event_9 and nothing else changes.", True),
        ("No other changes are needed once you stop event_9.", True),
        ("Apart from stopping event_9, nothing changes.", True),
        # the scoping word has to qualify *this* denial, not the next sentence
        ("No spending changes are needed. Other expenses stay the same. Stop event_9.", False),
    ],
)
def test_a_denial_is_excused_only_when_it_is_scoped_to_no_other_changes(sentence, expected):
    row = _row(spending_changes_needed="stop:event_9")
    assert verify_claims(sentence, row)["ok"] is expected


def test_an_action_amount_is_compared_at_the_cents_the_csv_prints():
    row = _row(spending_changes_needed="reduce_to:event_5:250.999")
    assert verify_claims("Reduce event_5 to 250.99 so the request fits.", row)["ok"] is True
    assert verify_claims("Reduce event_5 to 250.999 so the request fits.", row)["ok"] is False


def test_a_scope_word_must_qualify_the_denied_change():

    row = dict(ROW)
    row["spending_changes_needed"] = "stop:event_9"
    # "other" modifies "options", so the denial stands on its own.
    assert verify_claims(
        "No spending changes are needed, but other options include stopping event_9",
        row,
    )["ok"] is False
    for sentence in (
        "No other changes are needed once you stop event_9.",
        "Stop event_9 and nothing else changes.",
        "Apart from stopping event_9, nothing changes.",
        "Besides stopping event_9, nothing else changes.",
        "Nothing further is required beyond the stop on event_9.",
    ):
        assert verify_claims(sentence, row)["ok"] is True, sentence


def test_an_incomplete_action_is_a_claim_not_a_crash():
    from code.claims import _normalise_action

    assert _normalise_action("reduce_to:event_5") == "reduce_to:event_5:"
    row = dict(ROW)
    row["spending_changes_needed"] = "reduce_to:event_5:250.999"
    report = verify_claims("Apply reduce_to:event_5 so the request fits.", row)
    assert report["ok"] is False
    assert [item["kind"] for item in report["unsupported"]] == ["action"]
