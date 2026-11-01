"""Task 4 integration tests: spending changes as first-class plan candidates."""
from decimal import Decimal

import pandas as pd

import code.solver as solver_mod
from code.options import get_valid_options
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


def _change_set():
    return [[{
        "event_id": "event_change",
        "action": "stop",
        "value": Decimal("0"),
        "category": "dining",
        "savings": Decimal("4000"),
    }]]


def _oracle_sequence(monkeypatch, results):
    """Stub the oracle in call order; count calls and feed a single change set."""
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(solver_mod, "solve_safe_amount", fake)
    monkeypatch.setattr(solver_mod, "build_change_sets", lambda *a, **k: _change_set())
    return calls


def test_changed_full_payment_wins_and_preserves_the_base_amount_and_earliest(monkeypatch):
    ledger = [Decimal("10000")] * 91
    changed_ledger = [Decimal("12000")] * 91
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("1000"), "earliest_date": "2026-01-20", "ledger": ledger},
        {"safe_amount": Decimal("5000"), "earliest_date": "2026-01-05", "ledger": changed_ledger},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", False,
    )
    assert result["recommended_payment_method"] == "full_payment"
    assert result["affordability_status"] == "affordable_with_plan"
    assert result["spending_changes_needed"] == "stop:event_change"
    assert result["payment_plan"] == "2026-01-01:5000"
    # Output contract: both base (no-optional-change) oracle fields survive.
    assert result["amount_safe_to_pay"] == "1000"
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
    assert result["payment_plan"] == "2026-01-02:2500|2026-02-01:2500"


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
    assert result["payment_plan"] == "2026-01-05:5000"
    # A wait plan that only works after the change must report that plan's date:
    # an empty date next to "wait" would contradict the recommendation.
    assert result["earliest_date_for_full_payment"] == "2026-01-05"
    # The amount stays the base (pre-change) value by contract.
    assert result["amount_safe_to_pay"] == "0"


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
    assert len(calls) == 1, "a feasible no-change plan makes the change search provably moot"


def test_late_everything_is_not_affordable_and_emits_no_change(monkeypatch):
    ledger = [Decimal("10000")] * 91
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("0"), "earliest_date": "2026-02-01", "ledger": ledger},
        {"safe_amount": Decimal("0"), "earliest_date": "2026-02-01", "ledger": ledger},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", False,
    )
    assert result["affordability_status"] == "not_affordable"
    assert result["recommended_payment_method"] == "not_recommended"
    assert result["payment_plan"] == "none"
    assert result["spending_changes_needed"] == "none"


def test_partial_candidate_stays_base_shaped_while_installments_use_the_changed_ledger():
    """Ruling: the output contract wins over "leg 2 on the changed earliest".

    `amount_safe_to_pay` and `earliest_date_for_full_payment` come from the base
    oracle, and a partial plan must be exactly those two fields, so a changed
    partial plan cannot diverge from its no-change twin.  Changed installments
    are still certified against the changed ledger.
    """
    base = {"safe": Decimal("1000"), "earliest": "2026-01-20", "ledger": [Decimal("4000")] * 91}
    changed = {"safe": Decimal("3000"), "earliest": "2026-01-05",
               "ledger": [Decimal("10000")] * 91, "changes": ["stop:event_change"], "suffix": "_chg0"}
    profile = _profile("partial_payment|installments")
    ctx = {
        "request_date": "2026-01-01",
        "requested_amount": Decimal("5000"),
        "profile": profile,
        "will": {"partial_payment", "installments"},
        "allows_partial": True,
        "installment_options": get_valid_options("r1", profile, _installment_options()),
        "strict_installments": True,
    }
    candidates = solver_mod._build_candidates(dict(base, changes=[], suffix=""), base, ctx)
    changed_candidates = solver_mod._build_candidates(dict(changed), base, ctx)
    partial = [c for c in changed_candidates if c["payment_method"] == "partial_payment"]
    assert partial, changed_candidates
    assert partial[0]["payment_plan"] == "2026-01-01:1000|2026-01-20:4000"
    assert partial[0]["completion_date"] == "2026-01-20"
    assert Decimal(partial[0]["payment_plan"].split("|")[1].split(":")[1]) + Decimal("1000") == Decimal("5000")
    # The base ledger is unsafe for the 2500+2500 legs (floor 2000), so only the
    # changed ledger can certify installments.
    assert [c for c in candidates if c["payment_method"] == "installments"] == []
    assert [c["payment_method"] for c in changed_candidates].count("installments") == 1


def test_solver_uses_the_single_ranking_authority(monkeypatch):
    real_rank = solver_mod.rank_options
    seen = {}

    def spy(options, desired_completion_date=None):
        seen.setdefault("ids", set()).update(o["option_id"] for o in options)
        seen["deadline"] = desired_completion_date
        return real_rank(options, desired_completion_date)

    monkeypatch.setattr(solver_mod, "rank_options", spy)
    ledger = [Decimal("10000")] * 91
    changed_ledger = [Decimal("12000")] * 91
    _oracle_sequence(monkeypatch, [
        {"safe_amount": Decimal("1000"), "earliest_date": "2026-01-20", "ledger": ledger},
        {"safe_amount": Decimal("5000"), "earliest_date": "2026-01-05", "ledger": changed_ledger},
    ])
    result = solve_request(
        "r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
        [_change_event()], [], _empty_options(), None, "2026-01-10", False,
    )
    assert result["spending_changes_needed"] == "stop:event_change"
    assert any("_chg" in option_id or "_changed" in option_id for option_id in seen["ids"])
    assert seen["deadline"] == "2026-01-10"
    # There is no second inline ranker left in the solver.
    import inspect
    assert "spec_key" not in inspect.getsource(solver_mod)
