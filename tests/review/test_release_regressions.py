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
    # The gate now also asks the rationale to state the row's own status and
    # method, so the sentence does; the action citation is what is under test.
    stated = " ".join(
        (
            rationale,
            "This is",
            str(ACTION_ROW["affordability_status"]).replace("_", " "),
            "and",
            {
                "full_payment": "full payment",
                "partial_payment": "partial payment",
                "installments": "installments",
                "wait": "wait",
                "not_recommended": "not recommended",
            }[ACTION_ROW["recommended_payment_method"]],
            "is the recommendation.",
        )
    )
    result = enforce_decision(
        _candidate_for(ACTION_ROW, stated), dict(ACTION_ROW), disagreement_note=True
    )
    assert result["verification"]["rationale_source"] == "candidate", stated


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
    # "no other" has to attach to a change, not to any noun that follows it.
    assert verify_claims(
        "No spending changes are needed, but no other options include stopping event_9.",
        row,
    )["ok"] is False
    for sentence in (
        "No other changes are needed once you stop event_9.",
        "Stop event_9 and nothing else changes.",
        # a modal between the qualifier and the change still scopes it
        "Stop event_9 and nothing else should change.",
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


def test_redaction_covers_camel_assignments_and_json_bodies():
    """A gateway echoes its rejection body, and that body is what gets persisted."""
    from code.trace import sanitize

    cases = [
        "sessionId=abcdef123456789",
        '{"sessionId": "abcdef1234567890abcdef"}',
        "passwordHash=abcdef123456789",
        "SessionId=abcdef123456",
        '{"sessionToken": "abcdef123456"}',
        '{"apiKey": "abcdef123456"}',
    ]
    for text in cases:
        cleaned = sanitize({"e": text})["e"]
        assert "abcdef" not in cleaned, text
        assert "123456" not in cleaned, text

    # A counter is not a credential, and prose is not an assignment.
    for text in ("total_tokens=1234", "input_tokens=99", '{"total_tokens": 42}',
                 "account balance 500000"):
        assert sanitize({"e": text})["e"] == text


def test_the_scrubber_stays_linear_on_a_long_alphanumeric_body():
    """A bounded preview of a 65 KB body must not cost quadratic time."""
    import time

    from code.trace import sanitize

    # Each body carries the separator that makes the assignment pattern run.
    # Without one the scrubber takes the fast path and the assertion cannot
    # fail, which is how a quadratic branch survived eleven review rounds.
    bodies = (
        "a1b2c3d4e5f6" * 5461 + ":",
        "a-b-" * 16384 + "=",
        "Error: " + "-" * 65000 + ":",
        "x+" * 30000 + "@",
        "http://gw.example/" + "a-a-" * 6000 + "=",
        # Colon-dense: the value run must not be able to swallow the rest of
        # the string from every offset, which is what made this quadratic.
        "a:1," * 20000,
    )
    for body in bodies:
        started = time.monotonic()
        sanitize({"e": body})
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, (len(body), elapsed)

    # And the branch is still doing its job on the same shape: the credential
    # is gone and the host survives, whichever spelling of the separator it was.
    for text in ("https://user:hunter2value@gw.example", "user:hunter2value@gw.example"):
        scrubbed = sanitize({"e": text})["e"]
        assert "hunter2value" not in scrubbed, text
        assert scrubbed.endswith("gw.example"), scrubbed


def test_the_earliest_date_statement_is_checked_in_every_spelling():
    """The statement names one column, so a written date is checked too."""
    row = _row(
        affordability_status="affordable_later",
        recommended_payment_method="wait",
        payment_plan="2026-07-14:15656000",
        earliest_date_for_full_payment="2026-07-14",
    )
    assert verify_claims("The earliest full payment is 14 July 2026.", row)["ok"] is True
    assert verify_claims("The first safe full payment is 2026-07-14.", row)["ok"] is True
    assert verify_claims("The earliest full payment is 11 June 2026.", row)["ok"] is False
    assert verify_claims("The first safe full payment is 2026-06-11.", row)["ok"] is False

    blank = dict(row)
    blank["earliest_date_for_full_payment"] = ""
    assert verify_claims("The first safe full payment is 2026-07-14.", blank)["ok"] is False


def test_a_repeated_phrase_is_judged_where_it_appears():
    """One date statement must not exempt the other occurrence of the phrase."""
    row = _row(
        affordability_status="affordable_later",
        recommended_payment_method="wait",
        payment_plan="2026-07-14:15656000",
        earliest_date_for_full_payment="2026-07-14",
    )
    assert verify_claims(
        "The first safe full payment is 2026-07-14.", row
    )["ok"] is True
    report = verify_claims(
        "The first safe full payment is 2026-07-14. A full payment on 2026-06-11 also works.",
        row,
    )
    assert report["ok"] is False
    assert [item["kind"] for item in report["unsupported"]] == ["date", "method"]


def test_the_first_date_a_sentence_cites_is_the_one_checked():
    """A written date before a numeric one is the claim, not the numeric one."""
    from code.claims import _cited_date

    assert _cited_date("The earliest full payment is 11 June 2026, not 2026-07-14") == (
        "2026-06-11"
    )
    assert _cited_date("2026-07-14 is the date, not 11 June 2026") == "2026-07-14"

    row = _row(
        affordability_status="affordable_with_plan",
        recommended_payment_method="installments",
        payment_plan="2026-06-11:32036.45|2026-07-11:17413.55",
        earliest_date_for_full_payment="2026-07-14",
    )
    report = verify_claims("The earliest full payment is 11 June 2026, not 2026-07-14", row)
    assert [item["kind"] for item in report["unsupported"]] == ["date"]


def test_naming_the_earliest_date_is_not_naming_the_recommendation():
    from code.claims import states_the_decision

    row = _row(
        affordability_status="affordable_with_plan",
        recommended_payment_method="installments",
        payment_plan="2026-06-11:32036.45|2026-07-11:17413.55",
        earliest_date_for_full_payment="2026-07-14",
    )
    states = "This is affordable with a plan and installments are the recommendation."
    assert states_the_decision(states, row) is True
    # the status is named, the recommendation is not
    assert states_the_decision(
        "This is affordable with a plan; the earliest full payment is 2026-07-14.", row
    ) is False


# --- the final correctness pass -------------------------------------------


def test_a_scope_word_attached_to_a_later_denial_does_not_excuse_this_one():
    row = _row(spending_changes_needed="stop:event_9")
    for text in (
        "No spending changes are needed, and nothing else changes apart from stop event_9.",
        "No spending changes are needed, though nothing else changes apart from stop event_9.",
        "No spending changes are needed.",
    ):
        assert verify_claims(text, row)["ok"] is False, text
    for text in (
        "Stop event_9 and nothing else changes.",
        "No other changes are needed once you stop event_9.",
        "Apart from stopping event_9, nothing changes.",
        "Besides stopping event_9, nothing else changes.",
    ):
        assert verify_claims(text, row)["ok"] is True, text


def test_the_teens_are_figures_too():
    for text in ("About fifteen thousand is safe.", "About twenty thousand is safe."):
        assert verify_claims(text, _row())["ok"] is False, text


def test_a_spelled_duration_and_a_bare_month_are_date_claims():
    for text in ("Pay it in two weeks.", "Pay within a fortnight.", "Pay by August."):
        assert verify_claims(text, _row())["ok"] is False, text
    assert verify_claims("Pay on 2025-08-03.", _row())["ok"] is True


def test_a_count_noun_excuses_a_count_not_a_fortune():
    assert verify_claims("You would need 99999 payments to cover it.", _row())["ok"] is False


def test_an_earliest_date_statement_survives_more_wordings():
    row = _row(
        affordability_status="affordable_later",
        recommended_payment_method="wait",
        earliest_date_for_full_payment="2026-07-14",
    )
    for text in (
        "The earliest safe date for a full payment is 2026-07-14.",
        "The first safe date for a full payment is 2026-07-14.",
        "The first safe full payment is 2026-07-14.",
    ):
        assert verify_claims(text, row)["ok"] is True, text
    wrong = verify_claims("The earliest safe date for a full payment is 2026-06-11.", row)
    assert wrong["ok"] is False


def test_citing_evidence_is_not_recommending_a_change():
    row = _row(
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
    )
    text = "Not recommended because financial evidence for event_9421 remains unresolved."
    assert verify_claims(text, row)["ok"] is True
    # A change verb pointing at a foreign event is still a claim.
    action_row = _row(spending_changes_needed="stop:event_9")
    assert verify_claims("Also stop event_9421 instead.", action_row)["ok"] is False


def test_the_batch_writer_falls_back_when_its_prose_cannot_be_supported():
    from code.main import _checked_explanation

    row = dict(ROW)
    row["decision_explanation"] = "The kernel's own sentence."
    assert _checked_explanation("You can pay 99999999 today.", row) == (
        "The kernel's own sentence."
    )
    assert _checked_explanation("Pay the full amount.", row) == (
        "The kernel's own sentence."
    )
    stated = "This is affordable now and full payment is the recommendation."
    assert _checked_explanation(stated, row) == stated
    assert _checked_explanation("", row) == "The kernel's own sentence."


def test_every_private_key_is_cut_not_just_the_first_two_hundred():
    from code.trace import sanitize

    header = "-" * 5 + "BEGIN RSA PRIVATE KEY" + "-" * 5
    footer = "-" * 5 + "END RSA PRIVATE KEY" + "-" * 5
    body = header + "\nMIIE" + "A" * 40 + "\n" + footer + "\n"
    cleaned = sanitize({"e": body * 300 + "tail"})["e"]
    assert "MIIE" not in cleaned
    assert "AAAA" not in cleaned


def test_a_camel_case_cookie_assignment_keeps_no_value():
    from code.trace import sanitize

    for text in ("setCookie=abcdef123456", "idCookie=abcdef123456", "cookie=abcdef123456"):
        cleaned = sanitize({"e": text})["e"]
        assert "abcdef123456" not in cleaned, text
    assert sanitize({"e": "total_tokens=42"})["e"] == "total_tokens=42"


def test_a_qualifier_binds_to_the_denial_it_excuses():
    row = _row(spending_changes_needed="stop:event_9")
    for text in (
        "No spending changes are needed because no other changes are needed.",
        "Apart from stopping event_9, you don't need to stop event_9.",
    ):
        assert verify_claims(text, row)["ok"] is False, text
    for text in (
        "Stop event_9 and nothing else changes.",
        "Apart from stopping event_9, nothing changes.",
        "No other changes are needed once you stop event_9.",
    ):
        assert verify_claims(text, row)["ok"] is True, text


def test_a_small_leading_count_is_read_whole():
    report = verify_claims("We checked 3 of 5 samples.", _row())
    assert [item for item in report["unsupported"] if item["kind"] == "amount"] == []


def test_a_long_username_still_keeps_its_password_out_of_a_trace():
    from code.trace import sanitize

    cleaned = sanitize({"e": "a" * 100 + ":hunter2value@gateway.example"})["e"]
    assert "hunter2value" not in cleaned


def test_a_rejected_explanation_falls_back_to_the_kernel_text():
    from code.main import _checked_explanation

    row = dict(ROW)
    row["decision_explanation"] = "The kernel's own sentence."
    assert _checked_explanation(
        "You can safely pay 110423.7425 today.", row
    ) == "The kernel's own sentence."


def test_a_long_scheme_less_username_still_redacts_its_password():
    from code.trace import sanitize

    for size in (63, 64, 65, 200):
        user = "u" * size
        scrubbed = sanitize({"e": f"{user}:TOPSECRETVALUE@gateway.example"})["e"]
        assert "TOPSECRETVALUE" not in scrubbed, size
        assert scrubbed.endswith("gateway.example"), scrubbed


def test_a_token_heuristic_consumes_the_whole_token():
    from code.trace import sanitize

    for text in (
        "Authorization: Bearer abc+def/ghi+jkl",
        "Bearer abcdefgh==ijklmnop",
        "Basic dXNlcjpwYXNz-d29yZA",
    ):
        scrubbed = sanitize({"e": text})["e"]
        assert "abc+" not in scrubbed and "ijklmnop" not in scrubbed, text
        assert "d29yZA" not in scrubbed, text


def test_tab_armour_and_a_putty_key_are_redacted():
    from code.trace import sanitize

    tabbed = (("-" * 5) + "BEGIN\tRSA PRIVATE KEY" + ("-" * 5)
              + "\nMIIEexamplebody\n"
              + ("-" * 5) + "END\tRSA PRIVATE KEY" + ("-" * 5))
    assert "MIIEexamplebody" not in sanitize({"e": tabbed})["e"]

    ppk = ("PuTTY-User-Key-File-2: ssh-rsa\nPrivate-Lines: 2\n"
           "AAAAB3NzaC1yc2EAAAADAQAB\nAQDexamplebody12345678\n")
    assert "AQDexamplebody12345678" not in sanitize({"e": ppk})["e"]


def test_the_environment_key_is_registered_as_a_secret(monkeypatch):
    from code import jev
    from code.trace import REDACTED, _KNOWN_SECRETS, sanitize

    monkeypatch.setenv("OPENCODE_API_KEY", "envkeyvalue12345")
    jev.load_api_key()
    assert "envkeyvalue12345" in _KNOWN_SECRETS
    assert sanitize({"e": "403 key envkeyvalue12345 rejected"})["e"] == (
        f"403 key {REDACTED} rejected"
    )


# --- the final correctness blockers ---------------------------------------


def test_denying_the_recommended_method_is_a_claim():
    for method, text in (
        ("full_payment", "Do not pay in full now."),
        ("installments", "Do not use installments."),
        ("partial_payment", "Do not pay part of it now."),
        ("wait", "You should not wait."),
    ):
        row = _row(recommended_payment_method=method)
        assert verify_claims(text, row)["ok"] is False, text


def test_the_printed_second_leg_is_a_supported_figure():
    row = _row(
        recommended_payment_method="partial_payment",
        amount_safe_to_pay="32036.45",
        payment_plan="2024-12-03:32036.455|2024-12-15:17413.545",
        earliest_date_for_full_payment="2024-12-15",
    )
    # The CSV prints the residual of the printed first leg, not its truncation.
    assert verify_claims("Pay 32036.45 today and 17413.55 on 2024-12-15.", row)["ok"] is True


def test_a_figure_in_the_wrong_role_is_a_claim():
    requested = _row(requested_amount="15657000")
    assert verify_claims(
        "The request of 15657000 is safe to pay today.", requested
    )["ok"] is False
    floor = _row(minimum_balance="9000")
    assert verify_claims(
        "You can pay up to 9000 and still keep the minimum.", floor
    )["ok"] is False


def test_the_event_window_looks_both_ways():
    row = _row(spending_changes_needed="stop:event_3085")
    assert verify_claims("Cancel event_99999 instead.", row)["ok"] is False
    assert verify_claims("event_99999 should be stopped.", row)["ok"] is False
    # An evidence citation carries no change verb, so it is not an instruction.
    assert verify_claims(
        "The event_99999 charge remains unresolved.", row
    )["ok"] is True


def test_an_article_figure_and_a_vague_span_are_claims():
    assert verify_claims("a thousand is safe today", _row())["ok"] is False
    assert verify_claims("a couple hundred is safe today", _row())["ok"] is False
    for text in ("Wait until Monday.", "It becomes safe in a week.",
                 "Check again later this week.", "The safe point is mid-month."):
        assert verify_claims(text, _row())["ok"] is False, text


def test_the_natural_wait_phrasing_ships():
    row = _row(
        affordability_status="affordable_later",
        recommended_payment_method="wait",
        earliest_date_for_full_payment="2026-05-15",
    )
    assert verify_claims("Wait until 2026-05-15 and then pay in full.", row)["ok"] is True
    assert verify_claims("The plan pays in full on 2026-05-15.", row)["ok"] is True


# --- the round-nineteen findings, pinned at their real edge ------------------


def test_a_scheme_less_userinfo_is_redacted_whatever_the_run_length():
    # The review's case is a 65-character username; a real credential can be
    # longer, and neither the username nor the password may have a fixed cap.
    for size in (65, 300):
        for password in ("hunter2value", "p" * 100):
            user = "a" * size
            cleaned = sanitize({"e": f"{user}:{password}@gateway.example"})["e"]
            assert password not in cleaned, (size, len(password))
            assert cleaned.endswith("gateway.example"), cleaned


def test_a_long_username_run_is_scanned_once_not_at_every_offset():
    import time

    # A run of username characters with no colon used to let the engine start
    # a match at every offset; the start-anchored lookbehind gives it one.
    body = "-+%~" * 100_000 + ":"
    started = time.monotonic()
    sanitize({"e": body})
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, (len(body), elapsed)
