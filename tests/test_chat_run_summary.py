"""A turn says what it did, in one line the screen can render.

CR-19 (run summary): the Chat tab showed the reply and a list of tool names, but
nothing said whether a kernel figure backed the answer, whether supporting
evidence was consulted, or why a turn fell back.  ``ask`` now returns
``run_summary``, derived from :class:`code.chat.ChatRunState` (Task 27):

    {"tools": [...], "kernel_result": bool, "evidence_found": bool,
     "fallback_reason": str | None, "elapsed_ms": int}

and ``tools/app.py`` writes one dim line after the reply.  The summary is
descriptive only: it never adds a figure, and it can only repeat what the state
already recorded for this turn.
"""
from __future__ import annotations

import asyncio

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


def _turn(index: int, reply: str) -> list[FakeMessage]:
    """One kernel call followed by a spoken answer."""
    return [
        FakeMessage("", [{"name": "position", "args": {}, "id": f"call_{index}"}]),
        FakeMessage(reply),
    ]


def _log_text(app) -> str:
    """What the Chat tab has actually written, line by line."""
    from textual.widgets import RichLog

    return "\n".join(line.text for line in app.query_one("#chat-log", RichLog).lines)


# ------------------------------------------------------------- the contract


def test_a_tool_then_answer_yields_a_run_summary(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))

    result = chat.ask(account, "what can I spend?", provider=provider)

    summary = result["run_summary"]
    assert summary["tools"] == ["position"]
    assert summary["kernel_result"] is True
    assert summary["evidence_found"] is False
    assert summary["fallback_reason"] is None
    assert isinstance(summary["elapsed_ms"], int)
    assert summary["elapsed_ms"] >= 0


def test_a_refused_answer_names_its_fallback(db):
    account = _account()
    provider = FakeProvider(replies=["You can spend 9,999."])

    result = chat.ask(account, "how much can I spend?", provider=provider)

    assert result["run_summary"]["tools"] == []
    assert result["run_summary"]["kernel_result"] is False
    assert result["run_summary"]["fallback_reason"] == "unverified"


# --------------------------------------------------------------- the line


def test_the_line_states_the_tools_and_the_kernel_result():
    from tools.app import run_summary_line

    line = run_summary_line({
        "tools": ["position"],
        "kernel_result": True,
        "evidence_found": False,
        "fallback_reason": None,
        "elapsed_ms": 12,
    })

    assert "tools: position" in line
    assert "kernel result" in line
    assert "no evidence" in line
    assert "fallback" not in line
    assert "12 ms" in line


def test_the_line_states_a_fallback_and_a_missing_kernel_result():
    from tools.app import run_summary_line

    line = run_summary_line({
        "tools": [],
        "kernel_result": False,
        "evidence_found": False,
        "fallback_reason": "unverified",
        "elapsed_ms": 3,
    })

    assert "no tools" in line
    assert "no kernel result" in line
    assert "fallback: unverified" in line


def test_no_tools_means_no_tools_in_the_line():
    from tools.app import run_summary_line

    line = run_summary_line({"tools": [], "kernel_result": False})

    assert "no tools" in line


# ----------------------------------------------------------------- the app


def test_the_app_prints_one_summary_line_after_the_reply(db, monkeypatch):
    from textual.widgets import Input

    from code import models as model_registry
    from tools.app import BuyOrWait

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
        app = BuyOrWait()
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

            lines = [line for line in _log_text(app).splitlines() if line.strip()]
            assert reply in lines
            assert lines[-1].startswith("tools: position"), lines[-1]
            assert "kernel result" in lines[-1]
            assert "no evidence" in lines[-1]
            assert "fallback" not in lines[-1]
        return True

    assert asyncio.run(drive()) is True
