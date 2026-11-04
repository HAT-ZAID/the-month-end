"""A conversation remembers the turn before it, and only for one person.

CR-19, minimal: the chat loop used to rebuild its prompt from scratch on every
call, so a follow-up question ("and if I wait until Friday?") reached the model
with no memory of what had just been said.  :class:`code.chat.Session` keeps the
finished exchange in memory, in-memory only, for one person and for a bounded
time:

* a second turn replays the first turn's question and answer;
* the list is bounded, so a long conversation cannot grow without limit;
* an expired session starts clean;
* switching to another account clears the history instead of showing one
  person's answer inside another person's conversation.

No durable storage is involved: a session is an object the caller holds, never
something this module writes.
"""
from __future__ import annotations

import json
import time

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


def _account(name: str = "ana") -> dict:
    account = store.create(name, home_currency="ZAR")
    service.update_profile(account, balance="12000", floor="2000")
    store.save(account)
    return account


def _position_call(index: int = 1) -> dict:
    return {"name": "position", "args": {}, "id": f"call_{index}"}


def _turn(index: int, reply: str) -> list[FakeMessage]:
    """One scripted exchange: ask for a kernel figure, then phrase an answer.

    ``FakeProvider`` replays every queued tool batch before any reply, so a
    multi-turn script has to alternate the two kinds itself.
    """
    return [FakeMessage("", [_position_call(index)]), FakeMessage(reply)]


def _capture_messages(monkeypatch, provider) -> list[list[dict]]:
    """The ``messages`` list by reference: appends made after ``invoke`` stay visible."""
    model = provider.chat_model()
    original = model.invoke
    seen: list[list[dict]] = []

    def invoke(messages, **kwargs):
        seen.append(messages)
        return original(messages, **kwargs)

    monkeypatch.setattr(model, "invoke", invoke)
    return seen


def _roles(messages) -> list[str]:
    return [str(message.get("role")) for message in messages]


def test_a_second_turn_sees_the_first_turns_messages(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        replies=(
            _turn(1, "Your ledger is the only source I have.")
            + _turn(2, "Still the same ledger.")
        )
    )
    seen = _capture_messages(monkeypatch, provider)
    session = chat.Session()

    first = chat.ask(account, "what can I spend?", provider=provider, session=session)
    assert first["verified"] is True
    assert session.account_id == "ana"
    assert {"role": "user", "content": "what can I spend?"} in session.messages
    assert {"role": "assistant", "content": first["reply"]} in session.messages

    chat.ask(account, "and if I wait?", provider=provider, session=session)

    # The second turn's first prompt is the third ``invoke``: the scripted batch
    # and the reply of the first ask consumed two.  It must replay the exchange.
    second_prompt = seen[2]
    assert _roles(second_prompt)[0] == "system"
    assert {"role": "user", "content": "what can I spend?"} in second_prompt
    assert {"role": "assistant", "content": first["reply"]} in second_prompt
    # The history sits between the system prompt and this turn's question; tool
    # traffic follows it and belongs to this turn alone.
    assert _roles(second_prompt)[:5] == [
        "system", "user", "assistant", "user", "assistant",
    ]
    assert second_prompt[3] == {"role": "user", "content": "and if I wait?"}
    # Tool traffic is not replayed: a stale result must never look like this
    # turn's evidence, and every figure is re-read from the kernel.
    assert "tool" not in _roles(session.messages)


def test_switching_account_clears_the_history(db, monkeypatch):
    ana = _account("ana")
    bea = _account("bea")
    provider = FakeProvider(
        replies=(
            _turn(1, "Only ana should see this.")
            + _turn(2, "Only bea should see this.")
        )
    )
    seen = _capture_messages(monkeypatch, provider)
    session = chat.Session()

    chat.ask(ana, "ana's private question", provider=provider, session=session)
    assert session.account_id == "ana"
    assert session.messages

    chat.ask(bea, "bea's question", provider=provider, session=session)

    assert session.account_id == "bea"
    contents = [message.get("content") for message in session.messages]
    assert "ana's private question" not in contents
    # Both the stored history and the prompt sent for bea are free of ana's turn.
    assert "ana's private question" not in json.dumps(seen[2], default=str)
    assert {"role": "user", "content": "bea's question"} in seen[2]


def test_an_expired_session_starts_clean(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        replies=(
            _turn(1, "First answer, no figures.")
            + _turn(2, "Second answer, no figures.")
        )
    )
    seen = _capture_messages(monkeypatch, provider)
    session = chat.Session()

    chat.ask(account, "first question", provider=provider, session=session)
    # The session carries a deadline; moving past it discards the exchange.
    session.expires_at = time.monotonic() - 1

    chat.ask(account, "second question", provider=provider, session=session)

    prompt = seen[2]
    assert {"role": "user", "content": "first question"} not in prompt
    assert {"role": "user", "content": "second question"} in prompt
    assert _roles(session.messages) == ["user", "assistant"]


def test_the_history_is_bounded(db):
    account = _account()
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    script: list[FakeMessage] = []
    for index, word in enumerate(words, start=1):
        script.extend(_turn(index, word))
    provider = FakeProvider(replies=script, max_calls=40)
    session = chat.Session()

    for index, _word in enumerate(words):
        chat.ask(
            account,
            f"question {index}",
            provider=provider,
            session=session,
        )

    assert len(session.messages) <= chat.MAX_SESSION_MESSAGES
    assert len(session.messages) <= 12
    assert session.messages[-1] == {"role": "assistant", "content": words[-1]}
    # The oldest exchange has been trimmed; the newest is intact.
    assert {"role": "user", "content": "question 0"} not in session.messages
    assert {"role": "user", "content": "question 7"} in session.messages
