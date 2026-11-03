"""A fact recorded through the service must land on a category the forecast reads.

The defect this file pins (CR-05): :func:`code.service.add_fact` used to store
the words it was called with as the *category* -- ``income``, ``bills``,
``subscriptions``, ``refunds``.  The ledger projects only ``salary`` income
(:data:`code.forecast.PRIMARY_INCOME_CATEGORIES`) and the committed expense
categories (:data:`code.forecast.FORECASTABLE_EXPENSE_CATEGORIES` minus the
lifestyle ones), so a salary typed into the app never reached the salary
forecaster and a recorded bill never became a monthly budget.  The app said
"Recorded" and the decision silently ignored the row.

The resolver is the one place that translation happens, so these tests check
both ends: the category stored on the event, and the entry the forecast emits
from that same event.
"""
from __future__ import annotations

import pytest

import code.accounts as store
from code import service
from code.forecast import (
    DISCRETIONARY_CATEGORIES,
    FORECASTABLE_EXPENSE_CATEGORIES,
    build_forecast,
)

#: The expense categories the ledger actually projects forward.  Derived here
#: from the forecast's own vocabularies, not from the service under test.
PROJECTED = frozenset(FORECASTABLE_EXPENSE_CATEGORIES - DISCRETIONARY_CATEGORIES)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


# --------------------------------------------------- the defect, end to end


def test_salary_recorded_through_service_reaches_the_forecast(db):
    account = service.create_user("a", currency="USD", balance="10000", floor="2000")
    service.add_fact(account, "income", "3000", when="2025-12-01",
                     description="monthly payroll", recurrence="monthly")
    service.add_fact(account, "income", "3000", when="2026-01-01",
                     description="monthly payroll", recurrence="monthly")

    assert [event["category"] for event in account["events"]] == ["salary", "salary"]

    result = build_forecast(account["events"], "2026-02-01", home_currency="USD")
    income = [entry for entry in result.entries if entry.kind == "income"]
    assert income, "the recorded salary never reached the income forecast"
    assert {entry.category for entry in income} == {"salary"}


def test_bill_with_unknown_category_is_rejected(db):
    account = service.create_user("b", currency="USD", balance="10000", floor="2000")
    with pytest.raises(service.EngineError) as caught:
        service.add_fact(account, "bill", "500", when="2026-01-01", category="bills")
    assert "forecast" in str(caught.value).lower()
    assert account["events"] == [], "a rejected fact must not be recorded"


def test_a_bill_lands_on_a_category_the_forecast_projects(db):
    account = service.create_user("c", currency="USD", balance="10000", floor="2000")
    event = service.add_fact(account, "bill", "800", when="2026-01-01",
                             description="rent", category="rent")
    assert event["category"] == "rent"
    assert event["category"] in PROJECTED


# ---------------------------------------------------------- the resolver


def test_monthly_income_is_named_the_kernels_salary():
    assert service.resolve_category("income", "", "monthly") == "salary"
    assert service.resolve_category("income", "payroll", "") == "irregular_income"


def test_the_service_projects_exactly_what_the_forecast_projects():
    assert service.PROJECTED_EXPENSE_CATEGORIES == PROJECTED


def test_irregular_income_is_recorded_but_named_as_something_the_forecast_skips(db):
    account = service.create_user("d", currency="USD", balance="10000", floor="2000")
    event = service.add_fact(account, "income", "900", when="2025-12-01",
                             description="sold a bike")
    assert event["category"] == "irregular_income"
    assert event["category"] not in {"salary", *PROJECTED}


def test_a_discretionary_category_is_refused_because_it_is_never_projected():
    with pytest.raises(service.EngineError) as caught:
        service.resolve_category("bill", "dining", "")
    assert "forecast" in str(caught.value).lower()


def test_the_contract_words_are_normalised_before_matching():
    assert service.resolve_category("bill", " Rent ", "") == "rent"
    assert service.resolve_category("subscription", "Music Subscription", "") == (
        "music_subscription"
    )


def test_a_stated_category_is_honoured_for_a_subscription():
    account = service.create_user("e", currency="USD", balance="10000", floor="2000")
    event = service.add_fact(account, "subscription", "15", when="2026-01-01",
                             description="a show", category="streaming")
    assert event["category"] == "streaming"
    assert event["flexibility"] == "stoppable"


def test_a_bill_with_no_category_still_lands_where_the_forecast_can_see_it(db):
    """An unnamed bill is still a bill: dropping it would understate the month."""
    account = service.create_user("f", currency="USD", balance="10000", floor="2000")
    named = service.add_fact(account, "bill", "800", when="2026-01-01",
                             description="rent")
    unnamed = service.add_fact(account, "bill", "120", when="2026-01-02")
    assert named["category"] == "rent", "the description should name the category"
    assert unnamed["category"] in PROJECTED


def test_a_refund_is_still_recorded_with_its_own_category(db):
    account = service.create_user("g", currency="USD", balance="10000", floor="2000")
    event = service.add_fact(account, "refund", "60", when="2026-01-01",
                             description="returned shoes")
    assert event["category"] == "refunds"
    assert event["direction"] == "credit"
    assert service.resolve_category("refund", "", "") == "refunds"


def test_an_unknown_kind_is_still_refused_by_name(db):
    account = service.create_user("h", currency="USD", balance="10000", floor="2000")
    with pytest.raises(service.EngineError) as caught:
        service.add_fact(account, "inflow", "100")
    assert "income" in str(caught.value)
