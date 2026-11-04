"""Scenario guards and baseline comparison.

A what-if must stay a what-if.  Three hypotheses are refused before the kernel
runs: one that invents income, one that removes an expense in a category the
person asked to protect, and one that names a record belonging to another
account.  :func:`compare_scenarios` decides the two hypotheses on throwaway
copies and reports exactly which machine fields moved.
"""
from __future__ import annotations

import copy

import pytest

from code import accounts as store
from code import service
from code.scenarios import (
    KERNEL_FIELDS,
    Scenario,
    compare_scenarios,
    simulate_scenario,
)


@pytest.fixture
def account(tmp_path, monkeypatch):
    """A committed bill plus one purchase, stored in a throwaway database."""
    database = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: database)
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


# ------------------------------------------------------------------ income


def test_inventing_income_is_refused(account):
    """An income row re-labelled as a debit must never become a cheap expense."""
    acc, request_id, _rent_id = account
    income = service.add_fact(
        acc,
        "income",
        "5000",
        when="2026-11-01",
        description="salary",
        category="salary",
        recurrence="monthly",
    )
    # A tampered or hand-edited row: the event is income, but the direction
    # claims a debit.  Re-pricing it would invent spending money that is not
    # there, so the scenario is refused rather than decided.
    acc["events"][-1]["direction"] = "debit"
    before = copy.deepcopy(acc)

    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=income["event_id"],
                new_amount="1",
            ),
        )

    assert "income" in str(caught.value)
    assert acc == before


def test_re_pricing_an_income_row_is_refused(account):
    acc, request_id, _rent_id = account
    income = service.add_fact(
        acc, "income", "5000", when="2026-11-01", description="salary",
    )

    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=income["event_id"],
                new_amount="6000",
            ),
        )

    assert "income" in str(caught.value)


# --------------------------------------------------------------- protected


def test_removing_a_protected_expense_is_refused(account):
    acc, request_id, rent_id = account
    service.update_profile(acc, expense_categories_to_protect="rent")
    before = copy.deepcopy(acc)

    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=rent_id,
                new_amount="0",
            ),
        )

    message = str(caught.value)
    assert "rent" in message
    assert "protect" in message
    assert acc == before


def test_reducing_a_protected_expense_is_refused(account):
    acc, request_id, rent_id = account
    service.update_profile(acc, expense_categories_to_protect="rent")

    with pytest.raises(ValueError):
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=rent_id,
                new_amount="4000",
            ),
        )


def test_a_non_protected_expense_can_still_be_removed(account):
    """The guard is about protection, not about every spending change."""
    acc, request_id, rent_id = account
    streaming = service.add_fact(
        acc,
        "subscription",
        "200",
        when="2026-11-05",
        description="streaming",
        category="streaming",
    )
    service.update_profile(acc, expense_categories_to_protect="rent")

    result = simulate_scenario(
        acc,
        Scenario(
            kind="change_expense",
            request_id=request_id,
            event_id=streaming["event_id"],
            new_amount="0",
        ),
    )

    assert result["persisted"] is False
    assert acc["events"][-1]["amount"] == "200"
    assert acc["events"][0]["amount"] == "8000"  # the rent is untouched
    assert rent_id != streaming["event_id"]


# ---------------------------------------------------------- account scope


def test_a_request_from_another_account_is_refused(account):
    acc, request_id, _rent_id = account
    acc["requests"][0]["user_id"] = "someone-else"

    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
        )

    assert "another account" in str(caught.value)


def test_an_expense_from_another_account_is_refused(account):
    acc, request_id, rent_id = account
    acc["events"][0]["user_id"] = "someone-else"

    with pytest.raises(ValueError) as caught:
        simulate_scenario(
            acc,
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=rent_id,
                new_amount="7000",
            ),
        )

    assert "another account" in str(caught.value)


# ------------------------------------------------------------ comparison


def test_compare_scenarios_names_the_fields_that_moved(account):
    acc, request_id, _rent_id = account

    result = compare_scenarios(
        acc,
        Scenario(kind="pay_on", request_id=request_id, on="2026-11-01"),
        Scenario(kind="pay_on", request_id=request_id, on="2026-11-20"),
    )

    assert set(result) == {"baseline", "alternative", "delta_fields"}
    assert result["baseline"]["persisted"] is False
    assert result["alternative"]["persisted"] is False

    left = result["baseline"]["kernel_result"]
    right = result["alternative"]["kernel_result"]
    expected = [name for name in KERNEL_FIELDS if left.get(name) != right.get(name)]
    assert result["delta_fields"] == expected
    assert "earliest_date_for_full_payment" in result["delta_fields"]
    assert "payment_plan" in result["delta_fields"]
    assert "request_id" not in result["delta_fields"]
    assert left["earliest_date_for_full_payment"] == "2026-11-01"
    assert right["earliest_date_for_full_payment"] == "2026-11-20"


def test_compare_scenarios_reports_nothing_when_the_hypotheses_match(account):
    acc, request_id, _rent_id = account
    scenario = Scenario(kind="pay_on", request_id=request_id, on="2026-11-20")

    result = compare_scenarios(acc, scenario, scenario)

    assert result["delta_fields"] == []


def test_compare_scenarios_neither_persists_nor_mutates_the_account(account):
    acc, request_id, rent_id = account
    before = copy.deepcopy(acc)

    result = compare_scenarios(
        acc,
        Scenario(kind="delay_days", request_id=request_id, by=0),
        Scenario(
            kind="change_expense",
            request_id=request_id,
            event_id=rent_id,
            new_amount="9500",
        ),
    )

    assert result["baseline"]["persisted"] is False
    assert result["alternative"]["persisted"] is False
    assert acc == before


def test_compare_scenarios_refuses_an_unsafe_alternative(account):
    acc, request_id, rent_id = account
    service.update_profile(acc, expense_categories_to_protect="rent")

    with pytest.raises(ValueError):
        compare_scenarios(
            acc,
            Scenario(kind="pay_on", request_id=request_id, on="2026-11-01"),
            Scenario(
                kind="change_expense",
                request_id=request_id,
                event_id=rent_id,
                new_amount="0",
            ),
        )
