"""The chat's what-if tool runs on :mod:`code.scenarios` (issue #14).

Every hypothesis goes through :func:`code.scenarios.simulate_scenario`, which
deep-copies the account, applies the guards and reports ``persisted: False``.
The tool keeps its name and its flat decision response; the scenario envelope
adds ``persisted`` and ``assumptions``.  A fresh hypothetical purchase keeps the
older ``amount``/``on``/``by`` arguments and reaches the same guarded path.
"""
from __future__ import annotations

import copy

import pytest

import code.accounts as store
from code import chat, service


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


@pytest.fixture()
def account(db):
    """A committed bill plus one decided purchase."""
    acc = service.create_user("ana", currency="ZAR", balance="12000", floor="2000")
    rent = service.add_fact(
        acc,
        "bill",
        "8000",
        when="2026-11-03",
        description="rent",
        category="rent",
    )
    decision = service.buy(
        acc, "900", on="2026-11-01", by="2026-12-20", text="headphones",
    )
    return acc, decision["request_id"], rent["event_id"]


# ------------------------------------------------------------- the delay path


def test_a_delay_scenario_returns_a_kernel_result_and_persists_nothing(account):
    acc, request_id, _rent_id = account
    before = copy.deepcopy(acc)

    result = chat.dispatch(
        acc, "what_if", {"request_id": request_id, "delay_days": 19},
    )

    assert result["persisted"] is False
    assert result["affordability_status"] == "affordable_now"
    assert result["earliest_date_for_full_payment"] == "2026-11-20"
    assert result["payment_plan"] == "2026-11-20:900"
    assert isinstance(result["assumptions"], list) and result["assumptions"]
    assert all(isinstance(line, str) and line for line in result["assumptions"])
    assert acc == before, "a hypothesis moved the caller's account"


def test_pay_on_moves_a_recorded_request_through_the_same_path(account):
    acc, request_id, _rent_id = account

    result = chat.dispatch(
        acc, "what_if", {"request_id": request_id, "on": "2026-11-20"},
    )

    assert result["persisted"] is False
    assert result["earliest_date_for_full_payment"] == "2026-11-20"
    assert acc["requests"][0]["request_date"] == "2026-11-01"


# ------------------------------------------------------------- the guards


def test_an_unsafe_scenario_is_refused_with_the_guard_message(account):
    acc, request_id, rent_id = account
    service.update_profile(acc, expense_categories_to_protect="rent")
    before = copy.deepcopy(acc)

    with pytest.raises(ValueError) as caught:
        chat.dispatch(
            acc,
            "what_if",
            {"request_id": request_id, "event_id": rent_id, "new_amount": "0"},
        )

    message = str(caught.value)
    assert "rent" in message
    assert "protect" in message
    assert acc == before


def test_a_scenario_naming_another_account_is_refused(account):
    acc, request_id, _rent_id = account
    acc["requests"][0]["user_id"] = "someone-else"

    with pytest.raises(ValueError) as caught:
        chat.dispatch(acc, "what_if", {"request_id": request_id, "delay_days": 1})

    assert "another account" in str(caught.value)


# ------------------------------------------------------- the older amount path


def test_a_fresh_purchase_keeps_the_flat_response_and_adds_the_scenario_flags(account):
    acc, _request_id, _rent_id = account

    result = chat.dispatch(
        acc, "what_if", {"amount": "500.00", "on": "2026-11-01"},
    )

    for field in (
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    ):
        assert field in result, field
    assert result["persisted"] is False
    assert isinstance(result["assumptions"], list) and result["assumptions"]
    assert len(acc["requests"]) == 1, "the hypothetical purchase was recorded"


# ------------------------------------------------------------- the protocol


def test_the_schema_publishes_the_scenario_arguments():
    schema = next(
        item for item in chat.TOOL_SCHEMAS
        if item["function"]["name"] == "what_if"
    )
    parameters = schema["function"]["parameters"]

    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert {
        "amount", "on", "by", "request_id", "delay_days", "event_id", "new_amount",
    } <= set(parameters["properties"])
    assert chat.validate_tool_call(
        "what_if", {"request_id": "request_1", "delay_days": 3},
    ) == []
    assert chat.validate_tool_call(
        "what_if", {"request_id": "request_1", "on": "2026-11-20"},
    ) == []


def test_a_what_if_without_an_amount_or_request_is_rejected():
    assert chat.validate_tool_call("what_if", {}) == ["BAD_ARGS"]
    assert chat.validate_tool_call(
        "what_if", {"request_id": "request_1", "delay_days": True},
    ) == ["BAD_ARGS"]
    assert chat.validate_tool_call(
        "what_if", {"request_id": "request_1", "delay_days": 2, "on": "2026-11-20"},
    ) == ["BAD_ARGS"]
    assert chat.validate_tool_call(
        "what_if", {"request_id": "request_1", "amount": "500"},
    ) == ["BAD_ARGS"]
