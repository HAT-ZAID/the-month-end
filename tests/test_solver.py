"""Direct unit tests for code/solver.py solve_request (oracle stubbed for determinism)."""
from decimal import Decimal

import pandas as pd

import code.solver as solver_mod
from code.solver import solve_request


def _profile(will):
    return {"home_currency": "USD", "current_available_balance": "10000",
            "minimum_balance_to_keep": "2000",
            "payment_methods_user_will_consider": will}


def _empty_options():
    return pd.DataFrame(columns=["payment_option_id", "request_id", "payment_method",
                                 "payment_amount", "number_of_payments", "first_payment_date",
                                 "payment_frequency_days", "financing_fee", "total_payable_amount"])


def _stub(monkeypatch, safe, earliest):
    def fake(*a, **k):
        return {"safe_amount": Decimal(str(safe)), "earliest_date": earliest, "ledger": []}
    monkeypatch.setattr(solver_mod, "solve_safe_amount", fake)


def test_full_path_affordable_now(monkeypatch):
    _stub(monkeypatch, "5000", "2026-01-01")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
                        [], [], _empty_options(), None, None, False)
    assert res["recommended_payment_method"] == "full_payment"
    assert res["affordability_status"] == "affordable_now"
    assert res["earliest_date_for_full_payment"] == "2026-01-01"


def test_partial_blocked_when_not_allowed(monkeypatch):
    _stub(monkeypatch, "2000", "2026-01-20")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("partial_payment"),
                        [], [], _empty_options(), None, "2026-03-01", False)
    assert res["recommended_payment_method"] != "partial_payment"


def test_partial_blocked_when_earliest_after_deadline(monkeypatch):
    _stub(monkeypatch, "2000", "2026-03-01")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("partial_payment"),
                        [], [], _empty_options(), None, "2026-01-15", True)
    assert res["recommended_payment_method"] != "partial_payment"


def test_partial_fail_closed_on_none_flag(monkeypatch):
    # Missing allows_partial_payment must default to ineligible (fail-closed).
    _stub(monkeypatch, "2000", "2026-01-20")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("partial_payment"),
                        [], [], _empty_options(), None, "2026-03-01", None)
    assert res["recommended_payment_method"] != "partial_payment"


def test_wait_not_when_earliest_equals_request_date(monkeypatch):
    # earliest == request_date is "now", not later: wait requires a strictly-future date.
    # (Unreachable via the real oracle when safe<req; stubbed to pin the solver branch.)
    _stub(monkeypatch, "2000", "2026-01-01")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
                        [], [], _empty_options(), None, "2026-03-01", False)
    assert res["recommended_payment_method"] != "wait"


def test_wait_not_when_earliest_empty(monkeypatch):
    for empty in (None, ""):
        _stub(monkeypatch, "0", empty)
        res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
                            [], [], _empty_options(), None, "2026-03-01", False)
        assert res["recommended_payment_method"] != "wait"


def test_wait_not_when_earliest_nan(monkeypatch):
    # Non-date earliest ('nan') must be treated as missing: no wait option.
    # str('nan') > str(req_date) is True lexicographically, so an explicit
    # date-validity check is required.
    for bad in ("nan", "NaN", "not-a-date", float("nan")):
        _stub(monkeypatch, "0", bad)
        res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("full_payment"),
                            [], [], _empty_options(), None, "2026-03-01", False)
        assert res["recommended_payment_method"] != "wait"


def test_meets_deadline_nan_empty_normalization():
    from code.solver import meets_deadline
    # Missing/NaN on either side = no constraint (True via date parse).
    for bad in ("nan", "NaN", "NAN", "", None, float("nan"), "None", "null", "   ", "n/a"):
        assert meets_deadline("2026-02-01", bad) is True, f"desired={bad!r}"
        assert meets_deadline(bad, "2026-03-01") is True, f"comp={bad!r}"
    # Normal ordering still enforced.
    assert meets_deadline("2026-01-20", "2026-03-01") is True
    assert meets_deadline("2026-04-01", "2026-03-01") is False


