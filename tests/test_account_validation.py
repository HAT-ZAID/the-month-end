"""The account validator: no save may hand the kernel a fact it cannot trust.

``code.account.validate_account`` is the single definition of a storable
account, and ``code.accounts.save`` must refuse *before* it writes a row.  Each
test below pins one rejection the review found -- an unknown direction, a
non-finite or negative amount, an unreadable date, a currency outside the
contract's ISO set, and a duplicated event id.

A blank value is *unknown*, not malformed: onboarding that skips a balance still
saves, and the decision path names the missing fact rather than computing
against a zero.  So the validator is only allowed to reject a value that is
present and wrong.
"""
from __future__ import annotations

import pytest

import code.accounts as store
from code import account as acct


def _account(name: str = "ana") -> dict:
    """One well-formed account: a profile with both figures and one bill."""
    account = acct.blank_account(user_id=name, home_currency="ZAR")
    acct.set_profile(
        account,
        current_available_balance="12000",
        minimum_balance_to_keep="2000",
    )
    acct.add_event(
        account,
        event_type="expense",
        description="rent",
        category="rent",
        direction="debit",
        amount="8000",
        currency="ZAR",
        event_date="2026-11-03",
        settlement_date="2026-11-03",
        status="scheduled",
        flexibility="fixed",
    )
    return account


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the store in a throwaway SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


# ------------------------------------------------------------------ the validator


def test_a_well_formed_account_has_no_problems():
    assert acct.validate_account(_account()) == []


def test_a_blank_answer_stays_unknown_not_invalid():
    """Skipping onboarding is allowed; the decision path names the gap later."""
    assert acct.validate_account(acct.blank_account(user_id="ana")) == []


def test_an_unknown_direction_is_refused():
    account = _account()
    account["events"][0]["direction"] = "sideways"
    errors = acct.validate_account(account)
    assert any("direction" in message and "sideways" in message for message in errors)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "not money"])
def test_a_non_finite_amount_is_refused(value):
    account = _account()
    account["events"][0]["amount"] = value
    errors = acct.validate_account(account)
    assert any("amount" in message and "finite" in message for message in errors)


def test_a_negative_amount_is_refused():
    account = _account()
    account["events"][0]["amount"] = "-1"
    errors = acct.validate_account(account)
    assert any("amount" in message and "negative" in message for message in errors)


def test_a_negative_profile_balance_is_refused():
    account = _account()
    account["profile"]["current_available_balance"] = "-1"
    errors = acct.validate_account(account)
    assert any("balance" in message and "negative" in message for message in errors)


def test_an_invalid_iso_date_is_refused():
    account = _account()
    account["events"][0]["event_date"] = "03/11/2026"
    errors = acct.validate_account(account)
    assert any("date" in message.lower() and "ISO" in message for message in errors)


def test_a_currency_outside_the_iso_codes_is_refused():
    account = _account()
    account["events"][0]["currency"] = "GBP"
    errors = acct.validate_account(account)
    assert any("currency" in message and "GBP" in message for message in errors)


def test_a_home_currency_outside_the_iso_codes_is_refused():
    account = _account()
    account["profile"]["home_currency"] = "GBP"
    errors = acct.validate_account(account)
    assert any("home currency" in message and "GBP" in message for message in errors)


def test_duplicate_event_ids_are_refused():
    account = _account()
    account["events"].append(dict(account["events"][0]))
    errors = acct.validate_account(account)
    assert any("duplicate" in message and "event_1" in message for message in errors)


# ------------------------------------------------------------------ the save gate


def test_a_valid_account_saves_cleanly(db):
    assert store.save(_account()) == "ana"
    stored = store.load("ana")
    assert stored is not None
    assert stored["profile"]["home_currency"] == "ZAR"
    assert [event["amount"] for event in stored["events"]] == ["8000"]


def test_save_refuses_before_it_writes(db):
    account = _account()
    account["events"][0]["direction"] = "sideways"
    with pytest.raises(ValueError) as caught:
        store.save(account)
    assert "direction" in str(caught.value)
    assert store.load("ana") is None, "a refused account was written anyway"


def test_save_joins_every_problem_into_one_error(db):
    account = _account()
    account["events"][0]["amount"] = "-5"
    account["events"][0]["currency"] = "GBP"
    with pytest.raises(ValueError) as caught:
        store.save(account)
    message = str(caught.value)
    assert "amount" in message and "currency" in message
    assert "; " in message, "the errors must be joined with '; '"


# --------------------------------------------------------- the JSON save gate
#
# ``code.account.save_account`` is the other persistence path: one JSON file,
# used by the TUI and the task agent.  It writes the same contract a store save
# does, so it must ask the same validator first.  An account the validator
# refuses may not reach disk through either door.


def test_the_json_save_refuses_an_unknown_direction(tmp_path):
    account = _account()
    account["events"][0]["direction"] = "sideways"
    target = tmp_path / "account.json"
    with pytest.raises(ValueError) as caught:
        acct.save_account(account, path=target)
    assert "direction" in str(caught.value)
    assert not target.exists(), "a refused account was written anyway"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_the_json_save_refuses_a_non_finite_amount(tmp_path, value):
    account = _account()
    account["events"][0]["amount"] = value
    target = tmp_path / "account.json"
    with pytest.raises(ValueError) as caught:
        acct.save_account(account, path=target)
    message = str(caught.value)
    assert "amount" in message and "finite" in message
    assert not target.exists(), "a refused account was written anyway"


def test_the_json_save_joins_every_problem_into_one_error(tmp_path):
    account = _account()
    account["events"][0]["amount"] = "-5"
    account["events"][0]["currency"] = "GBP"
    target = tmp_path / "account.json"
    with pytest.raises(ValueError) as caught:
        acct.save_account(account, path=target)
    message = str(caught.value)
    assert "amount" in message and "currency" in message
    assert "; " in message, "the errors must be joined with '; '"
    assert not target.exists()


def test_a_refused_json_save_leaves_the_previous_file_untouched(tmp_path):
    """``open('w')`` truncates, so the validator has to run before the write."""
    target = tmp_path / "account.json"
    acct.save_account(_account(), path=target)
    before = target.read_bytes()

    account = _account()
    account["events"][0]["amount"] = "Infinity"
    with pytest.raises(ValueError):
        acct.save_account(account, path=target)

    assert target.read_bytes() == before


def test_a_valid_account_still_saves_and_reloads_through_the_json_path(tmp_path):
    target = tmp_path / "nested" / "account.json"
    assert acct.save_account(_account(), path=target) == target
    reloaded = acct.load_account(path=target)
    assert reloaded is not None
    assert reloaded["version"] == acct.ACCOUNT_VERSION
    assert reloaded["profile"]["home_currency"] == "ZAR"
    assert [event["amount"] for event in reloaded["events"]] == ["8000"]


def test_an_unanswered_account_still_saves_through_the_json_path(tmp_path):
    """A blank value is unknown, not malformed: onboarding must still persist."""
    target = tmp_path / "account.json"
    acct.save_account(acct.blank_account(user_id="ana"), path=target)
    reloaded = acct.load_account(path=target)
    assert reloaded["profile"]["user_id"] == "ana"
    assert reloaded["events"] == []
