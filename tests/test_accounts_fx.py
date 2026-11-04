"""Foreign-exchange rates are stored with the account and reach the kernel.

A person whose bill is denominated in another currency is only decidable if the
rate they recorded survives the trip through SQLite.  Before this contract the
store dropped ``fx`` on save, so a reloaded account looked like an account with
no rates at all: every foreign-currency row failed closed as ``FX_UNKNOWN`` and
the person was told nothing was safe.

Three things are pinned here:

* one rate row round-trips through the store unchanged;
* a version-1 database gains the table without losing the rows it already has;
* a decision over a foreign-currency event uses the loaded rate, and the same
  decision without it still fails closed -- the conversion is what changed the
  verdict, not a relaxed check.

Every expected figure is a plain literal from the inputs (the rate times the
amount); the test computes no balance of its own.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import code.accounts as store
from code import account as acct
from code import service

#: One settled USD->ZAR rate dated to the bill's settlement date, so
#: ``code.fx.resolve_rate`` finds an exact match rather than a prior snapshot
#: (which is deliberately flagged ``FX_UNKNOWN``).
FX_ROW = {
    "rate_date": "2026-11-05",
    "from_currency": "USD",
    "to_currency": "ZAR",
    "rate": "18.5",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the store in a throwaway SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


def _account(name: str = "ana") -> dict:
    account = acct.blank_account(user_id=name, home_currency="ZAR")
    acct.set_profile(
        account,
        current_available_balance="12000",
        minimum_balance_to_keep="2000",
        payment_methods_user_will_consider="full_payment",
    )
    return account


def _foreign_account() -> dict:
    """A ZAR account with a 100 USD scheduled bill and a 900 request."""
    account = _account()
    acct.add_event(
        account,
        event_type="expense",
        description="usd invoice",
        category="household",
        direction="debit",
        amount="100",
        currency="USD",
        event_date="2026-11-05",
        settlement_date="2026-11-05",
        status="scheduled",
        flexibility="fixed",
    )
    acct.add_request(
        account,
        request_date="2026-11-01",
        request_type="purchase",
        requested_amount="900",
        desired_completion_date="2026-11-30",
        allows_partial_payment="False",
        request_text="headphones",
    )
    return account


# ------------------------------------------------------------------ round-trip


def test_a_rate_row_survives_a_save_and_reload(db):
    account = _account()
    account["fx"] = [dict(FX_ROW)]
    assert acct.validate_account(account) == [], "a well-formed rate row was refused"

    store.save(account)
    reloaded = store.load("ana")

    assert reloaded is not None
    assert reloaded["fx"] == [FX_ROW], "the rate row did not survive the store"


def test_a_well_formed_rate_row_is_persisted_with_the_schema_that_knows_it(db):
    assert store.SCHEMA_VERSION == 2
    store.save({**_account(), "fx": [dict(FX_ROW)]})
    with sqlite3.connect(str(db)) as connection:
        version = connection.execute(
            "SELECT value FROM meta WHERE name = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == "2"
    assert "fx" in tables


# ------------------------------------------------------------------ the validator


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("rate", "NaN", "finite"),
        ("rate", "-1", "greater than zero"),
        ("from_currency", "GBP", "currency"),
        ("rate_date", "05/11/2026", "ISO date"),
    ],
)
def test_a_malformed_rate_row_is_refused(field, value, expected):
    account = _account()
    account["fx"] = [{**FX_ROW, field: value}]
    errors = acct.validate_account(account)
    assert any(expected in message for message in errors), errors


def test_an_incomplete_rate_row_is_refused():
    account = _account()
    account["fx"] = [{**FX_ROW, "rate": ""}]
    errors = acct.validate_account(account)
    assert any("rate" in message for message in errors), errors


# ------------------------------------------------------------------ the decision


def test_a_foreign_currency_event_decides_from_the_stored_rate(db):
    account = _foreign_account()
    account["fx"] = [dict(FX_ROW)]
    store.save(account)
    reloaded = store.load("ana")

    decision = service.decide(reloaded, "request_1")

    assert decision["affordability_status"] == "affordable_now"
    assert decision["recommended_payment_method"] == "full_payment"
    assert "FX_UNKNOWN" not in json.dumps(decision, default=str), (
        "the stored rate did not reach the kernel"
    )


def test_without_the_rate_the_same_event_still_fails_closed(db):
    """The negative control: dropping the rate must change the verdict."""
    account = _foreign_account()
    store.save(account)
    reloaded = store.load("ana")

    decision = service.decide(reloaded, "request_1")

    assert decision["affordability_status"] == "not_affordable"
    assert "FX_UNKNOWN" in decision["decision_explanation"]


def test_the_position_uses_the_stored_rate_too(db):
    account = _foreign_account()
    account["fx"] = [dict(FX_ROW)]
    store.save(account)
    reloaded = store.load("ana")

    position = service.position(reloaded, as_of="2026-11-01")

    assert position["fail_closed"] is False
    assert position["committed"] == "1850.0"  # 100 USD at 18.5
    assert position["max_safe_payment"] == "8150.0"  # 12000 - 2000 - 1850


# ------------------------------------------------------------------ migration


def _v1_database(path) -> None:
    """The schema the store wrote before rates were persisted."""
    columns = lambda fields: ", ".join(f"{name} TEXT" for name in fields)
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            f"""
            CREATE TABLE meta (
                name TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE profiles (
                user_name TEXT PRIMARY KEY,
                {columns(acct.PROFILE_FIELDS)},
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE events (
                user_name TEXT NOT NULL,
                {columns(acct.EVENT_FIELDS)},
                seq INTEGER
            );
            CREATE TABLE requests (
                user_name TEXT NOT NULL,
                {columns(acct.REQUEST_FIELDS)},
                seq INTEGER
            );
            CREATE TABLE payment_options (
                user_name TEXT NOT NULL,
                {columns(acct.OPTION_FIELDS)},
                seq INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO meta (name, value) VALUES ('schema_version', '1')"
        )
        connection.execute(
            f"INSERT INTO profiles (user_name, {', '.join(acct.PROFILE_FIELDS)}, "
            f"created_at, updated_at) VALUES "
            f"({', '.join(['?'] * (len(acct.PROFILE_FIELDS) + 3))})",
            [
                "ana",
                *[
                    "ZAR" if name == "home_currency"
                    else "12000" if name == "current_available_balance"
                    else "2000" if name == "minimum_balance_to_keep"
                    else "ana" if name == "user_id"
                    else ""
                    for name in acct.PROFILE_FIELDS
                ],
                "2026-11-01T00:00:00+00:00",
                "2026-11-01T00:00:00+00:00",
            ],
        )
        connection.execute(
            f"INSERT INTO events (user_name, {', '.join(acct.EVENT_FIELDS)}, seq) "
            f"VALUES ({', '.join(['?'] * (len(acct.EVENT_FIELDS) + 2))})",
            [
                "ana",
                *[
                    {
                        "event_id": "event_1",
                        "user_id": "ana",
                        "event_type": "income",
                        "description": "salary",
                        "category": "salary",
                        "direction": "credit",
                        "amount": "15000",
                        "currency": "ZAR",
                        "event_date": "2026-10-25",
                        "settlement_date": "2026-10-25",
                        "status": "settled",
                        "flexibility": "fixed",
                    }.get(name, "")
                    for name in acct.EVENT_FIELDS
                ],
                0,
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_a_v1_database_gains_the_fx_table_without_losing_events(db):
    _v1_database(db)

    reloaded = store.load("ana")

    assert reloaded is not None
    assert [event["event_id"] for event in reloaded["events"]] == ["event_1"]
    assert reloaded["profile"]["current_available_balance"] == "12000"
    assert reloaded["fx"] == []

    with sqlite3.connect(str(db)) as connection:
        version = connection.execute(
            "SELECT value FROM meta WHERE name = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == "2", "the migration did not record the new version"
    assert "fx" in tables, "the migration did not create the rate table"

    # The migrated database accepts rates, and saving does not disturb the
    # event it already held.
    reloaded["fx"] = [dict(FX_ROW)]
    store.save(reloaded)
    stored = store.load("ana")
    assert stored["fx"] == [FX_ROW]
    assert [event["event_id"] for event in stored["events"]] == ["event_1"]
