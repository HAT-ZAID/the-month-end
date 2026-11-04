"""The Ledger form must record what the person meant, not just what they typed.

The defect this file pins (CR-05, UI half): the form asked for a kind, an amount,
a date and a description, and passed exactly those to
:func:`code.service.add_fact`.  The category and the recurrence -- the two words
that decide whether the forecast can see the row -- were never asked for.  So a
salary typed into the app became an ``income`` row the forecast ignored, and a
bill had no committed category at all.

These tests drive the form's payload builder and the screen itself:

* income answered "monthly" reaches the account as ``salary``;
* a bill with no category is refused *before* anything is recorded, and the
  inline message names the categories the forecast projects;
* a bill with ``category="rent"`` passes that category through untouched;
* the pre-existing fields keep their ids and their values still flow through.

The expected category vocabulary is derived from :mod:`code.forecast`, never
from an assertion written to match the implementation.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from textual.css.query import NoMatches

import code.accounts as store
from code import service
from code.forecast import (
    DISCRETIONARY_CATEGORIES,
    FORECASTABLE_EXPENSE_CATEGORIES,
)

#: The expense categories the forecast projects forward, from its own vocabulary.
PROJECTED = frozenset(FORECASTABLE_EXPENSE_CATEGORIES - DISCRETIONARY_CATEGORIES)

#: How long a rendered effect may take to appear before the test gives up.
DEADLINE_SECONDS = 5.0


def _render(app, selector: str) -> str:
    """The current render of ``selector``, or "" while it is not mounted yet."""
    try:
        widget = app.query_one(selector)
    except NoMatches:
        return ""
    return str(widget.render())


async def _wait_for(pilot, effect, *, timeout: float = DEADLINE_SECONDS) -> None:
    """Poll until ``effect()`` holds, or the deadline expires.

    ``pilot.pause()`` waits for CPU idle, not for a ``Button.Pressed`` posted by
    ``press()`` to be dispatched: the message queue can still be holding it when
    the pause returns, so reading straight after one pause sees the screen
    before its handler ran.  Wait for the rendered effect -- the same text the
    assertion checks -- instead of one tick.  A deadline means a genuine
    regression still fails rather than hanging.
    """
    deadline = time.monotonic() + timeout
    while not effect() and time.monotonic() < deadline:
        await pilot.pause()


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


def _account(name: str = "ana") -> dict:
    account = store.create(name, home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    return account


# ------------------------------------------------------- the payload builder


def test_monthly_income_reaches_the_account_as_a_salary_event(db):
    """(a) The form's own answer to "monthly pay?" must reach the forecaster."""
    from tools.app import fact_payload

    account = service.create_user("sim", currency="USD", balance="10000", floor="2000")
    payload = fact_payload(
        "income", "3000", when="2025-12-01", description="payroll",
        recurrence="monthly",
    )
    event = service.add_fact(account, **payload)

    assert payload["category"] == ""
    assert payload["recurrence"] == "monthly"
    assert event["category"] == "salary"
    assert [row["category"] for row in account["events"]] == ["salary"]


def test_income_answered_no_is_recorded_but_not_projected(db):
    """The question is asked, so "no" must mean what it says."""
    from tools.app import fact_payload

    account = service.create_user("sol", currency="USD", balance="10000", floor="2000")
    payload = fact_payload(
        "income", "900", when="2025-12-01", description="sold a bike",
        recurrence="no",
    )
    event = service.add_fact(account, **payload)

    assert payload["recurrence"] == ""
    assert event["category"] == "irregular_income"
    assert event["category"] not in PROJECTED | {"salary"}


def test_a_bill_with_no_category_is_refused_before_add_fact(db, monkeypatch):
    """(b) An empty category is refused, not silently defaulted."""
    from tools import app as app_module

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("add_fact ran for a bill with no category")

    monkeypatch.setattr(service, "add_fact", _spy)

    with pytest.raises(service.EngineError) as caught:
        app_module.fact_payload(
            "bill", "800", when="2026-01-01", description="",
            category="", recurrence="",
        )

    message = str(caught.value)
    assert "category" in message.lower()
    for allowed in sorted(PROJECTED):
        assert allowed in message, f"the refusal must name {allowed!r}"
    assert calls == [], "nothing may be recorded by a refused bill"


def test_a_bill_with_no_category_is_refused_through_the_screen(db, monkeypatch):
    """The screen shows the same refusal inline, with the allowed words."""
    from tools.app import BuyOrWait

    account = _account()
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-ledger"
            await pilot.pause()
            app.query_one("#fact-kind").value = "bill"
            app.query_one("#fact-amount").value = "800"
            app.query_one("#fact-category").value = ""
            app.query_one("#fact-add").press()
            await _wait_for(
                pilot, lambda: "category" in _render(app, "#fact-status").lower()
            )

            assert app.account is not None
            assert app.account["events"] == [], "a refused bill was recorded anyway"
            shown = str(app.query_one("#fact-status").render())
            assert "category" in shown.lower()
            for allowed in sorted(PROJECTED):
                assert allowed in shown, f"the inline message must name {allowed!r}"
        return True

    assert asyncio.run(drive()) is True


def test_a_bill_with_a_named_category_passes_it_through(db):
    """(c) An explicit category is what the service is called with."""
    from tools.app import fact_payload

    account = service.create_user("ren", currency="USD", balance="10000", floor="2000")
    payload = fact_payload(
        "bill", "800", when="2026-01-01", description="monthly rent",
        category="rent",
    )
    assert payload["category"] == "rent"

    event = service.add_fact(account, **payload)
    assert event["category"] == "rent"
    assert event["category"] in PROJECTED


# ----------------------------------------------------------- the screen flow


def test_the_screen_records_monthly_income_as_a_salary(db):
    from tools.app import BuyOrWait

    account = _account()
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-ledger"
            await pilot.pause()
            app.query_one("#fact-kind").value = "income"
            app.query_one("#fact-amount").value = "3000"
            app.query_one("#fact-date").value = "2025-12-01"
            app.query_one("#fact-text").value = "payroll"
            app.query_one("#fact-recurrence").value = "yes"
            app.query_one("#fact-add").press()
            await _wait_for(
                pilot,
                lambda: app.account is not None and len(app.account["events"]) == 1,
            )

            assert app.account is not None
            assert len(app.account["events"]) == 1
            assert app.account["events"][0]["category"] == "salary"
        return True

    assert asyncio.run(drive()) is True


def test_the_screen_records_a_named_bill_on_its_category(db):
    from tools.app import BuyOrWait

    account = _account()
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-ledger"
            await pilot.pause()
            app.query_one("#fact-kind").value = "bill"
            app.query_one("#fact-amount").value = "800"
            app.query_one("#fact-date").value = "2026-01-01"
            app.query_one("#fact-category").value = "rent"
            app.query_one("#fact-add").press()
            await _wait_for(
                pilot,
                lambda: app.account is not None and len(app.account["events"]) == 1,
            )

            assert app.account is not None
            assert len(app.account["events"]) == 1
            assert app.account["events"][0]["category"] == "rent"
        return True

    assert asyncio.run(drive()) is True


def test_the_form_keeps_its_existing_fields_and_adds_the_two_new_ones(db):
    """Layout behaviour is preserved: same ids, same order, two new inputs."""
    from tools.app import BuyOrWait

    account = _account()
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            for field in ("#fact-kind", "#fact-amount", "#fact-date", "#fact-text",
                          "#fact-category", "#fact-recurrence", "#fact-add"):
                assert app.query_one(field) is not None
        return True

    assert asyncio.run(drive()) is True
