"""``max_tool_calls`` bounds dispatched tool calls, not loop iterations.

CR-08: one model turn may ask for more calls than the run has left.  The batch is
trimmed to the remainder: only those calls reach the kernel, the overflow gets one
protocol-correct tool error each (every ``tool_call_id`` needs a reply, or the next
turn is malformed), and the run stops with ``reason="tool_budget"``.  The tool
budget is consumed per dispatched call, so a model that asks for one call per turn
cannot spend more than the same cap.
"""
from __future__ import annotations

import json

import pytest

import code.accounts as store
from code import chat, service
from tests.support.fake_provider import FakeProvider

ERROR_KEYS = {"code", "retryable", "safe_summary"}


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


def _calls(count: int, *, start: int = 1, name: str = "position") -> list[dict]:
    return [
        {"name": name, "args": {}, "id": f"call_{index}"}
        for index in range(start, start + count)
    ]


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


def _counting_dispatch(monkeypatch) -> list[str]:
    """Record every call that actually reaches the kernel."""
    executed: list[str] = []
    original = chat.dispatch

    def dispatch(account, name, args=None):
        executed.append(str(name))
        return original(account, name, args)

    monkeypatch.setattr(chat, "dispatch", dispatch)
    return executed


def _tool_turns(messages) -> list[dict]:
    return [message for message in messages if message.get("role") == "tool"]


def _payloads(messages) -> list[dict]:
    return [json.loads(message.get("content") or "{}") for message in _tool_turns(messages)]


def test_a_ten_call_batch_is_cut_to_the_budget(db, monkeypatch):
    account = _account()
    provider = FakeProvider(tool_batches=[_calls(10)])
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(
        account, "how much can I spend?", provider=provider, max_tool_calls=4
    )

    # Exactly four calls reached the kernel, in the order the model asked.
    assert executed == ["position"] * 4
    assert result["tool_calls"] == ["position"] * 4
    assert result["reason"] == "tool_budget"
    assert result["verified"] is False
    # The run stops instead of asking the overspending model for another turn.
    assert provider.calls == 1

    # Every call id has a reply: four kernel results, six budget errors.
    tool_turns = _tool_turns(seen[0])
    assert [message["tool_call_id"] for message in tool_turns] == [
        f"call_{index}" for index in range(1, 11)
    ]
    payloads = _payloads(seen[0])
    errors = [payload for payload in payloads if set(payload) == ERROR_KEYS]
    assert len(errors) == 6
    assert all(payload["code"] == "TOOL_BUDGET" for payload in errors)
    assert all(payload["retryable"] is False for payload in errors)
    assert all("budget" in payload["safe_summary"].lower() for payload in errors)
    assert [payload for payload in payloads if set(payload) != ERROR_KEYS] == [
        chat.dispatch(account, "position") for _ in range(4)
    ]


def test_the_budget_counts_calls_across_turns(db, monkeypatch):
    account = _account()
    provider = FakeProvider(tool_batches=[_calls(3), _calls(3, start=4)])
    _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(
        account, "how much can I spend?", provider=provider, max_tool_calls=4
    )

    assert executed == ["position"] * 4
    assert result["tool_calls"] == ["position"] * 4
    assert result["reason"] == "tool_budget"
    assert provider.calls == 2


def test_a_model_that_stops_at_the_budget_can_still_answer(db):
    account = _account()
    position = chat.dispatch(account, "position")
    reply = (
        f"Your ledger shows {position['max_safe_payment']} is the largest safe "
        "payment today."
    )
    provider = FakeProvider(tool_batches=[_calls(4)], replies=[reply])

    result = chat.ask(
        account, "how much can I spend?", provider=provider, max_tool_calls=4
    )

    assert result["tool_calls"] == ["position"] * 4
    assert result["reason"] == "answered"
    assert result["verified"] is True
    assert result["reply"] == reply
    # One turn carried the batch; the next turn is the answer, not another call.
    assert provider.calls == 2


def test_a_zero_budget_rejects_the_batch_before_dispatch(db, monkeypatch):
    account = _account()
    provider = FakeProvider(tool_batches=[_calls(1)])
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(
        account, "how much can I spend?", provider=provider, max_tool_calls=0
    )

    assert executed == []
    assert result["tool_calls"] == []
    assert result["reason"] == "tool_budget"
    payloads = _payloads(seen[0])
    assert len(payloads) == 1
    assert set(payloads[0]) == ERROR_KEYS
    assert payloads[0]["code"] == "TOOL_BUDGET"
    assert payloads[0]["retryable"] is False
    assert "budget" in payloads[0]["safe_summary"].lower()
