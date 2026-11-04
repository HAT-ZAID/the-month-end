"""A model tool call is untrusted input until the schema says otherwise.

CR-09: the conversation publishes a closed schema for every tool, checks each
call locally before the kernel runs, and answers a rejected call with a
structured ``{code, retryable, safe_summary}`` envelope.  An unknown tool, a
non-object argument payload, an undeclared field, a repeated call id and a
numeric ``amount`` all stop at the protocol boundary; none of them becomes a
kernel call or a ``{}`` default.

The scripted double here is deliberately local: ``FakeProvider`` normalises
``args`` with ``dict(...)`` and has no ``invalid_tool_calls``, so it could never
deliver the malformed protocol payloads these tests exist to reject.
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


def _counting_dispatch(monkeypatch) -> list[str]:
    """Record every call that actually reaches the kernel."""
    executed: list[str] = []
    original = chat.dispatch

    def dispatch(account, name, args=None):
        executed.append(str(name))
        return original(account, name, args)

    monkeypatch.setattr(chat, "dispatch", dispatch)
    return executed


def _capture_messages(monkeypatch, provider) -> list[list[dict]]:
    """The ``messages`` list by reference, so later appends stay visible."""
    model = provider.chat_model()
    original = model.invoke
    seen: list[list[dict]] = []

    def invoke(messages, **kwargs):
        seen.append(messages)
        return original(messages, **kwargs)

    monkeypatch.setattr(model, "invoke", invoke)
    return seen


def _payloads(messages) -> list[dict]:
    return [
        json.loads(message.get("content") or "{}")
        for message in messages
        if message.get("role") == "tool"
    ]


# ------------------------------------------------------- malformed protocol


class _RawMessage:
    """One assistant turn with the raw fields a provider can really return."""

    def __init__(self, content="", tool_calls=None, invalid_tool_calls=None):
        self.content = str(content or "")
        self.tool_calls = list(tool_calls or ())
        self.invalid_tool_calls = list(invalid_tool_calls or ())


class _ScriptedModel:
    def __init__(self, provider):
        self.provider = provider

    def bind_tools(self, schemas, **kwargs):
        self.provider.schemas = schemas
        return self

    def invoke(self, messages, **kwargs):
        provider = self.provider
        provider.calls += 1
        provider.sent.append(messages)
        if provider.turns:
            return provider.turns.pop(0)
        return _RawMessage("no further scripted turns")


class _ScriptedProvider:
    """A provider that replays exact message objects, malformed fields included."""

    name = "scripted"
    model = "scripted-1"
    privacy = "local"

    def __init__(self, turns, max_calls=50):
        self.turns = list(turns or ())
        self.max_calls = max(1, int(max_calls))
        self.reserved_calls = 0
        self.calls = 0
        self.sent: list[list[dict]] = []
        self.schemas = None
        self._model = _ScriptedModel(self)

    def chat_model(self):
        return self._model

    def reserve_call(self) -> bool:
        if self.reserved_calls >= self.max_calls:
            return False
        self.reserved_calls += 1
        return True

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.reserved_calls)


# ------------------------------------------------------------------- schemas


def test_every_tool_schema_is_closed_and_amount_is_a_string():
    for schema in chat.TOOL_SCHEMAS:
        parameters = schema["function"]["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False, schema["function"]["name"]

    what_if = next(
        schema for schema in chat.TOOL_SCHEMAS
        if schema["function"]["name"] == "what_if"
    )
    assert what_if["function"]["parameters"]["properties"]["amount"]["type"] == "string"


def test_validate_tool_call_returns_stable_codes():
    assert chat.validate_tool_call("position", {}) == []
    assert chat.validate_tool_call("forecast_everything", {}) == ["UNKNOWN_TOOL"]
    assert chat.validate_tool_call("position", ["not", "an", "object"]) == ["BAD_ARGS"]
    assert chat.validate_tool_call("position", {"account_id": "someone_else"}) == [
        "EXTRA_FIELD"
    ]
    assert chat.validate_tool_call("decide", {}) == ["BAD_ARGS"]
    assert chat.validate_tool_call("what_if", {"amount": 500}) == ["BAD_ARGS"]
    assert chat.validate_tool_call("what_if", {"amount": "500.00"}) == []


# ---------------------------------------------------------------- rejections


def test_an_extra_field_is_rejected_before_dispatch(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[{
            "name": "position",
            "args": {"account_id": "someone_else"},
            "id": "call_1",
        }]]
    )
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what can I spend?", provider=provider, max_tool_calls=2)

    assert executed == []
    assert result["tool_calls"] == []
    payloads = _payloads(seen[0])
    assert len(payloads) == 1
    assert set(payloads[0]) == ERROR_KEYS
    assert payloads[0]["code"] == "EXTRA_FIELD"
    assert payloads[0]["retryable"] is True
    # The argument is untrusted data: the reply never repeats its value.
    assert "someone_else" not in payloads[0]["safe_summary"]


def test_non_dict_arguments_are_rejected_not_coerced(db, monkeypatch):
    account = _account()
    provider = _ScriptedProvider(
        turns=[_RawMessage(
            "",
            [{"name": "position", "args": "not-an-object", "id": "call_1"}],
        )]
    )
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what can I spend?", provider=provider)

    assert executed == []
    assert result["tool_calls"] == []
    payload = _payloads(provider.sent[0])[0]
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "BAD_ARGS"
    assert payload["retryable"] is True
    assert "json object" in payload["safe_summary"].lower()


def test_duplicate_call_ids_are_rejected(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[
            {"name": "position", "args": {}, "id": "call_same"},
            {"name": "facts", "args": {}, "id": "call_same"},
        ]]
    )
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what can I spend?", provider=provider)

    assert executed == ["position"]
    assert result["tool_calls"] == ["position"]
    payloads = _payloads(seen[0])
    assert len(payloads) == 2
    assert set(payloads[1]) == ERROR_KEYS
    assert payloads[1]["code"] == "DUPLICATE_ID"
    assert payloads[1]["retryable"] is True
    assert "unique" in payloads[1]["safe_summary"].lower()


def test_an_unknown_tool_is_rejected_before_dispatch(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[{
            "name": "forecast_everything",
            "args": {},
            "id": "call_1",
        }]]
    )
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what can I spend?", provider=provider)

    assert executed == []
    assert result["tool_calls"] == []
    payload = _payloads(seen[0])[0]
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "UNKNOWN_TOOL"
    assert "position" in payload["safe_summary"]


def test_amount_as_a_number_is_rejected_with_string_guidance(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[{
            "name": "what_if",
            "args": {"amount": 500},
            "id": "call_1",
        }]]
    )
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what if I spend 500?", provider=provider)

    assert executed == []
    assert result["tool_calls"] == []
    payload = _payloads(seen[0])[0]
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "BAD_ARGS"
    assert "string" in payload["safe_summary"].lower()
    assert "number" in payload["safe_summary"].lower()


# ------------------------------------------------- positive controls


def test_a_string_amount_reaches_the_kernel(db, monkeypatch):
    account = _account()
    provider = FakeProvider(
        tool_batches=[[{
            "name": "what_if",
            "args": {"amount": "500.00"},
            "id": "call_1",
        }]]
    )
    seen = _capture_messages(monkeypatch, provider)
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what if I spend 500?", provider=provider)

    assert executed == ["what_if"]
    assert result["tool_calls"] == ["what_if"]
    payload = _payloads(seen[0])[0]
    assert "code" not in payload
    assert "affordability_status" in payload


def test_invalid_tool_calls_are_replied_to_and_never_dispatched(db, monkeypatch):
    account = _account()
    provider = _ScriptedProvider(
        turns=[
            _RawMessage(
                "",
                [],
                [{
                    "name": "what_if",
                    "args": "{not json",
                    "id": "bad_1",
                    "error": "failed to parse arguments",
                }],
            ),
            _RawMessage("I need a valid amount before I can answer."),
        ]
    )
    executed = _counting_dispatch(monkeypatch)

    result = chat.ask(account, "what if I spend 500?", provider=provider)

    assert executed == []
    assert result["tool_calls"] == []
    assert result["verified"] is False
    # A clarification is not a money claim, so it is passed through rather than
    # replaced by the refusal; the flag still says nothing was verified.
    assert result["reply"] == "I need a valid amount before I can answer."
    payload = _payloads(provider.sent[0])[0]
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "BAD_ARGS"
    assert payload["retryable"] is True
    assert "not executed" in payload["safe_summary"]


def test_protocol_envelopes_are_the_only_tool_errors():
    assert chat._tool_error(
        {"code": "BAD_ARGS", "retryable": True, "safe_summary": "no"}
    )
    assert not chat._tool_error({"amount_safe_to_pay": "10.00"})
    assert not chat._tool_error(None)
