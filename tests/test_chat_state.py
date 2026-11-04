"""Every chat turn has a typed state, and the user-visible summary derives from it.

CR-19 (typed state): a turn used to report only its prose and a bag of flags, so
the screen had no way to say what actually happened.  :class:`code.chat.ChatRunState`
is the one record of a turn:

* ``phase`` moves ``received`` -> ``executing`` -> ``verifying`` -> ``completed``,
  or ends in ``fallback`` with a reason when the answer could not be grounded;
* ``tool_calls_used`` counts dispatched calls, the same number ``max_tool_calls``
  bounds;
* ``kernel_result`` is true only when a dispatched call returned a kernel figure;
* ``observations`` keeps the tool, its arguments and its result for the turn.

:func:`code.chat.ask` returns the state under ``"state"``, writes it to the trace
when an enabled recorder is supplied, and derives ``"run_summary"`` from it.
"""
from __future__ import annotations

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


def _turn(index: int, reply: str) -> list[FakeMessage]:
    """A kernel call followed by a spoken answer."""
    return [
        FakeMessage("", [{"name": "position", "args": {}, "id": f"call_{index}"}]),
        FakeMessage(reply),
    ]


class _Recorder:
    """The slice of ``TraceRecorder`` this module needs: enabled and ``record``."""

    enabled = True

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def record(self, kind, payload=None):
        self.events.append((str(kind), dict(payload or {})))
        return payload


class _DisabledRecorder:
    enabled = False

    def record(self, kind, payload=None):  # pragma: no cover - must not run
        raise AssertionError("a disabled recorder must never be written to")


def test_the_state_contract_is_the_published_one():
    fields = set(chat.ChatRunState.__dataclass_fields__)
    assert {
        "run_id",
        "account_id",
        "phase",
        "tool_calls_used",
        "observations",
        "kernel_result",
        "fallback_reason",
    } <= fields
    assert chat.CHAT_PHASES == frozenset(
        {"received", "executing", "verifying", "completed", "fallback"}
    )


def test_a_completed_turn_reports_the_dispatched_call_count(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))

    result = chat.ask(account, "what can I spend?", provider=provider)

    state = result["state"]
    assert isinstance(state, chat.ChatRunState)
    assert state.phase == "completed"
    assert state.phase in chat.CHAT_PHASES
    assert state.tool_calls_used == 1
    assert state.tool_calls_used == len(result["tool_calls"]) == 1
    assert state.kernel_result is True
    assert state.fallback_reason is None
    assert state.account_id == "ana"
    assert state.run_id

    assert result["run_summary"]["tools"] == ["position"]
    assert result["run_summary"]["kernel_result"] is True
    assert result["run_summary"]["evidence_found"] is False
    assert result["run_summary"]["fallback_reason"] is None
    assert isinstance(result["run_summary"]["elapsed_ms"], int)
    assert result["run_summary"]["elapsed_ms"] >= 0
    # The summary is derived from the state, not assembled beside it.
    assert result["run_summary"]["tools"] == [item["tool"] for item in state.observations]


def test_a_refused_financial_answer_is_a_fallback_with_a_reason(db):
    account = _account()
    provider = FakeProvider(replies=["You can spend 12,345 today."])

    result = chat.ask(account, "how much can I spend?", provider=provider)

    state = result["state"]
    assert result["reply"] == chat.UNVERIFIED_REPLY
    assert state.phase == "fallback"
    assert state.fallback_reason == "unverified"
    assert state.tool_calls_used == 0
    assert state.kernel_result is False
    assert result["run_summary"]["fallback_reason"] == "unverified"
    assert result["run_summary"]["tools"] == []
    assert result["run_summary"]["kernel_result"] is False


def test_a_budget_stop_is_a_fallback_with_its_own_reason(db):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[
            {"name": "position", "args": {}, "id": "call_1"},
            {"name": "position", "args": {}, "id": "call_2"},
        ]]
    )

    result = chat.ask(
        account, "how much can I spend?", provider=provider, max_tool_calls=1
    )

    state = result["state"]
    assert result["reason"] == "tool_budget"
    assert state.phase == "fallback"
    assert state.fallback_reason == "tool_budget"
    assert state.tool_calls_used == 1
    assert result["run_summary"]["fallback_reason"] == "tool_budget"


def test_the_state_is_written_to_an_enabled_trace(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))
    recorder = _Recorder()

    result = chat.ask(
        account, "what can I spend?", provider=provider, trace=recorder
    )

    kinds = [kind for kind, _payload in recorder.events]
    assert kinds == ["run_state"]
    payload = recorder.events[0][1]
    assert payload["run_id"] == result["state"].run_id
    assert payload["account_id"] == "ana"
    assert payload["phase"] == "completed"
    assert payload["tool_calls_used"] == 1
    assert payload["kernel_result"] is True
    assert payload["fallback_reason"] is None
    assert [item["tool"] for item in payload["observations"]] == ["position"]


def test_a_disabled_trace_is_never_written(db):
    account = _account()
    provider = FakeProvider(replies=_turn(1, "Your ledger is the only source."))

    result = chat.ask(
        account, "what can I spend?", provider=provider, trace=_DisabledRecorder()
    )

    assert result["state"].phase == "completed"