def test_meets_deadline_whitespace_desired_is_no_constraint(monkeypatch):
    # Desired "   " (whitespace) must not block partial; old string-compare returned False.
    _stub(monkeypatch, "2000", "2026-01-20")
    res = solve_request("r1", "u1", "2026-01-01", "5000", _profile("partial_payment"),
                        [], [], _empty_options(), None, "   ", True)
    assert res["recommended_payment_method"] == "partial_payment", f"got {res}"


def _inst_options_df():
    return pd.DataFrame([{
        "payment_option_id": "opt_inst1", "request_id": "r1", "payment_method": "installments",
        "payment_amount": "1000", "number_of_payments": 2, "first_payment_date": "2026-01-05",
        "payment_frequency_days": "30", "financing_fee": "0", "total_payable_amount": "2000",
    }], columns=["payment_option_id", "request_id", "payment_method",
                 "payment_amount", "number_of_payments", "first_payment_date",
                 "payment_frequency_days", "financing_fee", "total_payable_amount"])


def test_installment_earliest_is_oracle_not_start(monkeypatch):
    # Oracle earliest 2026-02-15 diverges from installment start 2026-01-05;
    # solver must propagate oracle earliest, never installment start.
    _stub(monkeypatch, "0", "2026-02-15")
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    res = solve_request("r1", "u1", "2026-01-01", "2000", prof,
                        [], [], _inst_options_df(), None, "2026-03-01", False,
                        installment_safety="lenient")
    assert res["recommended_payment_method"] == "installments"
    assert res["earliest_date_for_full_payment"] == "2026-02-15"


def _overdraft_stub(monkeypatch):
    # Ledger flat 10000, min 2000; legs 9000 on day0 overdraw to 1000 < 2000.
    from datetime import date, timedelta
    req = date.fromisoformat("2026-01-01")
    ledger = [Decimal("10000") for _ in range(91)]
    def fake(*a, **k):
        return {"safe_amount": Decimal("8000"), "earliest_date": "2026-01-01", "ledger": ledger}
    monkeypatch.setattr("code.solver.solve_safe_amount", fake)


def _overdraft_options_df():
    return pd.DataFrame([{
        "payment_option_id": "opt_big", "request_id": "r1", "payment_method": "installments",
        "payment_amount": "9000", "number_of_payments": 1, "first_payment_date": "2026-01-01",
        "payment_frequency_days": "30", "financing_fee": "0", "total_payable_amount": "9000",
    }], columns=["payment_option_id", "request_id", "payment_method",
                 "payment_amount", "number_of_payments", "first_payment_date",
                 "payment_frequency_days", "financing_fee", "total_payable_amount"])


def test_installment_strict_rejects_late_overdraft(monkeypatch):
    _overdraft_stub(monkeypatch)
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    res = solve_request("r1", "u1", "2026-01-01", "9000", prof,
                        [], [], _overdraft_options_df(), None, "2026-03-01", False,
                        installment_safety="strict")
    assert res["recommended_payment_method"] != "installments", f"strict must reject overdraft, got {res}"


def test_installment_lenient_accepts_late_overdraft(monkeypatch):
    _overdraft_stub(monkeypatch)
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    res = solve_request("r1", "u1", "2026-01-01", "9000", prof,
                        [], [], _overdraft_options_df(), None, "2026-03-01", False,
                        installment_safety="lenient")
    assert res["recommended_payment_method"] == "installments", f"lenient must accept (deadline only), got {res}"


def test_installment_deadline_filter_both_modes(monkeypatch):
    # Completion 2026-04-01 misses deadline 2026-02-01: rejected in both modes.
    def fake(*a, **k):
        return {"safe_amount": Decimal("8000"), "earliest_date": "2026-01-01",
                "ledger": [Decimal("10000") for _ in range(91)]}
    monkeypatch.setattr("code.solver.solve_safe_amount", fake)
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    df = pd.DataFrame([{
        "payment_option_id": "opt_late", "request_id": "r1", "payment_method": "installments",
        "payment_amount": "1000", "number_of_payments": 2, "first_payment_date": "2026-03-01",
        "payment_frequency_days": "30", "financing_fee": "0", "total_payable_amount": "2000",
    }], columns=["payment_option_id", "request_id", "payment_method",
                 "payment_amount", "number_of_payments", "first_payment_date",
                 "payment_frequency_days", "financing_fee", "total_payable_amount"])
    for mode in ("strict", "lenient"):
        res = solve_request("r1", "u1", "2026-01-01", "2000", prof,
                            [], [], df, None, "2026-02-01", False,
                            installment_safety=mode)
        assert res["recommended_payment_method"] != "installments", f"mode={mode} must respect deadline, got {res}"


