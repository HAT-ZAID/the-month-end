"""A blank onboarding answer stays unknown (CR-14).

The defect this file pins: onboarding turned an unanswered balance and floor
into ``"0"`` before they were stored, and :func:`code.service.decide` then ran
the kernel against a balance nobody gave it.  A person who skipped "Balance
now" was told, with a straight face, that a purchase was "not affordable" --
a verdict computed from a zero the app invented.

The rule these tests pin:

* a blank balance or reserve is stored as ``""`` (unknown), never ``"0"``;
* a decision needs both facts, so ``decide`` refuses with a message naming the
  field that is missing rather than computing against zero;
* a real balance still decides exactly as it did before;
* the app shows that message and offers a field to fill it in.

The expected figures in the decision tests come from the kernel's own contract
(a plain full payment of an amount smaller than the headroom), not from an
arithmetic repeated here.
"""
from __future__ import annotations

import asyncio

import pytest

import code.accounts as store
from code import account as acct
from code import service


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Keep the account store in a temporary SQLite file, never the real one."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(store, "default_db_path", lambda repo_root=None: path)
    return path


@pytest.fixture(autouse=True)
def _no_model_probe(monkeypatch):
    """Tests never reach a model server: the menu is exercised with discovery off."""
    monkeypatch.setenv("BUY_WAIT_MODEL_DISCOVERY", "0")


def _account(name: str = "ana", *, balance: str = "", floor: str = "") -> dict:
    """One person with exactly the profile facts the test names."""
    account = store.create(name, home_currency="ZAR")
    service.update_profile(account, balance=balance, floor=floor)
    return account


def _request(account: dict) -> dict:
    """A recorded purchase, without going through ``buy`` (which decides)."""
    return acct.add_request(
        account,
        request_date="2026-11-01",
        request_type="purchase",
        requested_amount="900",
        desired_completion_date="2026-11-20",
        allows_partial_payment="True",
        request_text="headphones",
    )


def _profile(account: dict, field: str) -> str:
    return str((account.get("profile") or {}).get(field) or "")


# ------------------------------------------------- blank is unknown, not zero


def test_update_profile_keeps_a_blank_balance_and_floor_blank(db):
    """The store records "no answer" as unknown, never as a zero balance."""
    account = _account(balance="", floor="")
    assert _profile(account, "current_available_balance") == ""
    assert _profile(account, "minimum_balance_to_keep") == ""
    assert _profile(account, "current_available_balance") != "0"


def test_a_present_balance_is_still_parsed_as_before(db):
    """Only blanks change meaning: a typed amount keeps its existing form."""
    account = _account(balance="12000", floor="2000")
    assert _profile(account, "current_available_balance") == "12000"
    assert _profile(account, "minimum_balance_to_keep") == "2000"


def test_a_blank_field_beside_a_present_one_stays_blank(db):
    """Answering one question must not answer the other with zero."""
    account = _account(balance="12000", floor="")
    assert _profile(account, "current_available_balance") == "12000"
    assert _profile(account, "minimum_balance_to_keep") == ""


# ------------------------------------------------------- the decision gate


def test_a_blank_balance_cannot_produce_a_decision(db):
    """(a) No decision from an unanswered balance: the refusal names the fact."""
    account = _account(balance="", floor="2000")
    request = _request(account)

    with pytest.raises(service.EngineError) as caught:
        service.decide(account, request["request_id"])

    message = str(caught.value)
    assert "balance is not set" in message, message
    assert service.MISSING_FACTS, "the missing-fact vocabulary must be importable"


def test_a_blank_floor_cannot_produce_a_decision(db):
    """The reserve is the other fact the kernel cannot invent."""
    account = _account(balance="12000", floor="")
    request = _request(account)

    with pytest.raises(service.EngineError) as caught:
        service.decide(account, request["request_id"])

    message = str(caught.value)
    assert "minimum balance to keep is not set" in message, message


def test_both_missing_facts_are_named_in_one_refusal(db):
    account = _account(balance="", floor="")
    request = _request(account)

    with pytest.raises(service.EngineError) as caught:
        service.decide(account, request["request_id"])

    message = str(caught.value)
    assert "balance is not set" in message, message
    assert "minimum balance to keep is not set" in message, message


