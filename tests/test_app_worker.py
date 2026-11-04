"""The chat runs off the event loop and an ambiguous account is never guessed.

The two defects this file pins (CR-17):

* ``tools/app.py`` called :func:`code.chat.ask` inside the message handler.  A
  provider turn can take seconds, and a synchronous call there froze the whole
  screen: no repaint, no cancel, no way out but killing the process.  A turn now
  runs in an exclusive Textual thread worker, the Chat tab shows what it is
  waiting for, and the cancel action stops waiting for it.
* ``on_mount`` opened ``service.list_users()[0]``.  With two people stored, the
  app silently picked one, so every figure on every tab belonged to somebody the
  person never chose.  With more than one account nothing is loaded until the
  picker is answered, and the decide/ask actions say "choose an account".

The provider is the scripted double from ``tests/support``: it sleeps on
purpose, so "the handler returned early" is measured rather than assumed.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import code.accounts as store
from code import service
from tests.support.fake_provider import FakeProvider


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


def _account(name: str, *, balance: str = "12000", floor: str = "2000") -> dict:
    """One stored person with the two profile facts a decision needs."""
    account = store.create(name, home_currency="ZAR")
    service.update_profile(account, balance=balance, floor=floor)
    store.save(account)
    return account


def _two_people() -> None:
    _account("ana")
    _account("bea")


def _log_text(app) -> str:
    """What the Chat tab has actually written, line by line."""
    from textual.widgets import RichLog

    return "\n".join(line.text for line in app.query_one("#chat-log", RichLog).lines)


def _status_text(app) -> str:
    """The one-line progress message under the chat log."""
    return str(app.query_one("#chat-progress").render())


async def _open_chat(app, pilot) -> None:
    """Make the Chat tab visible so the RichLog knows its size and renders."""
    app.query_one("#tabs").active = "tab-chat"
    await pilot.pause()


# ------------------------------------------------------- the account picker


def test_two_stored_people_are_not_narrowed_to_the_first_one(db):
    """(a) Opening the app must not choose between people on its own."""
    from tools.app import BuyOrWait, UserPicker

    _two_people()

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.account is None, "the first stored person was loaded anyway"
            assert isinstance(app.screen, UserPicker), "no picker was offered"
        return True

    assert asyncio.run(drive()) is True


def test_picking_one_of_two_people_loads_that_person(db):
    """The explicit answer is what loads an account, and it is the one asked for."""
    from tools.app import BuyOrWait, UserPicker

    _two_people()

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, UserPicker)
            app.screen.query_one("#pick-bea").press()
            await pilot.pause()

            assert app.account is not None
            assert app.account["profile"]["user_id"] == "bea"
            assert not isinstance(app.screen, UserPicker), "the picker did not close"
        return True

    assert asyncio.run(drive()) is True


def test_decide_with_two_people_and_no_selection_reports_choose_an_account(db):
    """(a) The decide action asks for a choice instead of deciding for ana."""
    from tools.app import BuyOrWait

    _two_people()

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.pop_screen()  # dismiss the picker without answering it
            await pilot.pause()
            app.query_one("#tabs").active = "tab-buy"
            await pilot.pause()
            app.query_one("#buy-amount").value = "500"
            app.query_one("#buy-go").press()
            # A press posts a message; the handler runs a turn later than the
            # call.  Wait for the rendered answer instead of sampling the panel
            # on the next tick, or the assertion fails against an empty widget
            # that is merely still on its way.
            shown = ""
            for _ in range(20):
                await pilot.pause()
                shown = str(app.query_one("#buy-explain").render())
                if shown.strip():
                    break

            assert "choose an account" in shown, shown
            assert app.account is None
        return True

    assert asyncio.run(drive()) is True
    for name in ("ana", "bea"):
        stored = store.load(name)
        assert stored["requests"] == [], f"{name} was decided against without a choice"


def test_chat_with_two_people_and_no_selection_never_reaches_the_model(db, monkeypatch):
    """(a) The ask action asks for a choice and calls no provider."""
    from textual.widgets import Input

    from code import chat
    from tools.app import BuyOrWait

    _two_people()
    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("chat.ask ran without a chosen account")

    monkeypatch.setattr(chat, "ask", _spy)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.pop_screen()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))
            await pilot.pause()

            assert "choose an account" in _status_text(app), _status_text(app)
            assert "choose an account" in _log_text(app)
        return True

    assert asyncio.run(drive()) is True
    assert calls == []


# ------------------------------------------------------------- the worker


def test_the_ask_handler_returns_before_the_provider_answers(db, monkeypatch):
    """(b) The handler starts a worker and is free while the provider thinks."""
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import BuyOrWait

    account = _account("ana")
    # The app only shows a reply the kernel can vouch for, so the script asks for
    # one kernel observation first and then phrases it.  The figure comes from
    # the kernel, never from this file.
    position = service.position(account)
    reply = (
        f"Your ledger shows {position['max_safe_payment']} is the largest safe "
        "payment today."
    )
    provider = FakeProvider(
        tool_batches=[[{"name": "position", "args": {}, "id": "call_1"}]],
        replies=[reply],
        delay=0.6,
    )
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"

            started = time.monotonic()
            app._chat(Input.Submitted(field, field.value))
            elapsed = time.monotonic() - started

            worker = app.ask_worker
            assert worker is not None, "no worker was started for the chat turn"
            assert not worker.is_finished, "the handler waited for the provider"
            assert elapsed < 0.3, f"the handler blocked for {elapsed:.2f}s"
            assert "asking" in _status_text(app).lower(), _status_text(app)

            await worker.wait()
            await pilot.pause()
            # One turn asks for the kernel figure, the next one phrases it.
            assert provider.calls == 2
            assert reply in _log_text(app)
            assert _status_text(app) == ""
        return True

    assert asyncio.run(drive()) is True


def test_cancel_stops_waiting_for_the_provider(db, monkeypatch):
    """The cancel action calls ``worker.cancel`` and hides the late answer."""
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import BuyOrWait

    _account("ana")
    provider = FakeProvider(replies=["too late"], delay=0.5)
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))

            worker = app.ask_worker
            assert worker is not None and not worker.is_finished
            await pilot.press("escape")  # the binding, not the method
            assert worker.is_cancelled, "escape did not reach the cancel action"
            for _ in range(40):
                await pilot.pause()
                if worker.is_finished:
                    break
            await pilot.pause()

            assert worker.state.name == "CANCELLED", worker.state
            assert "cancelled" in _status_text(app).lower(), _status_text(app)
            assert "too late" not in _log_text(app)
        return True

    assert asyncio.run(drive()) is True


def test_a_second_ask_cancels_the_turn_it_replaces(db, monkeypatch):
    """The worker group is exclusive: one question at a time per screen."""
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import BuyOrWait

    account = _account("ana")
    # Both turns must be verifiable for the surviving one to be rendered: the
    # script asks the kernel for a figure, then phrases it, exactly as above.
    safe = service.position(account)["max_safe_payment"]
    replies = [
        f"Your ledger allows a {safe} payment today.",
        f"You can safely pay {safe} from your ledger today.",
    ]
    providers = []

    def _provider(choice, env=None):
        index = len(providers)
        provider = FakeProvider(
            tool_batches=[[{"name": "position", "args": {}, "id": f"call_{index + 1}"}]],
            replies=[replies[index]],
            delay=0.3,
        )
        providers.append(provider)
        return provider

    monkeypatch.setattr(model_registry, "provider_for", _provider)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)

            field.value = "first question"
            app._chat(Input.Submitted(field, field.value))
            first = app.ask_worker
            assert first is not None and not first.is_finished

            app._chat(Input.Submitted(field, "second question"))
            second = app.ask_worker
            assert second is not None and second is not first
            assert first.is_cancelled, "the replaced turn kept running"

            await second.wait()
            await pilot.pause()
            assert replies[1] in _log_text(app)
            assert replies[0] not in _log_text(app)
        return True

    assert asyncio.run(drive()) is True


def test_cancel_with_no_turn_running_is_harmless(db):
    """The action exists and does nothing when there is nothing to cancel."""
    from tools.app import BuyOrWait

    _account("ana")

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cancel_ask()
            assert app.ask_worker is None
        return True

    assert asyncio.run(drive()) is True
