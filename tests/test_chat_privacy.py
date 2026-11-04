"""A hosted provider may not see account data without an explicit yes.

CR-19 (consent gate): ``ask`` reached the model before anything asked the person
whether a free or unclassified endpoint may receive their ledger.  The gate is
now at the top of :func:`code.chat.ask`, before ``provider.chat_model()`` and
before any budget is claimed:

* ``requires_consent(provider)`` is true unless the provider declares the
  ``local`` or ``paid`` privacy class -- an unclassified endpoint is treated as
  the least trusted class;
* ``NeedsConsent`` is raised instead of sending, and no model call happens;
* ``tools/app.py`` shows the warning, holds the question, and passes
  ``consent=True`` only after the person presses "Allow sending".
"""
from __future__ import annotations

import asyncio

import pytest

import code.accounts as store
from code import chat, service
from code.providers import FREE_TIER_WARNING, PRIVACY_FREE, PRIVACY_LOCAL, PRIVACY_PAID
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


def _turn(index: int, reply: str) -> list[FakeMessage]:
    return [
        FakeMessage("", [{"name": "position", "args": {}, "id": f"call_{index}"}]),
        FakeMessage(reply),
    ]


def _log_text(app) -> str:
    from textual.widgets import RichLog

    return "\n".join(line.text for line in app.query_one("#chat-log", RichLog).lines)


# ------------------------------------------------------------- the gate


def test_hosted_provider_without_consent_is_refused(db):
    account = _account()
    fake_hosted = FakeProvider(privacy=PRIVACY_FREE, replies=["You can spend 10."])

    with pytest.raises(chat.NeedsConsent):
        chat.ask(account, "how much can I spend?", provider=fake_hosted)

    assert fake_hosted.calls == 0
    assert fake_hosted.reserved_calls == 0


def test_the_gate_fires_before_the_model_is_even_built(db):
    account = _account()

    class _Spy(FakeProvider):
        def chat_model(self):  # pragma: no cover - must not be reached
            raise AssertionError("chat_model was called before consent")

    provider = _Spy(privacy=PRIVACY_FREE)

    with pytest.raises(chat.NeedsConsent):
        chat.ask(account, "how much can I spend?", provider=provider)


def test_local_provider_needs_no_consent(db):
    account = _account()
    local_provider = FakeProvider(
        privacy=PRIVACY_LOCAL, replies=_turn(1, "Your ledger is the only source.")
    )

    result = chat.ask(account, "hello", provider=local_provider)

    assert result["reply"]
    assert local_provider.calls == 2


def test_paid_provider_needs_no_consent(db):
    account = _account()
    paid_provider = FakeProvider(
        privacy=PRIVACY_PAID, replies=_turn(1, "Your ledger is the only source.")
    )

    result = chat.ask(account, "what can I spend?", provider=paid_provider)

    assert result["state"].phase == "completed"
    assert paid_provider.calls == 2


def test_an_unclassified_provider_is_treated_as_hosted(db):
    account = _account()

    class _Unclassified:
        name = "unclassified"
        model = "u-1"

        def chat_model(self):  # pragma: no cover - must not be reached
            raise AssertionError("an unclassified provider was asked without consent")

    provider = _Unclassified()
    assert chat.requires_consent(provider) is True

    with pytest.raises(chat.NeedsConsent):
        chat.ask(account, "how much can I spend?", provider=provider)


def test_consent_true_lets_a_hosted_provider_answer(db):
    account = _account()
    provider = FakeProvider(
        privacy=PRIVACY_FREE, replies=_turn(1, "Your ledger is the only source.")
    )

    result = chat.ask(
        account, "what can I spend?", provider=provider, consent=True
    )

    assert result["state"].phase == "completed"
    assert provider.calls == 2


def test_the_refusal_says_what_consent_is_for(db):
    account = _account()
    provider = FakeProvider(privacy=PRIVACY_FREE)

    with pytest.raises(chat.NeedsConsent) as excinfo:
        chat.ask(account, "how much can I spend?", provider=provider)

    message = str(excinfo.value).lower()
    assert "consent" in message
    assert "train" in message
    # It is a refusal, not a model failure.
    assert isinstance(excinfo.value, RuntimeError)


def test_no_configured_provider_is_no_model_not_a_consent_question(db, monkeypatch):
    """An absent model must not be reported as a privacy decision to approve."""
    monkeypatch.setenv("BUY_WAIT_LLM_PROVIDER", "none")
    account = _account()

    with pytest.raises(chat.NoModel):
        chat.ask(account, "how much can I spend?")


# ----------------------------------------------------------------- the app


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


def test_the_app_holds_a_hosted_question_until_it_is_allowed(db, monkeypatch):
    from textual.widgets import Button, Input

    from code import models as model_registry
    from tools.app import TheMonthEnd

    _account("ana")
    provider = FakeProvider(
        privacy=PRIVACY_FREE, replies=_turn(1, "Your ledger is the only source.")
    )
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )
    calls = _spy_on_ask(monkeypatch)

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))
            await pilot.pause()

            # Nothing reached the model, and the question is held, not lost.
            assert calls == [], "the app asked the model before consent"
            assert provider.calls == 0
            assert app.ask_worker is None
            assert app.pending_ask is not None
            assert app.pending_ask[0] == "how much can I spend?"

            text = _log_text(app)
            assert " ".join(FREE_TIER_WARNING.split()).lower() in " ".join(text.split()).lower()
            assert "train" in text.lower()
            assert "allow sending" in text.lower()
            assert not app.query_one("#chat-consent", Button).disabled

            # The explicit press is the only thing that sends it.
            app.query_one("#chat-consent", Button).press()
            for _ in range(20):
                await pilot.pause()
                if app.ask_worker is not None:
                    break
            worker = app.ask_worker
            assert worker is not None, "the confirmation did not start the turn"
            await worker.wait()
            await pilot.pause()

            assert calls == [True]
            assert app.pending_ask is None
            assert app.query_one("#chat-consent", Button).disabled
        return True

    assert asyncio.run(drive()) is True


def test_the_app_does_not_block_a_local_question(db, monkeypatch):
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import TheMonthEnd

    _account("ana")
    provider = FakeProvider(
        privacy=PRIVACY_LOCAL, replies=_turn(1, "Your ledger is the only source.")
    )
    monkeypatch.setattr(
        model_registry, "provider_for", lambda choice, env=None: provider
    )
    calls = _spy_on_ask(monkeypatch)

    async def drive():
        app = TheMonthEnd()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_chat(app, pilot)
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))

            worker = app.ask_worker
            assert worker is not None, "a local question waited for confirmation"
            await worker.wait()
            await pilot.pause()

            assert calls == [False]
            assert app.pending_ask is None
        return True

    assert asyncio.run(drive()) is True


def test_cancelling_withdraws_a_held_hosted_question(db, monkeypatch):
    from textual.widgets import Button, Input

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
            app.model_choice = "fake:fake-1"
            field = app.query_one("#chat-input", Input)
            field.value = "how much can I spend?"
            app._chat(Input.Submitted(field, field.value))
            await pilot.pause()
            assert app.pending_ask is not None

            app.action_cancel_ask()
            await pilot.pause()

            assert app.pending_ask is None
            assert app.query_one("#chat-consent", Button).disabled
            app.query_one("#chat-consent", Button).press()
            await pilot.pause()
            assert calls == []
            assert provider.calls == 0
        return True

    assert asyncio.run(drive()) is True
