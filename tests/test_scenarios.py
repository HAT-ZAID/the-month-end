"""Scenario simulation: a hypothesis runs on a throwaway copy of the account.

The first three tests are the acceptance tests: a delay returns a kernel result
without persisting, the caller's account is untouched, and an unsupported
hypothesis is refused.  The rest pin the details a caller relies on: which one
fact each kind moves, what is refused rather than silently ignored, and that the
scenario id is deterministic.
"""
from __future__ import annotations

import copy

import pytest

from code import accounts as store
from code import service
from code.scenarios import Scenario, simulate_scenario


@pytest.fixture
def account(tmp_path, monkeypatch):
    """An account built through the service, stored in a throwaway database."""
    database = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: database)
    acc = service.create_user("ana", currency="ZAR", balance="12000", floor="2000")
    service.add_fact(acc, "bill", "8000", when="2026-11-03", description="rent")
    decision = service.buy(
        acc, "900", on="2026-11-01", by="2026-12-20", text="headphones",
    )
    return acc, decision["request_id"]


# ------------------------------------------------------------ the hypothesis record


def test_the_scenario_record_starts_blank():
    scenario = Scenario(kind="pay_on")
    assert (scenario.request_id, scenario.amount, scenario.on) == ("", "", "")
    assert (scenario.by, scenario.event_id, scenario.new_amount) == (0, "", "")


def test_a_plain_mapping_is_not_a_scenario(account):
    with pytest.raises(TypeError):
        simulate_scenario(account[0], {"kind": "pay_on"})


# ------------------------------------------------------------ the acceptance tests


def test_delay_days_returns_a_kernel_result_and_persists_nothing(account):
    acc, request_id = account
    result = simulate_scenario(
        acc, Scenario(kind="delay_days", request_id=request_id, by=19),
    )
    assert result["persisted"] is False
    kernel = result["kernel_result"]
    assert kernel["request_id"] == request_id
    assert kernel["affordability_status"] == "affordable_now"
    assert kernel["earliest_date_for_full_payment"] == "2026-11-20"


def test_the_input_account_is_unchanged_by_a_simulation(account):
    acc, request_id = account
    event_id = acc["events"][0]["event_id"]
    before = copy.deepcopy(acc)

    simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )
    simulate_scenario(
        acc, Scenario(kind="delay_days", request_id=request_id, by=5),
    )
    simulate_scenario(
        acc,
        Scenario(
            kind="change_expense",
            request_id=request_id,
            event_id=event_id,
            new_amount="9500",
        ),
    )

    assert acc == before


@pytest.mark.parametrize("kind", ["invent_income", "", "change_income"])
def test_an_unsupported_kind_is_refused(account, kind):
    acc, request_id = account
    with pytest.raises(ValueError) as caught:
        simulate_scenario(acc, Scenario(kind=kind, request_id=request_id))
    message = str(caught.value)
    for supported in ("pay_on", "delay_days", "change_expense"):
        assert supported in message


# ------------------------------------------------------------ one fact moves


def test_pay_on_moves_only_the_purchase_date(account):
    acc, request_id = account
    result = simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )
    kernel = result["kernel_result"]
    assert kernel["earliest_date_for_full_payment"] == "2026-11-20"
    assert kernel["payment_plan"] == "2026-11-20:900"
    assert kernel["amount_safe_to_pay"] == "900"


def test_delay_days_of_zero_is_the_baseline(account):
    acc, request_id = account
    result = simulate_scenario(
        acc, Scenario(kind="delay_days", request_id=request_id, by=0),
    )
    assert result["kernel_result"]["earliest_date_for_full_payment"] == "2026-11-01"


def test_change_expense_reprices_the_named_event_in_the_copy(account):
    acc, request_id = account
    event_id = acc["events"][0]["event_id"]
    baseline = service.decide(acc, request_id)

    result = simulate_scenario(
        acc,
        Scenario(
            kind="change_expense",
            request_id=request_id,
            event_id=event_id,
            new_amount="9500",
        ),
    )

    assert result["kernel_result"]["amount_safe_to_pay"] != baseline["amount_safe_to_pay"]
    assert result["kernel_result"]["affordability_status"] == "not_affordable"
    assert acc["events"][0]["amount"] == "8000"


def test_the_assumptions_say_what_was_assumed(account):
    acc, request_id = account
    result = simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )
    lines = result["assumptions"]
    assert isinstance(lines, list) and lines
    assert all(isinstance(line, str) and line for line in lines)
    text = " ".join(lines)
    assert "copied" in text
    assert "unchanged" in text
    assert "2026-11-20" in text


# ------------------------------------------------------------ refused, not ignored


def test_a_missing_request_is_refused(account):
    acc, _request_id = account
    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc, Scenario(kind="pay_on", request_id="request_9", on="2026-11-20"),
        )
    assert "request_9" in str(caught.value)


def test_a_blank_request_id_is_refused(account):
    acc, _request_id = account
    with pytest.raises(ValueError):
        simulate_scenario(acc, Scenario(kind="delay_days", by=3))


@pytest.mark.parametrize("on", ["", "20-11-2026", "2026-02-30"])
def test_pay_on_refuses_a_date_it_cannot_read(account, on):
    acc, request_id = account
    with pytest.raises(ValueError):
        simulate_scenario(
            acc, Scenario(kind="pay_on", request_id=request_id, on=on),
        )


@pytest.mark.parametrize("by", [True, "5", 1.5, None])
def test_delay_days_refuses_a_partial_day(account, by):
    acc, request_id = account
    with pytest.raises(ValueError):
        simulate_scenario(
            acc, Scenario(kind="delay_days", request_id=request_id, by=by),
        )


def test_change_expense_refuses_a_missing_event(account):
    acc, request_id = account
    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id="event_99",
                new_amount="1",
            ),
        )
    assert "event_99" in str(caught.value)


@pytest.mark.parametrize("amount", ["", "not money", "NaN", "Infinity", "-1"])
def test_change_expense_refuses_an_amount_it_cannot_apply(account, amount):
    acc, request_id = account
    event_id = acc["events"][0]["event_id"]
    with pytest.raises(ValueError):
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=event_id,
                new_amount=amount,
            ),
        )


def test_change_expense_refuses_an_income_row(account):
    acc, request_id = account
    service.add_fact(acc, "income", "5000", when="2026-11-01", description="salary")
    income_id = acc["events"][1]["event_id"]
    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=income_id,
                new_amount="6000",
            ),
        )
    assert "expense" in str(caught.value)


def test_a_field_the_kind_cannot_apply_is_refused(account):
    acc, request_id = account
    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="pay_on",
                request_id=request_id,
                on="2026-11-20",
                amount="50",
            ),
        )
    assert "amount" in str(caught.value)


# ------------------------------------------------------------ stable identity


def test_the_scenario_id_is_deterministic(account):
    acc, request_id = account
    first = simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )
    second = simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )
    other = simulate_scenario(
        acc, Scenario(kind="pay_on", request_id=request_id, on="2026-11-21"),
    )

    assert first["scenario_id"] == second["scenario_id"]
    assert first["scenario_id"].startswith("scenario-pay_on-")
    assert other["scenario_id"] != first["scenario_id"]
