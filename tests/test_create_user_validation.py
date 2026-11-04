"""A new person is only stored once their profile amounts are valid.

``service.create_user`` used to call ``store.create`` first, so an invalid
balance or floor raised from ``update_profile`` after the blank account had
already been written.  A new name was left behind as an empty account, and
``overwrite=True`` wiped an existing account before refusing the new values.
Every amount is parsed before the store is touched.
"""
from __future__ import annotations

import pytest

import code.accounts as store
from code import service


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


@pytest.mark.parametrize(
    ("balance", "floor"),
    [
        ("abc", "0"),
        ("0", "-5"),
        ("Infinity", "0"),
    ],
)
def test_an_invalid_profile_amount_never_stores_a_new_account(db, balance, floor):
    with pytest.raises(service.EngineError):
        service.create_user("ana", currency="USD", balance=balance, floor=floor)

    assert store.load("ana") is None, "a refused profile left a blank account behind"


def test_an_invalid_profile_amount_never_replaces_an_existing_account(db):
    service.create_user("ana", currency="USD", balance="10000", floor="2000")
    before = store.load("ana")

    with pytest.raises(service.EngineError):
        service.create_user(
            "ana", currency="USD", balance="abc", floor="2000", overwrite=True
        )

    assert store.load("ana") == before, "a refused overwrite erased the account first"


def test_a_valid_pair_is_created_and_stored(db):
    account = service.create_user("bea", currency="ZAR", balance="10000", floor="2000")

    stored = store.load("bea")
    assert stored is not None
    assert stored["profile"]["current_available_balance"] == "10000"
    assert stored["profile"]["minimum_balance_to_keep"] == "2000"
    assert account["profile"]["current_available_balance"] == "10000"
    assert account["profile"]["minimum_balance_to_keep"] == "2000"