def test_spending_fallback_when_all_viable_miss_deadline(monkeypatch):
    # Full viable (safe>=req) but completion req_date 2026-02-01 misses deadline 2026-01-15.
    # Viable non-empty but all miss => must still try spending changes.
    import code.solver as sm
    def fake_safe(*a, **k):
        return {"safe_amount": Decimal("5000"), "earliest_date": "2026-02-01", "ledger": []}
    monkeypatch.setattr(sm, "solve_safe_amount", fake_safe)
    seen = {}
    def fake_search(*a, **k):
        seen["called"] = True
        return ["stop:event_x"]
    monkeypatch.setattr(sm, "search_changes", fake_search)
    # Second oracle call after changes must stay safe so fallback succeeds.
    orig_fake = fake_safe
    calls = {"n": 0}
    def fake_safe2(*a, **k):
        calls["n"] += 1
        return {"safe_amount": Decimal("5000"), "earliest_date": "2026-02-01", "ledger": []}
    monkeypatch.setattr(sm, "solve_safe_amount", fake_safe2)
    prof = _profile("full_payment")
    res = solve_request("r1", "u1", "2026-02-01", "5000", prof,
                        [{"event_id": "event_x"}], [], _empty_options(), None, "2026-01-15", False)
    assert seen.get("called") is True, f"fallback search must trigger when all viable miss deadline, got {res}"
    # Both the unchanged and changed full-payment plans miss the already-past
    # deadline; the specification's no-change tie-breaker keeps the unchanged plan.
    assert res["spending_changes_needed"] == "none", f"got {res}"


def test_solver_threads_recurrence_flags(monkeypatch):
    # Solver must pass real recurrence_flags (not empty set) into search_changes.
    import code.solver as sm
    def fake_safe(*a, **k):
        # First call (base) unsafe, second (after changes) safe.
        fake_safe.n += 1
        if fake_safe.n == 1:
            return {"safe_amount": Decimal("0"), "earliest_date": None, "ledger": []}
        return {"safe_amount": Decimal("5000"), "earliest_date": None, "ledger": []}
    fake_safe.n = 0
    monkeypatch.setattr(sm, "solve_safe_amount", fake_safe)
    captured = {}
    def fake_search(uid, rdate, ramt, prof, evts, fx, recurrence_flags=None):
        captured["flags"] = recurrence_flags
        return ["stop:event_x"]
    monkeypatch.setattr(sm, "search_changes", fake_search)
    monkeypatch.setattr(sm, "detect_recurrence", lambda evts: {("u1", "dining", "expense")})
    prof = _profile("full_payment")
    res = solve_request("r1", "u1", "2026-01-01", "5000", prof,
                        [], [], _empty_options(), None, None, False)
    assert captured.get("flags") == {("u1", "dining", "expense")}, f"must thread real flags, got {captured}"


def _salary_profile():
    return {"home_currency": "USD", "current_available_balance": "8000",
            "minimum_balance_to_keep": "2000",
            "payment_methods_user_will_consider": "full_payment",
            "expense_categories_to_protect": "",
            "expense_categories_user_is_willing_to_reduce": "",
            "expense_categories_user_is_willing_to_stop": ""}


def _salary_events():
    def ev(eid, amt, sdate, cat="salary", et="income", d="credit", st="settled"):
        return {"event_id": eid, "user_id": "u1", "event_type": et, "category": cat,
                "direction": d, "amount": str(amt), "currency": "USD",
                "event_date": sdate, "settlement_date": sdate, "status": st,
                "flexibility": "fixed", "linked_event_id": "", "minimum_allowed_amount": ""}
    return [
        ev("sal1", "3000", "2025-11-15"),
        ev("sal2", "3000", "2025-12-15"),
        ev("sal3", "3000", "2026-01-15"),
        ev("bigexp", "6000", "2026-03-01", cat="rent", et="expense", d="debit"),
    ]


