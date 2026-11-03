"""Pins the scripted provider double the chat protocol tests import.

The double exists so a protocol test never needs a network, an SDK or a live
model.  These tests fix the two behaviours a loop depends on (one tool batch then
one reply, and a caller-visible delay) plus the budget contract every chat
provider has to honour.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_one_tool_batch_then_one_reply_drives_a_two_step_loop():
    provider = FakeProvider(
        tool_batches=[[{"name": "position", "args": {"amount": "10"}, "id": "call_1"}]],
        replies=["You can spend 10."],
    )
    bound = provider.chat_model().bind_tools([])
    messages = [{"role": "user", "content": "how much can I spend?"}]

    dispatched: list[str] = []
    for _ in range(5):
        message = bound.invoke(messages)
        messages.append(message)
        if not message.tool_calls:
            break
        dispatched.extend(call["name"] for call in message.tool_calls)

    assert dispatched == ["position"]
    assert messages[1].tool_calls[0]["args"] == {"amount": "10"}
    assert messages[1].tool_calls[0]["id"] == "call_1"
    assert messages[-1].content == "You can spend 10."
    assert messages[-1].tool_calls == []
    assert provider.calls == 2


def test_invoke_blocks_for_at_least_the_configured_delay():
    provider = FakeProvider(replies=["done"], delay=0.05)
    started = time.monotonic()
    provider.chat_model().invoke([])
    assert time.monotonic() - started >= 0.05


def test_bind_raises_flag_surfaces_from_bind_tools():
    provider = FakeProvider(bind_raises=True)
    with pytest.raises(RuntimeError):
        provider.chat_model().bind_tools([])


def test_reserve_call_spends_the_budget_and_reports_remaining():
    provider = FakeProvider(max_calls=2)

    assert provider.remaining_calls == 2
    assert provider.reserve_call() is True
    assert provider.reserve_call() is True
    assert provider.reserve_call() is False
    assert provider.remaining_calls == 0
    # Reserving is not an invocation, so ``calls`` stays at zero.
    assert provider.calls == 0


def test_identity_defaults_match_the_chat_provider_contract():
    provider = FakeProvider()

    assert (provider.name, provider.model, provider.privacy) == ("fake", "fake-1", "local")
    assert provider.max_calls == 50
    assert provider.calls == 0
    assert provider.remaining_calls == 50


def test_an_exhausted_script_answers_without_more_tool_calls():
    provider = FakeProvider(replies=["only reply"])
    model = provider.chat_model()

    first = model.invoke([])
    exhausted = model.invoke([])

    assert first.content == "only reply"
    assert exhausted.tool_calls == []
    assert exhausted.content
    assert provider.calls == 2
    # The script is consumed, not replayed, so a loop cannot spin forever.
    assert model.invoke([]).content == exhausted.content


def test_the_double_imports_without_langchain():
    code = (
        "import sys; "
        "sys.modules['langchain_core'] = None; "
        "sys.modules['langchain_core.messages'] = None; "
        "from tests.support.fake_provider import FakeProvider; "
        "FakeProvider().chat_model().invoke([])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