def test_a_refused_purchase_records_nothing(db):
    """A refusal must not leave a phantom request on the account."""
    account = _account(balance="", floor="2000")

    with pytest.raises(service.EngineError):
        service.buy(account, "900", on="2026-11-01", by="2026-11-20")

    assert account["requests"] == [], "a refused purchase was recorded anyway"


def test_a_real_balance_still_decides(db):
    """(b) The gate refuses only the unknown; an answered account still works."""
    account = _account(balance="12000", floor="2000")
    service.add_fact(account, "bill", "8000", when="2026-11-03",
                     description="rent", category="rent")

    decision = service.buy(account, "900", on="2026-11-01", by="2026-11-20",
                           text="headphones")

    assert decision["affordability_status"] == "affordable_now"
    assert decision["recommended_payment_method"] == "full_payment"
    assert decision["amount_safe_to_pay"] == "900"
    assert len(account["requests"]) == 1


def test_the_missing_fact_vocabulary_has_one_definition():
    """CR-14: the kernel and the service must name the same two facts, once.

    Two tuples kept in step by comments drift silently: rename a field in one
    module and the other keeps refusing with a stale word.  The kernel is the
    lower layer, so it owns the vocabulary and the service imports it.
    """
    from code import solver

    assert service.MISSING_FACTS is solver.MISSING_FACTS
    assert solver.MISSING_FACTS == (
        ("current_available_balance", "balance"),
        ("minimum_balance_to_keep", "minimum balance to keep"),
    )


# --------------------------------------------------------------- onboarding


def test_onboarding_leaves_a_blank_balance_and_floor_blank(db):
    """(c) Skipping the questions stores unknown, not "0"."""
    from tools.app import BuyOrWait, Onboarding

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, Onboarding)
            onboard = app.screen
            onboard.query_one("#ob-name").value = "sam"
            onboard.query_one("#ob-currency").value = "ZAR"
            # Balance and floor deliberately left empty.
            onboard.query_one("#ob-start").press()
            await pilot.pause()
            assert app.account is not None
            assert _profile(app.account, "current_available_balance") == ""
            assert _profile(app.account, "minimum_balance_to_keep") == ""
        return True

    assert asyncio.run(drive()) is True

    stored = service.load_user("sam")
    assert stored is not None
    assert _profile(stored, "current_available_balance") == ""
    assert _profile(stored, "minimum_balance_to_keep") == ""


def test_the_app_names_the_missing_fact_and_offers_to_fill_it(db):
    """The screen says which fact is missing and gives a field for it."""
    from tools.app import BuyOrWait

    account = _account(balance="", floor="2000")
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            shown = str(app.query_one("#pos-missing").render())
            assert "balance is not set" in shown, shown
            # The offer to fill it: an input for the missing fact and a save.
            balance_field = app.query_one("#profile-balance")
            save_button = app.query_one("#profile-save")
            assert balance_field is not None and save_button is not None

            balance_field.value = "12000"
            save_button.press()
            for _ in range(20):  # the press is a posted message: wait for it
                await pilot.pause()
                if app.account is not None and _profile(
                    app.account, "current_available_balance"
                ) == "12000":
                    break

            assert app.account is not None
            assert _profile(app.account, "current_available_balance") == "12000"
            assert "balance is not set" not in str(
                app.query_one("#pos-missing").render()
            )
            assert service.missing_facts(app.account) == []
        return True

    assert asyncio.run(drive()) is True

    reloaded = service.load_user("ana")
    assert reloaded is not None
    assert _profile(reloaded, "current_available_balance") == "12000"


def test_the_purchase_tab_surfaces_the_same_refusal(db):
    """A purchase with a blank balance shows the refusal and records nothing."""
    from tools.app import BuyOrWait

    account = _account(balance="", floor="2000")
    store.save(account)

    async def drive():
        app = BuyOrWait()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#tabs").active = "tab-buy"
            await pilot.pause()
            await pilot.pause()
            app.query_one("#buy-amount").value = "500"
            app.query_one("#buy-go").press()
            for _ in range(20):  # the press is a posted message: wait for it
                await pilot.pause()
                if str(app.query_one("#buy-explain").render()).strip():
                    break

            shown = str(app.query_one("#buy-explain").render())
            assert "balance is not set" in shown, shown
            assert "Position" in shown, "the refusal must route the person to the field"
            assert app.account is not None
            assert app.account["requests"] == [], "a refused purchase was recorded"
        return True

    assert asyncio.run(drive()) is True


