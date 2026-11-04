"""Review regressions for the free-text router and the model's confidence.

Two boundaries are pinned here. The router decides *what a clause is* before any
handler runs, so a precedence mistake becomes an action the user never asked
for: "why are there changes?" must be answered, not executed, and a negated
verb ("do not stop my subscription") must never reach the mutation boundary.
The classifier's own answer is untrusted input as well, so a boolean,
non-finite or out-of-range confidence is rejected rather than coerced.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from code.intent import parse_intent
from code.task_agent import STATUS_OK, Workspace, parse_instruction, run_task


# --- a stubbed workspace, so routing is tested without the kernel ----------

DATASET = {
    "requests": [
        {
            "request_id": "request_1",
            "user_id": "user_1",
            "request_date": "2025-08-03",
            "requested_amount": "1000",
        }
    ],
    "profiles": {
        "user_1": {"home_currency": "ZAR", "minimum_balance_to_keep": "100"}
    },
    "events_by_user": {
        "user_1": [{"event_id": "event_9", "category": "subscriptions"}]
    },
    "fx": [],
    "options": None,
    "messages": None,
    "warnings": [],
}

DECISION = {
    "request_id": "request_1",
    "amount_safe_to_pay": "400",
    "affordability_status": "affordable_later",
    "recommended_payment_method": "wait",
    "payment_plan": "none",
    "earliest_date_for_full_payment": "2025-09-01",
    "spending_changes_needed": "none",
    "decision_explanation": "Wait until the salary settles.",
}


class StubWorkspace(Workspace):
    """A workspace whose decisions are fixed, so no solver or dataset is needed."""

    def __init__(self):
        super().__init__(data=DATASET)

    def decide(self, request_id, session=None, cache=None):
        return dict(DECISION)


# --- the why-question outranks the change list it asks about ---------------


@pytest.mark.parametrize(
    "instruction",
    [
        "why are there changes?",
        "please explain the changes",
        "how come there are candidates for request_1?",
    ],
)
def test_a_why_word_outranks_the_noun_it_asks_about(instruction):
    (spec,) = parse_instruction(instruction)
    assert spec.kind == "explain"


def test_the_why_question_is_answered_and_never_lists_changes():
    workspace = StubWorkspace()
    workspace.current_request_id = "request_1"
    result = run_task("why are there changes?", workspace=workspace)
    (step,) = result.steps
    assert step.kind == "explain"
    assert step.status == STATUS_OK
    assert "decision" in step.data
    assert "candidates" not in step.data
    assert "baseline safe" not in step.detail


def test_a_leading_why_question_about_an_action_is_an_explanation_not_a_mutation():
    (spec,) = parse_instruction("Why stop event_9 for request_1?")
    assert spec.kind == "explain"

    workspace = StubWorkspace()
    result = run_task("Why stop event_9 for request_1?", workspace=workspace)
    (step,) = result.steps
    assert step.kind == "explain"
    assert step.status == STATUS_OK
    assert result.actions_applied == []
    assert result.changed == []
    assert workspace.session.overrides == {}


def test_the_imperative_form_of_the_same_action_still_routes_to_apply():
    (spec,) = parse_instruction("stop event_9 for request_1")
    assert spec.kind == "apply"
    assert spec.params["event_id"] == "event_9"
    assert spec.params["request_id"] == "request_1"


# --- a negated verb is never an instruction to mutate ----------------------


@pytest.mark.parametrize(
    "instruction",
    [
        "do not stop my subscription",
        "don't cancel the delivery membership",
        "never reduce groceries to 8,000",
        "do not kill the gym membership",
    ],
)
def test_a_negated_action_verb_is_not_an_apply(instruction):
    for spec in parse_instruction(instruction):
        assert spec.kind != "apply"


def test_a_negated_stop_applies_nothing_to_the_session():
    workspace = StubWorkspace()
    result = run_task("do not stop my subscription", workspace=workspace)
    assert result.actions_applied == []
    assert workspace.session.overrides == {}
    assert all(step.kind != "apply" for step in result.steps)


class FakeModel:
    def __init__(self, reply):
        self.reply = reply

    def invoke(self, messages):
        return SimpleNamespace(content=self.reply)


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply):
        self.reply = reply

    def chat_model(self):
        return FakeModel(self.reply)


#: A model answer that would be a real mutation if the clause were trusted.
ACTION_REPLY = (
    '{"intent": "action", "request_kind": "subscription_renewal", '
    '"target_request_id": "request_1", "target_event_id": "event_9", '
    '"ambiguity": "", "missing_parameters": "", "confidence": 0.9}'
)


def test_a_negated_clause_is_not_promoted_into_an_action_by_the_model():
    workspace = StubWorkspace()
    result = run_task(
        "do not stop my subscription", workspace=workspace, provider=FakeProvider(ACTION_REPLY)
    )
    assert all(step.kind != "apply" for step in result.steps)
    assert result.actions_applied == []
    assert workspace.session.overrides == {}


def test_the_router_still_reacts_to_a_plain_action_and_a_plain_change_list():
    (stop,) = parse_instruction("stop my delivery membership")
    assert stop.kind == "apply"
    (leaks,) = parse_instruction("where am I leaking money")
    assert leaks.kind == "changes"
    (reason,) = parse_instruction("why was request_101 refused?")
    assert reason.kind == "explain"


# --- the classifier's confidence is untrusted input ------------------------


def _payload(**overrides) -> dict:
    payload = {
        "intent": "question",
        "request_kind": "purchase",
        "target_request_id": "",
        "target_event_id": "",
        "ambiguity": "",
        "missing_parameters": "",
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "confidence",
    [True, False, "nan", "inf", "-inf", -0.1, 1.1, None],
)
def test_an_invalid_confidence_is_rejected_not_coerced(confidence):
    intent, errors = parse_intent(_payload(confidence=confidence))
    assert intent is None
    assert any("confidence" in message for message in errors)


@pytest.mark.parametrize("confidence", [0, 1, 0.0, 1.0, 0.5])
def test_a_real_probability_is_still_accepted(confidence):
    intent, errors = parse_intent(_payload(confidence=confidence))
    assert errors == []
    assert intent is not None
    assert Decimal(str(intent.confidence)) == Decimal(str(float(confidence)))
