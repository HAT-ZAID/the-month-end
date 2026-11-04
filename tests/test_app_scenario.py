"""The Purchase tab's what-if runs through :mod:`code.scenarios` (issue #14).

The screen renders and computes nothing: a delayed purchase is simulated on a
deep copy by :func:`code.scenarios.simulate_scenario`, the kernel's verdict is
shown, and the stored account still says the purchase is on its original date.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

import code.accounts as store
from code import service


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


@pytest.fixture(autouse=True)
def _no_model_probe(monkeypatch):
    """Tests never reach a model server: the menu is exercised with discovery off."""
    monkeypatch.setenv("BUY_WAIT_MODEL_DISCOVERY", "0")


def _account() -> dict:
    account = service.create_user("ana", currency="ZAR", balance="12000", floor="2000")
    service.add_fact(
        account, "bill", "8000", when="2026-11-03", description="rent",
        category="rent",
    )
    store.save(account)
    return account


def _scenario_text(app) -> str:
    return str(app.query_one("#scenario-explain").render())


def test_the_purchase_tab_delays_a_decided_purchase_without_saving_it(db):
    from tools.app import TheMonthEnd

    _account()
    before = copy.deepcopy(store.load("ana"))

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-buy"
            await pilot.pause()
            app.query_one("#buy-amount").value = "900"
            app.query_one("#buy-on").value = "2026-11-01"
            app.query_one("#buy-by").value = "2026-12-20"
            app.query_one("#buy-go").press()
            for _ in range(20):
                await pilot.pause()
                if app.last_request_id:
                    break
            assert app.last_request_id, "the purchase was not decided"

            app.query_one("#scenario-days").value = "19"
            app.query_one("#scenario-go").press()
            shown = ""
            for _ in range(20):
                await pilot.pause()
                shown = _scenario_text(app)
                if shown.strip():
                    break

            assert "2026-11-20" in shown, shown
            assert "nothing was saved" in shown.lower(), shown
            assert app.account["requests"][0]["request_date"] == "2026-11-01"
        return True

    assert asyncio.run(drive()) is True
    stored = store.load("ana")
    assert stored["requests"][0]["request_date"] == "2026-11-01"
    assert stored["events"] == before["events"]


def test_the_screen_refuses_a_protected_expense_change(db):
    from tools.app import TheMonthEnd

    account = _account()
    decision = service.buy(
        account, "900", on="2026-11-01", by="2026-12-20", text="headphones",
    )
    rent_id = account["events"][0]["event_id"]
    service.update_profile(account, expense_categories_to_protect="rent")
    store.save(account)
    before = copy.deepcopy(store.load("ana"))

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            with pytest.raises(ValueError) as caught:
                app.run_scenario(
                    "change_expense",
                    decision["request_id"],
                    event_id=rent_id,
                    new_amount="0",
                )
            assert "rent" in str(caught.value)
            assert "protect" in str(caught.value)
        return True

    assert asyncio.run(drive()) is True
    assert store.load("ana") == before