# --------------------------------------------------- the position contract


#: The same two-event account used by the kernel position test in
#: ``tests/review/test_general_use.py``: a settled salary and a scheduled rent.
_POSITION_EVENTS = [
    {
        "event_id": "event_1", "user_id": "ana", "event_type": "income",
        "description": "salary", "category": "income", "direction": "credit",
        "amount": "15000", "currency": "ZAR", "event_date": "2026-10-25",
        "settlement_date": "2026-10-25", "status": "settled",
        "linked_event_id": "", "flexibility": "fixed", "minimum_allowed_amount": "",
    },
    {
        "event_id": "event_2", "user_id": "ana", "event_type": "expense",
        "description": "rent", "category": "rent", "direction": "debit",
        "amount": "8000", "currency": "ZAR", "event_date": "2026-11-03",
        "settlement_date": "2026-11-03", "status": "scheduled",
        "linked_event_id": "", "flexibility": "fixed", "minimum_allowed_amount": "",
    },
]


def _position_profile(**overrides) -> dict:
    profile = {
        "user_id": "ana",
        "home_currency": "ZAR",
        "current_available_balance": "12000",
        "minimum_balance_to_keep": "2000",
        "payment_methods_user_will_consider": "full_payment",
    }
    profile.update(overrides)
    return profile


def test_position_over_a_blank_balance_is_unknown_not_zero():
    """The kernel names the missing fact instead of valuing the balance as 0."""
    from code.solver import position as kernel_position

    result = kernel_position(
        "ana", "2026-11-01",
        _position_profile(current_available_balance=""), _POSITION_EVENTS, [],
    )

    assert result["unknown"] == ["balance is not set"]
    assert result["available_balance"] == ""
    assert result["minimum_balance"] == ""
    assert result["headroom"] == ""
    assert result["max_safe_payment"] == ""
    assert result["fail_closed"] is True
    assert str(result["fail_closed_reason"]).startswith("MISSING_FACTS")
    assert "balance is not set" in str(result["fail_closed_reason"])


def test_position_names_a_blank_floor():
    from code.solver import position as kernel_position

    result = kernel_position(
        "ana", "2026-11-01",
        _position_profile(minimum_balance_to_keep=""), _POSITION_EVENTS, [],
    )

    assert result["unknown"] == ["minimum balance to keep is not set"]
    assert result["minimum_balance"] == ""
    assert result["max_safe_payment"] == ""


def test_position_names_both_missing_facts():
    from code.solver import position as kernel_position

    result = kernel_position(
        "ana", "2026-11-01",
        _position_profile(current_available_balance="",
                          minimum_balance_to_keep=""),
        _POSITION_EVENTS, [],
    )

    assert result["unknown"] == [
        "balance is not set",
        "minimum balance to keep is not set",
    ]
    assert result["available_balance"] == ""
    assert result["max_safe_payment"] == ""


def test_position_with_a_real_balance_still_returns_numbers():
    """The guard refuses only the unknown; an answered profile still values."""
    from code.solver import position as kernel_position

    result = kernel_position(
        "ana", "2026-11-01", _position_profile(), _POSITION_EVENTS, [],
    )

    assert result["unknown"] == []
    assert result["fail_closed"] is False
    assert result["available_balance"] == "12000"
    assert result["minimum_balance"] == "2000"
    assert result["headroom"] == "10000"
    # Pinned by tests/review/test_general_use.py on the same two events.
    assert result["max_safe_payment"] == "2000.0"


def test_the_service_and_chat_position_paths_carry_the_unknown_marker(db):
    """CR-14 on the chat path: no zero-based figure reaches the model."""
    from code import chat

    account = _account(balance="", floor="2000")
    responses = (
        service.position(account, as_of="2026-11-01"),
        chat.dispatch(account, "position"),
    )
    for result in responses:
        assert result["unknown"] == ["balance is not set"]
        assert result["available_balance"] == ""
        assert result["max_safe_payment"] == ""