def _salary_messages(amount, eff_date):
    return pd.DataFrame([{
        "message_id": "m1", "user_id": "u1", "request_id": "", "related_event_id": "",
        "sent_at": "2026-01-20T09:30:00Z", "source_type": "employer",
        "message_text": f"Your monthly salary has increased to USD {amount}. The change applies from {eff_date}. Payroll ref EMP-0001."
    }], columns=["message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"])


def test_salary_raise_before_req_shifts_safe_up():
    # Raise effective 2026-01-15 (before req 2026-02-01) must increase safe vs stale average.
    from code.solver import solve_request as sr
    base = sr("r1", "u1", "2026-02-01", "5000", _salary_profile(),
              _salary_events(), [], _empty_options(), None, None, False)
    raised = sr("r1", "u1", "2026-02-01", "5000", _salary_profile(),
                _salary_events(), [], _empty_options(), _salary_messages("5000", "2026-01-15"), None, False)
    assert Decimal(raised["amount_safe_to_pay"]) > Decimal(base["amount_safe_to_pay"]), f"base={base} raised={raised}"


def test_salary_raise_after_window_no_effect():
    # Raise effective 2026-06-01 (after 90d window ending 2026-05-02) must not change safe.
    from code.solver import solve_request as sr
    base = sr("r1", "u1", "2026-02-01", "5000", _salary_profile(),
              _salary_events(), [], _empty_options(), None, None, False)
    raised = sr("r1", "u1", "2026-02-01", "5000", _salary_profile(),
                _salary_events(), [], _empty_options(), _salary_messages("5000", "2026-06-01"), None, False)
    assert raised["amount_safe_to_pay"] == base["amount_safe_to_pay"], f"base={base} raised={raised}"


def test_salary_injection_does_not_mutate_caller():
    import copy
    from code.solver import solve_request as sr
    evts = _salary_events()
    snapshot = copy.deepcopy(evts)
    sr("r1", "u1", "2026-02-01", "5000", _salary_profile(),
       evts, [], _empty_options(), _salary_messages("5000", "2026-01-15"), None, False)
    assert evts == snapshot, "solver must never mutate caller events (deepcopy required)"


def test_parser_emp0001_and_emp0002_patterns():
    import pandas as pd
    from code.parser import parse_messages
    df = pd.DataFrame([
        {"message_id": "m1", "user_id": "u1", "request_id": "", "related_event_id": "",
         "sent_at": "2025-07-29T09:30:00Z", "source_type": "employer",
         "message_text": "Rincian penggajian Anda di Cobalt Systems telah berubah. Gaji bulanan Anda naik menjadi IDR 42750000. Perubahan ini berlaku mulai 2025-08-15. Ref payroll EMP-0001."},
        {"message_id": "m2", "user_id": "u1", "request_id": "request_03", "related_event_id": "",
         "sent_at": "2019-08-31T09:30:00Z", "source_type": "employer",
         "message_text": "Tim payroll BrightPath Media telah mengirim pembaruan. Gaji rutin untuk penggajian berikutnya sudah dikonfirmasi. Slip gaji berikutnya akan menampilkan gaji rutin dan penyesuaian satu kali secara terpisah. Ref payroll EMP-0002."},
        {"message_id": "m3", "user_id": "u1", "request_id": "", "related_event_id": "",
         "sent_at": "2025-11-02T09:30:00Z", "source_type": "employer",
         "message_text": "Your quarterly bonus is still subject to the final performance review. The final amount and payment date have not been approved yet. Payroll ref EMP-0048."},
    ], columns=["message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"])
    out = parse_messages("u1", df)
    # EMP-0001 yields one raise with amount+date; EMP-0002/bonus yield none.
    assert len(out["salary_updates"]) == 1, f"got {out}"
    su = out["salary_updates"][0]
    assert su["effective_date"] == "2025-08-15" and su["amount"] == Decimal("42750000")


