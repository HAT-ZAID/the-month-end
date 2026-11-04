"""I3: an armed free-tier consent belongs to one exact provider and model.

The "Allow sending" button used to keep a held question armed through a Position
refresh (which clears the chat log, warning included) and through a model change
(which can put a different, free provider behind the same button).  Consent is
now bound to the provider and model that were warned about: a refresh, any
model change, or a warning that no longer applies withdraws it, and a new ask
has to be consented to again.
"""
from __future__ import annotations

import asyncio

import pytest

import code.accounts as store
from code import chat, service
from code.providers import FREE_TIER_WARNING, PRIVACY_FREE, PRIVACY_LOCAL
from tests.support.fake_provider import FakeProvider


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


def _spy_on_ask(monkeypatch) -> list:
    """Record the ``consent`` value the app hands to ``chat.ask``."""
    calls: list = []
    original = chat.ask

    def spy(account, text, provider=None, **kwargs):
        calls.append(kwargs.get("consent"))
        return original(account, text, provider=provider, **kwargs)

    monkeypatch.setattr(chat, "ask", spy)
    return calls


async def _open_chat(app, pilot) -> None:
    app.query_one("#tabs").active = "tab-chat"
    await pilot.pause()


def _arm(app, pilot, question: str) -> None:
    from textual.widgets import Input

    field = app.query_one("#chat-input", Input)
    field.value = question
    app._chat(Input.Submitted(field, field.value))


def test_switching_models_withdraws_an_armed_consent(db, monkeypatch):
    from textual.widgets import Button, Select

    from code import models as model_registry
    from tools.app import TheMonthEnd

    _account("ana")
    provider = FakeProvider(privacy=PRIVACY_FREE)
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )
    calls = _spy_on_ask(monkeypatch)

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:free-1"
            _arm(app, pilot, "how much can I spend?")
            await pilot.pause()

            assert app.pending_ask is not None
            assert app.pending_ask.model == "fake:free-1"
            assert app.pending_ask.warning == FREE_TIER_WARNING

            # Another model choice: the held question must not stay armed.
            app._model_changed(
                Select.Changed(
                    app.query_one("#model-select", Select), "fake:local-2"
                )
            )
            await pilot.pause()

            assert app.pending_ask is None
            assert app.query_one("#chat-consent", Button).disabled

            # A new question asks for consent again instead of reusing the yes.
            _arm(app, pilot, "and now?")
            await pilot.pause()
            assert app.pending_ask is not None
            assert not app.query_one("#chat-consent", Button).disabled
            assert calls == []
            assert provider.calls == 0
        return True

    assert asyncio.run(drive()) is True


def test_refreshing_the_position_withdraws_an_armed_consent(db, monkeypatch):
    from textual.widgets import Button

    from code import models as model_registry
    from tools.app import TheMonthEnd

    _account("ana")
    provider = FakeProvider(privacy=PRIVACY_FREE)
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )
    calls = _spy_on_ask(monkeypatch)

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:free-1"
            _arm(app, pilot, "how much can I spend?")
            await pilot.pause()
            assert app.pending_ask is not None

            # Refresh clears the log; the held question must go with it.
            app.query_one("#refresh-position", Button).press()
            await pilot.pause()

            assert app.pending_ask is None
            assert app.query_one("#chat-consent", Button).disabled
            assert calls == []
            assert provider.calls == 0
        return True

    assert asyncio.run(drive()) is True


def test_a_consent_whose_warning_no_longer_applies_is_withdrawn(db, monkeypatch):
    from textual.widgets import Button

    from code import models as model_registry
    from tools.app import TheMonthEnd

    _account("ana")
    provider = FakeProvider(privacy=PRIVACY_FREE)
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )
    calls = _spy_on_ask(monkeypatch)

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:free-1"
            _arm(app, pilot, "how much can I spend?")
            await pilot.pause()
            assert app.pending_ask is not None

            # The warning that was shown no longer applies to this endpoint.
            provider.privacy = PRIVACY_LOCAL
            app.allow_pending_ask()
            await pilot.pause()

            assert app.pending_ask is None
            assert app.query_one("#chat-consent", Button).disabled
            assert calls == []
            assert provider.calls == 0
        return True

    assert asyncio.run(drive()) is True
