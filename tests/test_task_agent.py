"""Tests for the conversational task engine (step 1: deterministic)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from code import task_agent
from code.task_agent import (
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_UNKNOWN,
    StepSpec,
    Workspace,
    extract_date,
    extract_money,
    parse_instruction,
    run_task,
)


# --- instruction parsing ---------------------------------------------------


def test_compound_instruction_splits_into_ordered_steps():
    spec = parse_instruction(
        "find where I am leaking money, then stop the delivery membership, "
        "then tell me if I can still pay rent on the 3rd"
    )
    kinds = [step.kind for step in spec]
    assert kinds == ["changes", "apply", "afford"]
    assert spec[-1].params["date"] == date(1, 1, 1) or isinstance(
        spec[-1].params["date"], date
    )


def test_afford_clause_extracts_amount_and_iso_date():
    (spec,) = parse_instruction("can I afford 40,000 on 2025-09-12?")
    assert spec.kind == "afford"
    assert spec.params["amount"] == Decimal("40000")
    assert spec.params["date"] == date(2025, 9, 12)


def test_day_ordinal_rolls_to_the_next_occurrence():
    reference = date(2025, 8, 20)
    (spec,) = parse_instruction("can I afford 5000 on the 12th", reference=reference)
    assert spec.params["date"] == date(2025, 9, 12)


def test_month_name_date_is_parsed():
    assert extract_date("pay it on 12 September") == date(date.today().year, 9, 12)


def test_money_ignores_request_ids_and_dates():
    assert extract_money("explain request_101 on 2025-09-12") is None
    assert extract_money("reduce groceries to 8,000") == Decimal("8000")


def test_stop_reduce_and_explain_are_classified_by_verb():
    (stop,) = parse_instruction("stop my delivery membership")
    assert stop.kind == "apply"
    assert stop.params["action"] == "stop"
    (reduce,) = parse_instruction("reduce groceries to 8,000")
    assert reduce.kind == "apply"
    assert reduce.params["action"] == "reduce_to"
    assert reduce.params["amount"] == Decimal("8000")
    (explain,) = parse_instruction("why was request_101 refused?")
    assert explain.kind == "explain"
    assert explain.params["request_id"] == "request_101"


def test_whatif_follow_up_is_recognised():
    (spec,) = parse_instruction("and if I wait until the 20th?")
    assert spec.kind == "whatif"
    assert isinstance(spec.params["date"], date)


def test_undo_and_unknown_verbs():
    (undo,) = parse_instruction("undo that")
    assert undo.kind == "undo"
    (odd,) = parse_instruction("make me a sandwich")
    assert odd.kind == "unknown"


def test_empty_instruction_produces_no_steps():
    assert parse_instruction("") == []
    assert parse_instruction("   ,  ") == []


# --- step execution with a stubbed solver ----------------------------------


class StubWorkspace(Workspace):
    """Workspace whose decisions are fixed, so no dataset or solver is needed."""

    def __init__(self, decisions, data=None):
        super().__init__(data=data or {
            "requests": [{"request_id": "request_1", "user_id": "user_1",
                          "request_date": "2025-08-03", "requested_amount": "1000"}],
            "profiles": {"user_1": {"home_currency": "ZAR",
                                    "minimum_balance_to_keep": "100"}},
            "events_by_user": {"user_1": []},
            "fx": [],
            "options": None,
            "messages": None,
            "warnings": [],
        })
        self.decisions = decisions
        self.solved = []

    def decide(self, request_id, session=None, cache=None):
        self.solved.append(str(request_id))
        overlay = (session or self.session).overrides.get(str(request_id), {})
        base = dict(self.decisions.get("request_1", {}))
        if "requested_amount" in overlay:
            base["amount_safe_to_pay"] = min(
                Decimal(str(base.get("amount_safe_to_pay", "0"))),
                Decimal(overlay["requested_amount"]),
            )
        return base


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


def test_afford_step_reports_the_kernel_number():
    workspace = StubWorkspace({"request_1": DECISION})
    result = run_task("can I afford 1000 on 2025-08-03", workspace=workspace)
    (step,) = result.steps
    assert step.status == STATUS_OK
    assert "not today, but yes later" in step.detail.lower()
    assert "400.00" in step.detail
    assert "2025-09-01" in step.detail
    assert result.changed[0]["status"] == "affordable_later"


def test_afford_without_request_uses_the_default_user():
    workspace = StubWorkspace({"request_1": DECISION})
    result = run_task("how much can I pay, afford 250", workspace=workspace)
    assert result.steps[0].status == STATUS_OK
    manual_id = result.steps[0].data["request_id"]
    assert manual_id.startswith("manual_")
    assert manual_id in workspace._manual


def test_unknown_clause_is_reported_not_guessed():
    workspace = StubWorkspace({"request_1": DECISION})
    result = run_task("book me a flight", workspace=workspace)
    assert result.steps[0].status == STATUS_UNKNOWN
    assert "did not understand" in result.steps[0].detail


def test_undo_reports_when_nothing_was_changed():
    workspace = StubWorkspace({"request_1": DECISION})
    result = run_task("undo", workspace=workspace)
    assert result.steps[0].status == STATUS_OK
    assert "cleared every session change" in result.steps[0].detail


def test_apply_refuses_a_target_it_cannot_resolve():
    workspace = StubWorkspace({"request_1": DECISION})

    def no_match(spec):
        return None, "I could not match that to a stoppable event"

    workspace.resolve_action = no_match  # type: ignore[assignment]
    result = run_task("stop something vague", workspace=workspace)
    assert result.steps[0].status == STATUS_REFUSED
    assert "could not match" in result.steps[0].detail


# --- real dataset integration ----------------------------------------------


@pytest.fixture(scope="module")
def real_workspace():
    return Workspace()


def test_real_explain_step_matches_the_shipped_decision(real_workspace):
    result = run_task("why was request_101 refused?", workspace=real_workspace)
    (step,) = result.steps
    assert step.status == STATUS_OK
    decision = step.data["decision"]
    assert decision["affordability_status"] == "not_affordable"
    assert decision["recommended_payment_method"] == "not_recommended"
    assert "request_101" in step.detail


def test_real_changes_step_never_invents_numbers(real_workspace):
    result = run_task("where am I leaking money for request_26", workspace=real_workspace)
    (step,) = result.steps
    assert step.status == STATUS_OK
    for candidate in step.data.get("candidates", []):
        assert candidate["action"].startswith(("stop:", "reduce_to:"))


def test_real_apply_then_undo_restores_the_original(real_workspace):
    original = real_workspace.decide("request_101")
    applied = run_task("apply stop event_9421 to request_101", workspace=real_workspace)
    step = applied.steps[0]
    if step.status == STATUS_OK:
        assert real_workspace.session.overrides.get("request_101")
    reverted = run_task("undo request_101", workspace=real_workspace)
    assert reverted.steps[0].status == STATUS_OK
    assert "request_101" not in real_workspace.session.overrides
    assert real_workspace.decide("request_101") == original


def test_trace_is_written_only_when_a_directory_is_given(tmp_path):
    workspace = StubWorkspace({"request_1": DECISION})
    quiet = run_task("undo", workspace=workspace)
    assert quiet.trace_path is None
    written = run_task("undo", workspace=workspace, trace_dir=tmp_path / "tasks")
    assert written.trace_path is not None
    payload = (tmp_path / "tasks").glob("*.json")
    assert next(payload).read_text(encoding="utf-8").startswith("{")
