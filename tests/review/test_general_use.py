"""The general-use layer: account store, position engine, and the Textual app.

These are focused tests for the four things that make the product usable on a
real person's money rather than the calibration fixture: the contract-form
account, the SQLite store, the kernel position entry point, and the screens that
render those figures.

Every money assertion compares against what the kernel returns. A test that
computed its own expected sum would be testing the arithmetic it was meant to
keep out of the view layer.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

import pytest

import code.accounts as store
from code import account as acct
from code import service
from code.solver import position as kernel_position

PROFILE = {
    "user_id": "ana",
    "home_currency": "ZAR",
    "current_available_balance": "12000",
    "minimum_balance_to_keep": "2000",
    "financial_priorities": "rent|food",
    "expense_categories_to_protect": "rent",
    "expense_categories_user_is_willing_to_reduce": "",
    "expense_categories_user_is_willing_to_stop": "",
    "payment_methods_user_will_consider": "full_payment",
    "max_installment_months": "",
}

EVENTS = [
    {
        "event_id": "event_1", "user_id": "ana", "event_type": "income",
        "description": "salary", "category": "income", "direction": "credit",
        "amount": "15000", "currency": "ZAR", "event_date": "2026-10-25",
        "settlement_date": "2026-10-25", "status": "settled",
        "linked_event_id": "", "flexibility": "fixed", "minimum_allowed_amount": "",
    },
    {
        "event_id": "event_2", "user_id": "ana", "event_type": "expense",
        "description": "rent", "category": "rent", "direction": "debit",
        "amount": "8000", "currency": "ZAR", "event_date": "2026-11-03",
        "settlement_date": "2026-11-03", "status": "scheduled",
        "linked_event_id": "", "flexibility": "fixed", "minimum_allowed_amount": "",
    },
]


# ------------------------------------------------------------- account shape


def test_account_stores_the_contract_form_not_the_typed_form():
    account = acct.blank_account(user_id="ana", home_currency="ZAR")
    acct.set_profile(
        account,
        expense_categories_to_protect="rent, food",
        payment_methods_user_will_consider="full_payment, partial_payment",
    )
    profile = account["profile"]
    assert profile["expense_categories_to_protect"] == "rent|food"
    assert profile["payment_methods_user_will_consider"] == "full_payment|partial_payment"


def test_unknown_field_and_vocabulary_are_rejected_at_the_boundary():
    account = acct.blank_account(user_id="ana")
    with pytest.raises(KeyError):
        acct.set_profile(account, not_a_field="x")
    with pytest.raises(KeyError):
        acct.add_event(account, not_a_field="x")


def test_problems_names_what_is_missing_in_plain_english():
    account = acct.blank_account(user_id="ana")
    problems = acct.problems(account)
    assert any("balance" in item for item in problems)
    assert any("minimum" in item for item in problems)


def test_account_data_is_the_kernel_shape():
    account = acct.blank_account(user_id="ana", home_currency="ZAR")
    acct.set_profile(account, current_available_balance="12000",
                     minimum_balance_to_keep="2000")
    data = acct.account_data(account)
    assert set(data) >= {"requests", "profiles", "events_by_user", "fx", "options"}
    assert data["profiles"]["ana"]["current_available_balance"] == "12000"


# ------------------------------------------------------------- the store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


def test_store_round_trips_one_person(db):
    created = store.create("ana", home_currency="ZAR")
    assert store.has_user("ana")
    assert store.list_users() == ["ana"]
    loaded = store.load("ana")
    assert loaded is not None
    assert loaded["profile"]["home_currency"] == "ZAR"
    assert store.load("nobody") is None
    assert store.delete("ana") is True
    assert store.list_users() == []


def test_store_replaces_rows_instead_of_duplicating_them(db):
    account = store.create("ana")
    acct.add_event(account, event_type="expense", description="rent",
                   direction="debit", amount="8000", category="rent",
                   event_date="2026-11-03", settlement_date="2026-11-03",
                   currency="ZAR", status="scheduled", flexibility="fixed")
    store.save(account)
    store.save(account)
    reloaded = store.load("ana")
    assert len(reloaded["events"]) == 1


def test_store_rejects_an_empty_name(db):
    with pytest.raises(ValueError):
        store.create("   ")


# ------------------------------------------------------------- the engine


def test_position_figures_come_from_the_kernel():
    result = kernel_position("ana", "2026-11-01", dict(PROFILE), list(EVENTS), [])
    assert result["available_balance"] == "12000"
    assert result["minimum_balance"] == "2000"
    assert result["max_safe_payment"] == "2000.0"  # 12000 - 2000 floor - 8000 rent
    assert result["committed"] == "8000.0"
    assert result["fail_closed"] is False
    assert any("conservative" in item for item in result["assumptions"])


def test_position_refuses_a_date_it_cannot_read():
    with pytest.raises(ValueError):
        kernel_position("ana", "not-a-date", dict(PROFILE), list(EVENTS), [])


def test_buy_runs_the_kernel_and_records_the_request(db):
    account = store.create("ana", home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    service.add_fact(account, "bill", "8000", when="2026-11-03", description="rent")
    decision = service.buy(account, "900", on="2026-11-01", by="2026-11-20",
                           text="headphones")
    assert decision["affordability_status"] == "affordable_now"
    assert decision["recommended_payment_method"] == "full_payment"
    assert decision["amount_safe_to_pay"] == "900"
    assert decision["earliest_date_for_full_payment"] == "2026-11-01"
    assert len(account["requests"]) == 1


def test_a_bad_amount_never_reaches_the_solver(db):
    account = store.create("ana")
    with pytest.raises(service.EngineError):
        service.buy(account, "not money")
    with pytest.raises(service.EngineError):
        service.add_fact(account, "bill", "-5")


def test_an_unknown_fact_kind_names_the_ones_that_work(db):
    account = store.create("ana")
    with pytest.raises(service.EngineError) as caught:
        service.add_fact(account, "inflow", "100")
    assert "income" in str(caught.value)


# ------------------------------------------------------------- the screens


def _drive_app():
    from tools.app import BuyOrWait, Metric

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.account is not None
            pos = service.position(app.account, as_of=None)
            shown = app.query_one("#m-safe", Metric).value
            assert shown == service.money(pos["max_safe_payment"])
            assert app.query_one("#m-balance", Metric).value == service.money(
                pos["available_balance"]
            )
            assert app.query_one("#m-floor", Metric).value == service.money(
                pos["minimum_balance"]
            )
            # The ledger table lists exactly the recorded facts.
            table = app.query_one("#ledger-table")
            assert table.row_count == len(app.account["events"])
        return True

    return asyncio.run(drive())


def test_the_app_opens_on_the_kernels_position_not_the_fixture(db, monkeypatch):
    account = store.create("ana", home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    service.add_fact(account, "bill", "8000", when=date.today().isoformat(),
                     description="rent")
    store.save(account)
    assert _drive_app() is True


def test_the_app_starts_on_onboarding_when_nobody_is_stored(db):
    from tools.app import BuyOrWait, Onboarding

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, Onboarding)
        return True

    assert asyncio.run(drive()) is True


def test_the_ui_module_holds_no_arithmetic_over_money():
    """A figure added up in the view layer is the defect this phase removed."""
    import io
    from pathlib import Path

    source = io.open(
        Path(__file__).resolve().parents[2] / "tools" / "app.py", encoding="utf-8"
    ).read()
    for banned in ("+ float(", "sum(", "Decimal("):
        assert banned not in source, f"{banned} in the view layer"


# ------------------------------------------------------------- the conversation


def test_the_conversation_tools_return_kernel_figures(db):
    from code import chat

    account = store.create("ana", home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    service.add_fact(account, "bill", "8000", when="2026-11-03", description="rent")

    pos = chat.dispatch(account, "position")
    assert pos["max_safe_payment"] == service.position(account)["max_safe_payment"]

    trial = chat.dispatch(account, "what_if", {"amount": "500", "on": "2026-11-01"})
    assert "affordability_status" in trial
    # A hypothetical is never recorded on the person's account.
    assert len(account["requests"]) == 0

    missing = chat.dispatch(account, "facts")
    assert isinstance(missing["problems"], list)
    blank = acct.blank_account(user_id="nobody")
    assert chat.dispatch(blank, "facts")["problems"] != []


def test_an_unknown_tool_names_the_ones_that_exist(db):
    from code import chat

    account = store.create("ana")
    with pytest.raises(KeyError) as caught:
        chat.dispatch(account, "forecast_everything")
    assert "position" in str(caught.value)


def test_without_a_model_the_conversation_says_so(db):
    from code import chat

    account = store.create("ana")
    with pytest.raises(chat.NoModel):
        chat.ask(account, "what can I spend this week?")