def test_parser_indonesian_grouping_via_amounts():
    # IDR 42.750.000 (dot-thousands) must parse to 42750000 via amounts helpers, not crash.
    import pandas as pd
    from code.parser import parse_messages
    df = pd.DataFrame([{
        "message_id": "m1", "user_id": "u1", "request_id": "", "related_event_id": "",
        "sent_at": "2025-07-29T09:30:00Z", "source_type": "employer",
        "message_text": "Gaji bulanan Anda naik menjadi IDR 42.750.000. Perubahan ini berlaku mulai 2025-08-15. Ref payroll EMP-0001."
    }], columns=["message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"])
    out = parse_messages("u1", df)
    assert len(out["salary_updates"]) == 1
    assert out["salary_updates"][0]["amount"] == Decimal("42750000")


def test_spending_fallback_earliest_is_original(monkeypatch):
    # Spending fallback must return original oracle earliest (2026-02-15), not req_date.
    # Deadline 2026-01-10 blocks wait (earliest 2026-02-15 misses), forcing fallback.
    import code.solver as sm
    calls = {"n": 0}
    def fake_safe(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"safe_amount": Decimal("100"), "earliest_date": "2026-02-15", "ledger": []}
        return {"safe_amount": Decimal("5000"), "earliest_date": "2026-02-15", "ledger": []}
    monkeypatch.setattr(sm, "solve_safe_amount", fake_safe)
    monkeypatch.setattr(sm, "search_changes", lambda *a, **k: ["stop:event_x"])
    prof = _profile("full_payment")
    res2 = solve_request("r1", "u1", "2026-01-01", "5000", prof,
                         [{"event_id": "event_x"}], [], _empty_options(), None, "2026-01-10", False)
    assert res2["affordability_status"] == "affordable_with_plan"
    assert res2["spending_changes_needed"] == "stop:event_x"
    assert res2["earliest_date_for_full_payment"] == "2026-02-15", f"got {res2}"


def _parser_df(text):
    import pandas as pd
    return pd.DataFrame([{
        "message_id": "m1", "user_id": "u1", "request_id": "", "related_event_id": "",
        "sent_at": "2026-01-01T00:00:00Z", "source_type": "employer",
        "message_text": text,
    }], columns=["message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"])


def test_parser_bonus_with_amount_yields_no_salary_update():
    from code.parser import parse_messages
    df = _parser_df("Your quarterly bonus is USD 5000. Payment date 2026-02-01. Payroll ref EMP-0048.")
    out = parse_messages("u1", df)
    assert out["salary_updates"] == [], f"bonus must not emit salary_updates, got {out}"


def test_parser_commission_with_amount_yields_no_salary_update():
    from code.parser import parse_messages
    df = _parser_df("Your commission payout is USD 3000 effective 2026-02-01. Payroll ref EMP-0049.")
    out = parse_messages("u1", df)
    assert out["salary_updates"] == [], f"commission must not emit salary_updates, got {out}"


def test_installment_safe_fail_closed():
    from code.solver import _installment_safe
    prof = {"minimum_balance_to_keep": "2000"}
    # Empty / missing ledger must be unsafe (fail-closed, 91-point contract).
    assert _installment_safe([], "2026-01-01", "2026-01-01:100", prof) is False
    assert _installment_safe(None, "2026-01-01", "2026-01-01:100", prof) is False
    # Short ledger (not 91 points) must be unsafe.
    assert _installment_safe([__import__("decimal").Decimal("10000")] * 10, "2026-01-01", "2026-01-01:100", prof) is False
    # Unparsable req_date / floor / entries must be unsafe.
    assert _installment_safe([__import__("decimal").Decimal("10000")] * 91, "bad-date", "2026-01-01:100", prof) is False
    assert _installment_safe([__import__("decimal").Decimal("10000")] * 91, "2026-01-01", "2026-01-01:100", {"minimum_balance_to_keep": "bad"}) is False
    assert _installment_safe(["bad"] * 91, "2026-01-01", "2026-01-01:100", prof) is False
    # NaN ledger balance must be explicitly unsafe.
    assert _installment_safe([__import__("decimal").Decimal("NaN")] * 91, "2026-01-01", "2026-01-01:100", prof) is False


