"""A fact amount must be a finite number before it can be stored.

``Decimal("Infinity")`` parses, so the old service helper accepted it and wrote
an unpayable amount to the account; the account then failed ``save_user``
validation outside the UI handler's ``try``.  ``"nan"`` parsed too, and the
following comparison raised ``decimal.InvalidOperation`` instead of the engine's
own refusal.  Both are refused before anything is recorded.
"""
from __future__ import annotations

import copy

import pytest

import code.accounts as store
from code import service


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


def _account() -> dict:
    return service.create_user("ana", currency="USD", balance="10000", floor="2000")


def _add_bill(account, amount):
    return service.add_fact(
        account,
        "bill",
        amount,
        when="2026-01-01",
        description="rent",
        category="rent",
    )


@pytest.mark.parametrize("amount", ["Infinity", "-Infinity"])
def test_an_infinite_amount_is_refused_and_leaves_the_account_unchanged(db, amount):
    account = _account()
    before = copy.deepcopy(account)

    with pytest.raises(service.EngineError) as caught:
        _add_bill(account, amount)

    assert "amount" in str(caught.value).lower()
    assert account == before, "a refused fact was recorded anyway"


def test_nan_is_an_engine_error_not_a_decimal_invalid_operation(db):
    account = _account()
    before = copy.deepcopy(account)

    with pytest.raises(service.EngineError) as caught:
        _add_bill(account, "nan")

    assert "amount" in str(caught.value).lower()
    assert account == before, "a refused fact was recorded anyway"


def test_a_finite_amount_is_still_recorded(db):
    account = _account()

    event = _add_bill(account, "800")

    assert event["amount"] == "800"
    assert event["category"] == "rent"
