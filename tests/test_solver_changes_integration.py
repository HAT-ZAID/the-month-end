"""Task 3 RED/GREEN integration tests for changed solver plans."""
from decimal import Decimal

import pandas as pd

import code.solver as solver_mod
from code.solver import solve_request


def _profile(methods="full_payment", **extra):
    profile = {
        "home_currency": "USD",
        "current_available_balance": "10000",
        "minimum_balance_to_keep": "2000",
        "payment_methods_user_will_consider": methods,
        "expense_categories_user_is_willing_to_stop": "dining",
        "expense_categories_user_is_willing_to_reduce": "",
        "expense_categories_to_protect": "rent",
        "max_installment_months": "12",
    }
    profile.update(extra)
    return profile


def _empty_options():
    return pd.DataFrame(columns=[
        "payment_option_id", "request_id", "payment_method", "payment_amount",
        "number_of_payments", "first_payment_date", "payment_frequency_days",
        "financing_fee", "total_payable_amount",
    ])


def _installment_options():
    return pd.DataFrame([{
        "payment_option_id": "opt_inst",
        "request_id": "r1",
        "payment_method": "installments",
        "payment_amount": "2500",
        "number_of_payments": 2,
        "first_payment_date": "2026-01-02",
        "payment_frequency_days": "30",
        "financing_fee": "0",
        "total_payable_amount": "5000",
    }])


def _change_event():
    return {
        "event_id": "event_change",
        "user_id": "u1",
        "event_type": "expense",
        "category": "dining",
        "direction": "debit",
        "amount": "4000",
        "currency": "USD",
        "event_date": "2026-01-01",
        "settlement_date": "2026-01-01",
        "status": "scheduled",
        "flexibility": "stoppable",
        "minimum_allowed_amount": "",
    }


def _oracle_sequence(monkeypatch, results):
    calls = []

    def fake(*args, **kwargs):
        calls.append(args[4])
        return results[len(calls) - 1]

    monkeypatch.setattr(solver_mod, "solve_safe_amount", fake)
    monkeypatch.setattr(solver_mod, "search_changes", lambda *args, **kwargs: ["stop:event_change"])
    return calls


def test_permitted_change_unlocks_partial_payment_and_preserves_original_earliest(monkeypatch):
    ledger = [Decimal("10000")] * 91
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("1000"), "earliest_date": "2026-01-20", "ledger": ledger},
        {"safe_amount": Decimal("3000"), "earliest_date": "2026-01-05", "ledger": ledger},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("partial_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", True,
    )
    assert result["recommended_payment_method"] == "partial_payment"
    assert result["spending_changes_needed"] == "stop:event_change"
    assert result["payment_plan"] == "2026-01-01:1000|2026-01-05:4000"
    assert result["earliest_date_for_full_payment"] == "2026-01-20"


def test_changed_ledger_can_make_installments_safe(monkeypatch):
    unsafe_ledger = [Decimal("1000")] * 91
    safe_ledger = [Decimal("10000")] * 91
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("0"), "earliest_date": None, "ledger": unsafe_ledger},
        {"safe_amount": Decimal("0"), "earliest_date": None, "ledger": safe_ledger},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("installments"),
        [_change_event()], [], _installment_options(), None, "2026-02-15", False,
    )
    assert result["recommended_payment_method"] == "installments"
    assert result["spending_changes_needed"] == "stop:event_change"


def test_changed_ledger_can_make_wait_meet_deadline(monkeypatch):
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("0"), "earliest_date": "2026-02-01", "ledger": []},
        {"safe_amount": Decimal("0"), "earliest_date": "2026-01-05", "ledger": []},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", False,
    )
    assert result["recommended_payment_method"] == "wait"
    assert result["affordability_status"] == "affordable_later"
    assert result["spending_changes_needed"] == "stop:event_change"
    assert result["earliest_date_for_full_payment"] == "2026-02-01"


def test_no_change_plan_is_preferred_over_an_unneeded_change(monkeypatch):
    calls = _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("5000"), "earliest_date": "2026-01-01", "ledger": []},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", False,
    )
    assert result["recommended_payment_method"] == "full_payment"
    assert result["spending_changes_needed"] == "none"
    assert len(calls) == 1