def test_installment_rejects_nonfinite_ledger_values():
    from code.solver import _installment_safe
    prof = {"minimum_balance_to_keep": "2000"}
    for bad in ("Infinity", "-Infinity", "NaN"):
        ledger = [Decimal("10000")] * 91
        ledger[45] = Decimal(bad)
        assert _installment_safe(ledger, "2026-01-01", "2026-01-01:100", prof) is False


def test_installment_rejects_malformed_plan_fragments_and_entire_plan():
    from code.solver import _installment_safe
    prof = {"minimum_balance_to_keep": "2000"}
    ledger = [Decimal("10000")] * 91
    assert _installment_safe(ledger, "2026-01-01", "2026-01-01:100|not-a-leg", prof) is False
    assert _installment_safe(ledger, "2026-01-01", "not-a-leg", prof) is False


def test_installment_rejects_negative_and_nonfinite_amounts():
    from code.solver import _installment_safe
    prof = {"minimum_balance_to_keep": "2000"}
    ledger = [Decimal("10000")] * 91
    for amount in ("-100", "Infinity", "-Infinity", "NaN"):
        assert _installment_safe(ledger, "2026-01-01", f"2026-01-01:{amount}", prof) is False


def test_installment_tail_leg_beyond_window_strict_rejected_lenient_accepted(monkeypatch):
    import code.solver as sm
    from decimal import Decimal as _D
    def fake(*a, **k):
        return {"safe_amount": _D("8000"), "earliest_date": "2026-01-01",
                "ledger": [_D("10000") for _ in range(91)]}
    monkeypatch.setattr(sm, "solve_safe_amount", fake)
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    df = __import__("pandas").DataFrame([{
        "payment_option_id": "opt_tail", "request_id": "r1", "payment_method": "installments",
        "payment_amount": "1000", "number_of_payments": 2, "first_payment_date": "2026-01-05",
        "payment_frequency_days": "100", "financing_fee": "0", "total_payable_amount": "2000",
    }], columns=["payment_option_id", "request_id", "payment_method",
                 "payment_amount", "number_of_payments", "first_payment_date",
                 "payment_frequency_days", "financing_fee", "total_payable_amount"])
    # Legs: 2026-01-05 and 2026-04-15; req+90d = 2026-04-01 so tail leg is beyond horizon.
    # Deadline 2026-06-01 accepts completion 2026-04-15 in lenient mode.
    res_strict = solve_request("r1", "u1", "2026-01-01", "2000", prof,
                               [], [], df, None, "2026-06-01", False,
                               installment_safety="strict")
    assert res_strict["recommended_payment_method"] != "installments", f"strict must reject tail beyond window, got {res_strict}"
    res_lenient = solve_request("r1", "u1", "2026-01-01", "2000", prof,
                                [], [], df, None, "2026-06-01", False,
                                installment_safety="lenient")
    assert res_lenient["recommended_payment_method"] == "installments", f"lenient must accept (deadline only), got {res_lenient}"


def test_installment_leg_before_request_is_rejected_strict(monkeypatch):
    # The 91-point oracle ledger starts on request_date; a supplied payment
    # before that date cannot be verified against it and must fail closed.
    def fake(*a, **k):
        return {"safe_amount": Decimal("8000"), "earliest_date": "2026-01-01",
                "ledger": [Decimal("10000") for _ in range(91)]}
    monkeypatch.setattr("code.solver.solve_safe_amount", fake)
    prof = _profile("installments")
    prof["max_installment_months"] = "12"
    df = pd.DataFrame([{
        "payment_option_id": "opt_pre_request", "request_id": "r1", "payment_method": "installments",
        "payment_amount": "1000", "number_of_payments": 1, "first_payment_date": "2025-12-31",
        "payment_frequency_days": "30", "financing_fee": "0", "total_payable_amount": "1000",
    }], columns=["payment_option_id", "request_id", "payment_method",
                 "payment_amount", "number_of_payments", "first_payment_date",
                 "payment_frequency_days", "financing_fee", "total_payable_amount"])
    res = solve_request("r1", "u1", "2026-01-01", "1000", prof,
                        [], [], df, None, "2026-02-01", False,
                        installment_safety="strict")
    assert res["recommended_payment_method"] != "installments", f"pre-request leg must fail closed, got {res}"


