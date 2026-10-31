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
    # "the 3rd" is relative, so it is resolved by the step handler against the
    # target request's date, not against today() at parse time.
    assert spec[-1].params["date"] is None


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
    assert extract_date("pay it on 12 September", reference=date(2026, 8, 1)) == date(
        2026, 9, 12
    )


def test_a_month_name_never_resolves_to_a_past_date():
    # Asked in October, "12 September" is next September, not last month.
    assert extract_date("pay it on 12 September", reference=date(2026, 10, 31)) == date(
        2027, 9, 12
    )
    assert extract_date("pay it on 12 September", reference=date(2026, 9, 12)) == date(
        2026, 9, 12
    )


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
    assert spec.params["date"] is None
    assert spec.params["amount"] is None


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


def test_locale_amounts_are_read_by_the_locale_parser():
    from code.task_agent import _money_value

    assert _money_value("1.050,00") == Decimal("1050.00")
    assert _money_value("40,000") == Decimal("40000")
    assert _money_value("1500.50") == Decimal("1500.50")
    assert _money_value("5.000") == Decimal("5.000")
    assert _money_value("", "EUR", "es") is None


def test_extract_money_reads_an_eu_formatted_figure():
    assert extract_money("can I afford 1.050,00 on the 12th") == Decimal("1050.00")

def test_the_task_trace_never_stores_the_typed_instruction(tmp_path):
    from code.task_agent import StepResult, TaskResult, _write_trace

    secret = "can I afford 1.050,00 for the medical bill"
    result = TaskResult(
        task_id="task_test",
        instruction=secret,
        steps=[StepResult("afford", secret, "ok", f"you can pay 1.050,00")],
        summary=f"you can pay 1.050,00",
    )
    written = _write_trace(result, tmp_path)
    assert written is not None
    text = (tmp_path / "task_test.json").read_text(encoding="utf-8")
    assert secret not in text
    assert "1.050,00" not in text
    assert '"sha256"' in text


def test_the_task_trace_redacts_a_credential_in_a_provider_error(tmp_path):
    from code.task_agent import TaskResult, _write_trace

    result = TaskResult(
        task_id="task_secret",
        instruction="ask",
        concerns=[
            "RuntimeError: https" "://tenant:pa55w0rd@gateway.invalid/v1 rejected "
            "sk-or-v1-SECRETKEY0123456789"
        ],
    )
    written = _write_trace(result, tmp_path)
    text = (tmp_path / "task_secret.json").read_text(encoding="utf-8")
    assert written is not None
    assert "pa55w0rd" not in text
    assert "SECRETKEY0123456789" not in text

def test_a_manual_afford_is_what_the_next_whatif_tests():
    workspace = Workspace()
    result = run_task(
        "can I afford 40,000 on the 12th then what if I wait until the 20th",
        workspace=workspace,
    )
    kinds = [step.kind for step in result.steps]
    assert kinds[:2] == ["afford", "whatif"]
    # The follow-up must target the request the manual question created, not an
    # earlier request or nothing at all.
    assert workspace.current_request_id
    assert workspace.current_request_id.startswith("manual_")

def test_february_29_resolves_to_the_next_leap_year():
    reference = date(2026, 1, 15)
    assert extract_date("29 February", reference=reference) == date(2028, 2, 29)
    assert extract_date("29 February", reference=date(2028, 1, 15)) == date(2028, 2, 29)


def test_the_task_trace_keeps_the_action_but_not_the_amount(tmp_path):
    from code.task_agent import TaskResult, _write_trace

    result = TaskResult(
        task_id="task_action",
        instruction="reduce the gym to 40000",
        actions_applied=[
            {"request_id": "request_1", "action": "reduce_to:evt_003:40000.00"}
        ],
    )
    assert _write_trace(result, tmp_path) is not None
    text = (tmp_path / "task_action.json").read_text(encoding="utf-8")
    assert "reduce_to:evt_003" in text
    assert "40000" not in text


def test_the_task_trace_keeps_the_intent_source_and_no_clause_text(tmp_path):
    from code.task_agent import StepResult, TaskResult, _write_trace

    step = StepResult(
        "afford",
        "can I afford 1.050,00 for the dentist",
        "ok",
        "you can pay 1.050,00",
        {"intent": {"intent": "afford", "source": "model", "confidence": 0.8}},
    )
    result = TaskResult(task_id="task_intent", instruction="ask", steps=[step])
    assert _write_trace(result, tmp_path) is not None
    text = (tmp_path / "task_intent.json").read_text(encoding="utf-8")
    assert '"source": "model"' in text
    assert "dentist" not in text
    assert "1.050,00" not in text


def test_a_manual_id_never_reuses_one_the_tui_already_made(tmp_path):
    workspace = Workspace()
    workspace.data["requests"] = list(workspace.data["requests"]) + [
        {"request_id": "manual_0001", "user_id": "user_001"}
    ]
    request_id = workspace.add_manual(1000, date(2025, 9, 1))
    assert request_id == "manual_0002"


def test_a_relative_date_is_left_for_the_step_handler_to_resolve():
    (relative,) = parse_instruction("can I afford 1.050,00 on the 12th")
    # Not resolved at parse time: "the 12th" depends on the target request's
    # own date, and resolving it against today() would make the same
    # instruction decide differently on different days.
    assert relative.params["date"] is None

    (absolute,) = parse_instruction("can I afford 1.050,00 on 2025-09-12")
    assert absolute.params["date"] == date(2025, 9, 12)


def test_the_task_trace_keeps_no_amounts_from_changed(tmp_path):
    from code.task_agent import TaskResult, _write_trace

    result = TaskResult(
        task_id="task_changed",
        instruction="can I afford 40000",
        changed=[
            {
                "request_id": "request_1",
                "safe_amount": "40000.00",
                "earliest_date": "2025-09-12",
                "fields": {"amount_safe_to_pay": ("1", "2")},
            }
        ],
    )
    assert _write_trace(result, tmp_path) is not None
    text = (tmp_path / "task_changed.json").read_text(encoding="utf-8")
    assert "request_1" in text
    assert "40000" not in text
def test_a_relative_whatif_date_uses_the_request_date_not_today():
    workspace = StubWorkspace({"request_1": DECISION})
    result = run_task("what if request_1 waits until the 20th", workspace=workspace)
    (step,) = result.steps
    # request_1 is dated 2025-08-03, so "the 20th" is 2025-08-20 whatever today
    # happens to be.
    assert "2025-08-20" in step.detail
def test_a_waiting_period_is_never_read_as_an_amount():
    (days,) = parse_instruction("what if request_1 waits 30 days")
    assert days.params["amount"] is None
    (weeks,) = parse_instruction("what if I wait 2 weeks")
    assert weeks.params["amount"] is None
    # Stripping the duration must not eat a real amount in the same clause.
    (both,) = parse_instruction("can I afford 1000 in 30 days")
    assert both.params["amount"] == Decimal("1000")
