"""A chat answer is checked against a kernel result produced in the same turn.

CR-07: the model may phrase an answer, but it may not contribute a figure the
ledger did not return.  These tests pin the three failure modes on the
conversation path:

* a financial answer with no tool result is refused, never shown;
* a model that cannot bind tools is not used unbound;
* a figure absent from the tool results replaces the reply with a deterministic
  summary of what the tools actually said.
"""
from __future__ import annotations

import pytest

import code.accounts as store
from code import chat, service
from tests.support.fake_provider import FakeProvider


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


def test_financial_answer_without_a_tool_call_is_refused(db):
    account = _account()
    provider = FakeProvider(replies=["You can spend 12,345."])

    result = chat.ask(account, "how much can I spend?", provider=provider)

    assert result["verified"] is False
    assert result["reason"] == "unverified"
    assert result["tool_calls"] == []
    assert "12,345" not in result["reply"]
    assert result["reply"] == chat.UNVERIFIED_REPLY


def test_verify_reply_without_observations_refuses_any_prose():
    reply, verified = chat.verify_reply("You can spend 12345 today.", [])

    assert verified is False
    assert reply == chat.UNVERIFIED_REPLY


def test_bind_tools_failure_is_not_silently_unbound(db):
    account = _account()
    provider = FakeProvider(
        bind_raises=True, replies=["You can spend 12,345."]
    )

    with pytest.raises(chat.NoModel):
        chat.ask(account, "how much can I spend?", provider=provider)

    # The unbound model was never asked: an answer it gave could not be grounded.
    assert provider.calls == 0


def test_answer_figures_must_appear_in_tool_results(db):
    account = _account()
    position = chat.dispatch(account, "position")
    provider = FakeProvider(
        tool_batches=[[{"name": "position", "args": {}, "id": "call_1"}]],
        replies=["You can safely spend 9,999,999 today."],
    )

    result = chat.ask(account, "how much can I spend?", provider=provider)

    assert result["tool_calls"] == ["position"]
    assert result["verified"] is False
    assert result["reason"] == "unverified"
    assert "9,999,999" not in result["reply"]
    assert "9999999" not in result["reply"]
    # The fallback is rendered from the tool result, figure included.
    assert "max safe payment" in result["reply"]
    assert str(position["max_safe_payment"]) in result["reply"]


def test_a_balance_cannot_witness_a_safe_payment_claim():
    """A figure must be grounded in the field the sentence is about, not anywhere."""
    observations = [
        {
            "available_balance": "12000",
            "max_safe_payment": "10000",
            "minimum_balance": "2000",
        }
    ]

    reply, verified = chat.verify_reply("You can safely pay 12000 today.", observations)

    assert verified is False
    assert reply != "You can safely pay 12000 today."
    assert reply.startswith("From your ledger:")


def test_a_sign_flipped_figure_is_rejected():
    observations = [
        {
            "available_balance": "12000",
            "max_safe_payment": "10000",
            "minimum_balance": "2000",
        }
    ]

    reply, verified = chat.verify_reply("Your balance is -12000.", observations)

    assert verified is False
    assert reply != "Your balance is -12000."
    assert reply.startswith("From your ledger:")


def test_a_genuine_safe_payment_figure_stays_verified():
    observations = [
        {
            "available_balance": "12000",
            "max_safe_payment": "10000",
            "minimum_balance": "2000",
        }
    ]

    reply, verified = chat.verify_reply("You can safely pay 10000 today.", observations)

    assert verified is True
    assert reply == "You can safely pay 10000 today."


def test_grouped_and_trailing_zero_figures_normalise_to_the_observed_value():
    observations = [{"max_safe_payment": "10000"}]

    reply, verified = chat.verify_reply(
        "You can safely pay 10,000.0 today.", observations
    )

    assert verified is True
    assert reply == "You can safely pay 10,000.0 today."


def test_a_reply_that_cites_a_tool_figure_is_accepted_verbatim(db):
    account = _account()
    position = chat.dispatch(account, "position")
    reply = (
        f"Your ledger shows {position['max_safe_payment']} is the largest safe "
        "payment today."
    )
    provider = FakeProvider(
        tool_batches=[[{"name": "position", "args": {}, "id": "call_1"}]],
        replies=[reply],
    )

    result = chat.ask(account, "how much can I spend?", provider=provider)

    assert result["verified"] is True
    assert result["reason"] == "answered"
    assert result["reply"] == reply
    assert result["tool_calls"] == ["position"]


def test_a_general_answer_without_a_kernel_call_is_not_stonewalled():
    """Only a money claim needs a witness; a greeting is allowed through."""
    reply, verified = chat.verify_reply("Hello! Ask me about a purchase.", [])
    assert verified is True
    assert reply == "Hello! Ask me about a purchase."


def test_general_prose_with_a_number_and_no_money_claim_is_allowed():
    reply, verified = chat.verify_reply("There are 12 months in a year.", [])
    assert verified is True
    assert reply == "There are 12 months in a year."


def test_a_money_claim_without_a_kernel_call_is_still_refused():
    reply, verified = chat.verify_reply("You can safely pay 12,000 today.", [])
    assert verified is False
    assert reply == chat.UNVERIFIED_REPLY