def test_salary_flag_survives_large_raise_pre_injection():
    # 5x raise must not drop the salary recurrence flag (CV-stable pre-injection),
    # while safe_amount still reflects the raise (injection kept for amounts).
    import code.solver as sm
    real_safe = sm.solve_safe_amount
    captured = {}
    def spy(*a, **k):
        # a: (user_id, req_date, req_amt, profile, events, fx, flags)
        if len(a) >= 7:
            captured["flags"] = a[6]
        elif "recurrence_flags" in k:
            captured["flags"] = k["recurrence_flags"]
        return real_safe(*a, **k)
    sm.solve_safe_amount = spy
    try:
        base = sm.solve_request("r1", "u1", "2026-02-01", "5000", _salary_profile(),
                                _salary_events(), [], _empty_options(), None, None, False)
        captured.clear()
        raised = sm.solve_request("r1", "u1", "2026-02-01", "5000", _salary_profile(),
                                  _salary_events(), [], _empty_options(),
                                  _salary_messages("15000", "2026-01-15"), None, False)
        flags = captured.get("flags", set())
    finally:
        sm.solve_safe_amount = real_safe
    assert ("u1", "salary", "income") in (flags or set()), f"salary flag must survive 5x raise, got {flags}"
    assert __import__("decimal").Decimal(raised["amount_safe_to_pay"]) > __import__("decimal").Decimal(base["amount_safe_to_pay"]), f"safe must reflect raise: base={base} raised={raised}"


def test_spending_change_unlocks_full_payment_status():
    """Gold samples show a spending change yields affordable_with_plan + full_payment."""
    from code.oracle import solve_safe_amount
    from code.recurrence import detect_recurrence

    profile = {
        "home_currency": "USD",
        "current_available_balance": "4600",
        "minimum_balance_to_keep": "4000",
        "payment_methods_user_will_consider": "full_payment",
        "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_stop": "streaming",
        "expense_categories_user_is_willing_to_reduce": "",
        "max_installment_months": "",
    }
    events = []
    for i, month in enumerate(("2026-01", "2026-02", "2026-03")):
        events.append({
            "event_id": f"sub{i}", "user_id": "u1", "event_type": "subscription",
            "category": "streaming", "direction": "debit", "amount": "100",
            "currency": "USD", "event_date": f"{month}-05",
            "settlement_date": f"{month}-05", "status": "settled",
            "linked_event_id": "", "flexibility": "stoppable",
            "minimum_allowed_amount": "0",
        })
    flags = detect_recurrence([dict(e) for e in events])
    pre = solve_safe_amount("u1", "2026-04-01", "600", profile,
                            [dict(e) for e in events], [], flags)
    assert pre["safe_amount"] < Decimal("600"), f"fixture must need a change: {pre}"
    res = solve_request("r1", "u1", "2026-04-01", "600", profile, events, [],
                        _empty_options(), None, None, False)
    assert res["spending_changes_needed"] != "none", f"got {res}"
    assert res["affordability_status"] == "affordable_with_plan", f"got {res}"
    assert res["recommended_payment_method"] == "full_payment", f"got {res}"
    # problem_statement.md:182 / AGENTS 6.2: amount_safe_to_pay is the PRE-change
    # safe amount; the stop is what makes the full plan itself viable (plan pays
    # the whole request on request_date even though only 200 was safe before).
    assert Decimal(res["amount_safe_to_pay"]) == pre["safe_amount"], f"got {res} pre={pre}"
    assert res["payment_plan"] == "2026-04-01:600", f"got {res}"
    # Spec: earliest is the first date full payment is safe WITHOUT changes;
    # pre-change full payment never becomes safe in the horizon here.
    assert res["earliest_date_for_full_payment"] in (None, ""), f"got {res}"
