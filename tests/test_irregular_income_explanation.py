"""A decision names irregular income as recorded but not projected.

Income recorded without ``recurrence="monthly"`` is stored as
``irregular_income`` (:func:`code.service.resolve_category`), and the forecast
deliberately never projects it: it is counted on its own date only.  Without a
sentence saying so, the decision silently ignores money the person recorded and
the explanation reads as if the forecast had considered it.

The sentence is fixed prose with no figure in it, so it can never be read as an
amount the ledger did not compute.
"""
from __future__ import annotations

import pytest

import code.accounts as store
from code import service

#: The sentence the decision must carry, and must not carry without the row.
IRREGULAR_INCOME_NOTE = "Irregular income is recorded but not projected."


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


def _account() -> dict:
    return service.create_user("ana", currency="ZAR", balance="12000", floor="2000")


def _decision(account: dict) -> dict:
    """A decision the kernel can make regardless of the extra income row."""
    return service.buy(
        account, "900", on="2026-11-01", by="2026-11-20", text="headphones"
    )


def test_a_decision_names_irregular_income_as_not_projected(db):
    account = _account()
    event = service.add_fact(
        account, "income", "3000", when="2026-10-01", description="sold a bike"
    )
    assert event["category"] == "irregular_income"

    decision = _decision(account)

    assert IRREGULAR_INCOME_NOTE in decision["decision_explanation"]


def test_a_decision_without_irregular_income_does_not_claim_it(db):
    account = _account()
    event = service.add_fact(
        account,
        "income",
        "3000",
        when="2026-10-01",
        description="monthly payroll",
        recurrence="monthly",
    )
    assert event["category"] == "salary"

    decision = _decision(account)

    assert IRREGULAR_INCOME_NOTE not in decision["decision_explanation"]
