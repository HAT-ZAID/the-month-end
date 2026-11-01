"""Task 4: pure-helper tests for the interactive TUI.

These target only the helpers, never a live terminal, so CI never blocks on
stdin. The ledger/decision fixtures are synthetic; no gold-sample event IDs.
"""
from tools import tui


ROW = {
    "request_id": "request_06",
    "amount_safe_to_pay": "603.3",
    "affordability_status": "affordable_with_plan",
    "recommended_payment_method": "full_payment",
    "payment_plan": "2026-01-15:603.3",
    "earliest_date_for_full_payment": "2026-01-15",
    "spending_changes_needed": "stop:event_55",
    "decision_explanation": "Stop streaming to protect the minimum balance.",
}


def test_render_decision_row_formats_money_and_status():
    text = tui.render_decision(ROW)
    assert "request_06" in text
    assert "affordable_with_plan" in text
    assert "stop:event_55" in text


def test_build_ledger_table_has_rows():
    ledger = [{"date": f"2026-01-{d:02d}", "balance": "100"} for d in range(1, 32)]
    table = tui.build_ledger_table(ledger, minimum_balance="50")
    assert table is not None
    assert table.row_count >= len(ledger)


def test_lookup_request_returns_none_for_unknown_id():
    assert tui.lookup_request([ROW], "request_99") is None
    assert tui.lookup_request([ROW], "request_06")["request_id"] == "request_06"


def test_apply_overrides_does_not_mutate_original():
    out = tui.apply_overrides(ROW, {"requested_amount": "900"})
    assert out["requested_amount"] == "900"
    assert "requested_amount" not in ROW


def test_diff_fields_reports_changed_fields_only():
    current = tui.apply_overrides(
        dict(ROW, affordability_status="not_affordable"), {"requested_amount": "900"}
    )
    changed = dict(tui.diff_fields(ROW, current))
    assert set(changed) == {"affordability_status"}
    assert changed["affordability_status"] == ("affordable_with_plan", "not_affordable")


def test_session_undo_clears_overrides():
    s = tui.Session()
    s.set("request_06", {"requested_amount": "900"})
    assert s.effective(ROW)["requested_amount"] == "900"
    s.undo("request_06")
    assert s.effective(ROW) is ROW
    s.set("request_06", {"requested_amount": "900"})
    s.undo()
    assert s.effective(ROW) is ROW
