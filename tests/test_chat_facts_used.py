"""The last answer can say which facts it used.

CR-19 (facts control): a person could see a figure but not the tool result behind
it.  :class:`code.chat.Session` now keeps ``last_observations`` for the turn just
finished, and :func:`code.chat.facts_used` returns the bounded, user-facing shape:

    [{"tool": str, "args": dict, "result_summary": str}]

A summary may repeat only what the tool returned: it is rendered from the result
by the same deterministic renderer the verifier uses, capped at
:data:`code.chat.MAX_RESULT_SUMMARY_CHARS`, and a failed call reports its fixed
``safe_summary`` rather than an exception message.  The app exposes the list as
the ``facts`` action, and says plainly when no turn has run yet.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import code.accounts as store
from code import chat, service
from tests.support.fake_provider import FakeMessage, FakeProvider


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


@pytest.fixture(autouse=True)
def _no_model_probe(monkeypatch):
    """The model menu is exercised with discovery off; no test reaches a server."""
    monkeypatch.setenv("BUY_WAIT_MODEL_DISCOVERY", "0")


def _account(name: str = "ana") -> dict:
    account = store.create(name, home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    store.save(account)
    return account


def _turn(index: int, reply: str, name: str = "position") -> list[FakeMessage]:
    """One dispatched call followed by a spoken answer."""
    args = {} if name != "what_if" else {"amount": "500.00"}
    return [
        FakeMessage("", [{"name": name, "args": args, "id": f"call_{index}"}]),
        FakeMessage(reply),
    ]


def _digits(text: str) -> set[str]:
    return {
        match.group(0)
        for match in chat._FIGURE.finditer(str(text or ""))
    }


def _log_text(app) -> str:
    """What the Chat tab has actually written, line by line."""
    from textual.widgets import RichLog

    return "\n".join(line.text for line in app.query_one("#chat-log", RichLog).lines)


# ------------------------------------------------------------- the contract


def test_no_turn_means_no_facts():
    assert chat.facts_used(chat.Session()) == []
    assert chat.facts_used(None) == []


def test_a_turn_names_the_tool_and_a_bounded_summary(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))
    session = chat.Session()

    chat.ask(account, "what can I spend?", provider=provider, session=session)

    facts = chat.facts_used(session)
    assert [item["tool"] for item in facts] == ["position"]
    assert facts[0]["args"] == {}
    summary = facts[0]["result_summary"]
    assert summary
    assert len(summary) <= chat.MAX_RESULT_SUMMARY_CHARS
    # The summary repeats the tool's own words, never a new sentence of its own.
    assert summary.startswith("From your ledger:") or "position" in summary


def test_a_summary_adds_no_figure_the_tool_did_not_return(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))
    session = chat.Session()

    chat.ask(account, "what can I spend?", provider=provider, session=session)

    result = chat.dispatch(account, "position")
    allowed = _digits(json.dumps(result, default=str))
    summary = chat.facts_used(session)[0]["result_summary"]
    assert _digits(summary) <= allowed


def test_the_last_turn_replaces_the_facts_of_the_one_before(db):
    account = _account()
    provider = FakeProvider(
        replies=(
            _turn(1, "Your ledger is the only source.")
            + _turn(2, "Nothing is missing from your account.", name="facts")
        )
    )
    session = chat.Session()

    chat.ask(account, "first question", provider=provider, session=session)
    assert [item["tool"] for item in chat.facts_used(session)] == ["position"]

    chat.ask(account, "second question", provider=provider, session=session)
    assert [item["tool"] for item in chat.facts_used(session)] == ["facts"]


def test_switching_account_forgets_the_last_facts(db):
    ana = _account("ana")
    bea = _account("bea")
    provider = FakeProvider(
        replies=(
            _turn(1, "Your ledger is the only source.")
            + _turn(2, "Your ledger is still the only source.")
        )
    )
    session = chat.Session()

    chat.ask(ana, "ana's question", provider=provider, session=session)
    assert chat.facts_used(session)

    chat.ask(bea, "bea's question", provider=provider, session=session)

    assert session.account_id == "bea"
    # Only bea's turn may be described; ana's tool list is gone, not relabelled.
    assert len(chat.facts_used(session)) == 1


# ----------------------------------------------------------------- the app


def test_the_app_says_nothing_yet_before_any_turn(db):
    from tools.app import TheMonthEnd

    _account("ana")

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-chat"
            await pilot.pause()
            app.action_facts()
            await pilot.pause()
            assert "nothing yet" in _log_text(app).lower()
        return True

    assert asyncio.run(drive()) is True


def test_the_app_prints_the_facts_the_last_answer_used(db, monkeypatch):
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import TheMonthEnd

    account = _account("ana")
    position = service.position(account)
    reply = f"Your ledger shows {position['max_safe_payment']} is safe today."
    provider = FakeProvider(
        replies=[
            FakeMessage("", [{"name": "position", "args": {}, "id": "call_1"}]),
            FakeMessage(reply),
        ]
    )
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-chat"
            await pilot.pause()
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))

            worker = app.ask_worker
            assert worker is not None, "no worker was started for the chat turn"
            await worker.wait()
            await pilot.pause()

            app.action_facts()
            await pilot.pause()

            text = _log_text(app)
            assert "Facts used" in text
            assert "position()" in text
            assert "From your ledger:" in text
        return True

    assert asyncio.run(drive()) is True
