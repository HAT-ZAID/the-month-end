"""I2: starting a person who already exists must not erase them.

"Start a new person" used to call ``accounts.create`` unconditionally, and
``save`` replaces every row wholesale.  Typing an existing name therefore
deleted that account's events, requests and payment options and blanked the
profile, with no warning and no way back.  An existing name is now refused until
the person confirms a second, explicit time; only then is the account replaced.
"""
from __future__ import annotations

import asyncio

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
    """The app must never reach a model server while these tests run."""
    monkeypatch.setenv("BUY_WAIT_MODEL_DISCOVERY", "0")


def _stored(name: str = "ana") -> dict:
    """One person with a profile, a movement, a request and a decision."""
    account = service.create_user(name, currency="ZAR", balance="12000", floor="2000")
    service.add_fact(
        account,
        "income",
        "3000",
        when="2026-01-01",
        description="salary",
        recurrence="monthly",
    )
    service.buy(account, "500", on="2026-01-02", text="shoes")
    service.save_user(account)
    stored = store.load(name)
    assert stored is not None
    assert stored["events"] and stored["requests"]
    return stored


def test_creating_an_existing_person_is_refused_and_changes_nothing(db):
    before = _stored("ana")

    with pytest.raises(ValueError) as caught:
        service.create_user("ana", currency="USD", balance="1", floor="0")

    message = str(caught.value).lower()
    assert "ana" in message
    assert "exist" in message or "already" in message
    assert store.load("ana") == before


def test_an_explicit_overwrite_replaces_the_account(db):
    before = _stored("ana")

    account = service.create_user(
        "ana", currency="USD", balance="1", floor="0", overwrite=True
    )

    after = store.load("ana")
    assert after != before
    assert after["events"] == []
    assert after["requests"] == []
    assert after["options"] == []
    assert after["profile"]["home_currency"] == "USD"
    assert after["profile"]["current_available_balance"] == "1"
    assert account["profile"]["user_id"] == "ana"


def test_a_new_person_is_still_created_in_one_step(db):
    account = service.create_user("bea", currency="ZAR", balance="10", floor="1")

    assert account["profile"]["user_id"] == "bea"
    assert store.load("bea")["profile"]["current_available_balance"] == "10"


def test_the_start_button_refuses_until_it_is_pressed_a_second_time(db):
    from textual.widgets import Button, Input

    from tools.app import BuyOrWait, Onboarding

    before = _stored("ana")

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(Onboarding())
            await pilot.pause()
            app.screen.query_one("#ob-name", Input).value = "ana"
            app.screen.query_one("#ob-currency", Input).value = "USD"
            app.screen.query_one("#ob-balance", Input).value = "1"
            app.screen.query_one("#ob-floor", Input).value = "0"

            # First press: refused, and not one row was touched.
            app.screen.query_one("#ob-start", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, Onboarding)
            assert store.load("ana") == before

            # Second press with the same name is the explicit confirmation.
            app.screen.query_one("#ob-start", Button).press()
            await pilot.pause()
            assert not isinstance(app.screen, Onboarding)
            after = store.load("ana")
            assert after["events"] == []
            assert after["requests"] == []
            assert after["options"] == []
            assert after["profile"]["home_currency"] == "USD"
        return True

    assert asyncio.run(drive()) is True
