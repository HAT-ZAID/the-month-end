"""Task 6A: dashboard helper + session-only manual entry tests.

Every test uses synthetic data, a captured console and a scripted ``input_fn``;
no live terminal, no dataset write, no solver call (``_solve`` is patched).
"""
import builtins
import io
from decimal import Decimal

import pytest
from rich.console import Console

from tools import tui


def make_data():
    return {
        "requests": [
            {
                "request_id": "request_01",
                "user_id": "user_01",
                "request_date": "2026-01-05",
                "request_type": "purchase",
                "requested_amount": "100",
                "desired_completion_date": "2026-01-20",
                "allows_partial_payment": "false",
                "request_text": "",
            }
        ],
        "profiles": {
            "user_01": {
                "user_id": "user_01",
                "home_currency": "USD",
                "minimum_balance_to_keep": "50",
                "payment_methods_user_will_consider": "full_payment|partial_payment|wait",
            }
        },
        "events_by_user": {"user_01": []},
        "fx": [],
        "options": None,
        "messages": None,
        "warnings": [],
    }


def make_console():
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=110, force_terminal=False, highlight=False, markup=False
    )
    return console, buffer


class Answers:
    """Scripted input_fn that fails loudly if the code prompts too often."""

    def __init__(self, values):
        self.values = list(values)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.values:
            raise AssertionError(f"unexpected extra prompt {prompt!r}")
        return self.values.pop(0)


def make_app(answers, monkeypatch=None):
    console, buffer = make_console()
    app = tui.TUI(data=make_data(), input_fn=Answers(answers), console=console)
    return app, buffer


# ---------------------------------------------------------------------------
# 1. dashboard summary helper
# ---------------------------------------------------------------------------

REQUESTS = [
    {"request_id": "r_small", "requested_amount": "40"},
    {"request_id": "r_large", "requested_amount": "900"},
    {"request_id": "r_mid", "requested_amount": "250"},
]

DECISIONS = [
    {
        "request_id": "r_small",
        "amount_safe_to_pay": "40",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "spending_changes_needed": "none",
    },
    {
        "request_id": "r_large",
        "amount_safe_to_pay": "0",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "spending_changes_needed": "none",
    },
    {
        "request_id": "r_mid",
        "amount_safe_to_pay": "120",
        "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "partial_payment",
        "spending_changes_needed": "stop:event_9",
    },
]


def test_dashboard_summarizes_statuses_and_methods():
    summary = tui.dashboard_summary(REQUESTS, DECISIONS)

    assert summary["total"] == 3
    assert summary["by_status"]["affordable_now"] == 1
    assert summary["by_status"]["affordable_with_plan"] == 1
    assert summary["by_status"]["affordable_later"] == 0
    assert summary["by_status"]["not_affordable"] == 1
    assert summary["by_method"]["full_payment"] == 1
    assert summary["by_method"]["partial_payment"] == 1
    assert summary["by_method"]["not_recommended"] == 1
    assert summary["by_method"]["installments"] == 0
    assert summary["spending_changes"] == 1
    assert summary["zero_safe"] == 1
    assert summary["fail_closed"] == 1
    assert summary["missing_decisions"] == 0

    attention = summary["attention"]
    assert [entry["request_id"] for entry in attention] == ["r_large", "r_mid", "r_small"]
    assert attention[0]["requested_amount"] == Decimal("900")

    text = tui.render_dashboard(summary, source_label="output.csv snapshot")
    assert "needs attention" in text
    assert text.index("r_large") < text.index("r_small")


# ---------------------------------------------------------------------------
# 2. stale / mismatched output.csv snapshot
# ---------------------------------------------------------------------------

HEADER = (
    "request_id,amount_safe_to_pay,affordability_status,"
    "recommended_payment_method,payment_plan,earliest_date_for_full_payment,"
    "spending_changes_needed,decision_explanation\n"
)


def test_dashboard_rejects_mismatched_output_snapshot(tmp_path):
    snapshot = tmp_path / "output.csv"
    snapshot.write_text(
        HEADER
        + "request_01,10,affordable_now,full_payment,2026-01-05:10,2026-01-05,none,x\n"
        + "request_99,10,affordable_now,full_payment,2026-01-05:10,2026-01-05,none,x\n",
        encoding="utf-8",
    )

    rows, message = tui.read_output_snapshot(snapshot, ["request_01"])

    assert rows is None
    assert "does not match" in message
    assert "run the pipeline" in message
    assert message.strip()


def test_dashboard_accepts_matching_output_snapshot(tmp_path):
    snapshot = tmp_path / "output.csv"
    snapshot.write_text(
        HEADER + "request_01,100,affordable_now,full_payment,2026-01-05:100,2026-01-05,none,x\n",
        encoding="utf-8",
    )

    rows, message = tui.read_output_snapshot(snapshot, ["request_01"])

    assert message == ""
    assert len(rows) == 1
    assert rows[0]["request_id"] == "request_01"
    summary = tui.dashboard_summary(
        [{"request_id": "request_01", "requested_amount": "100"}], rows
    )
    assert summary["by_status"]["affordable_now"] == 1


def test_dashboard_reports_missing_snapshot(tmp_path):
    rows, message = tui.read_output_snapshot(tmp_path / "absent.csv", ["request_01"])
    assert rows is None
    assert "run the pipeline" in message


# ---------------------------------------------------------------------------
# 3/4. manual entry validation keeps state unchanged
# ---------------------------------------------------------------------------

def test_new_rejects_unknown_user_and_keeps_state_unchanged():
    app, buffer = make_app(["user_99"])
    before = list(app.data["requests"])

    app.cmd_new([])

    assert "unknown user" in buffer.getvalue()
    assert app.data["requests"] == before
    assert app.manual_requests() == []
    assert "manual_0001" not in buffer.getvalue()


@pytest.mark.parametrize(
    "answers, expected",
    [
        (["user_01", "2026-02-01", "abc"], "requested_amount"),
        (["user_01", "2026-02-01", "0"], "requested_amount"),
        (["user_01", "2026-02-01", "-5"], "requested_amount"),
        (["user_01", "2026-02-31", "10"], "request_date"),
        (["user_01", "2026-02-10", "10", "2026-02-01"], "desired_completion_date"),
    ],
)
def test_new_rejects_bad_amount_and_bad_dates(answers, expected):
    app, buffer = make_app(answers)
    before = list(app.data["requests"])

    app.cmd_new([])

    assert expected in buffer.getvalue()
    assert app.data["requests"] == before
    assert app.manual_requests() == []


def test_new_cancels_cleanly_on_eof():
    def eof(prompt=""):
        raise EOFError

    app, buffer = make_app([])
    app.input_fn = eof
    before = list(app.data["requests"])

    app.cmd_new([])

    assert "cancelled" in buffer.getvalue()
    assert app.data["requests"] == before
    assert app.manual_requests() == []


# ---------------------------------------------------------------------------
# 5. manual request is session-only, solved and visible
# ---------------------------------------------------------------------------

def test_new_creates_session_only_request_and_solves(monkeypatch):
    calls = []

    def fake_solve(context, data):
        calls.append(context.row["request_id"])
        return {
            "request_id": context.row["request_id"],
            "amount_safe_to_pay": "125",
            "affordability_status": "affordable_with_plan",
            "recommended_payment_method": "partial_payment",
            "payment_plan": "2026-02-01:125|2026-02-01:125",
            "earliest_date_for_full_payment": "2026-02-01",
            "spending_changes_needed": "none",
            "decision_explanation": "synthetic manual decision",
        }

    monkeypatch.setattr(tui, "_solve", fake_solve)

    app, buffer = make_app(
        ["user_01", "2026-02-01", "250", "", "y", "", "New phone"]
    )
    writes = []
    real_open = builtins.open

    def guard(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard)

    app.cmd_new([])

    ids = [request["request_id"] for request in app.data["requests"]]
    assert ids == ["request_01", "manual_0001"]
    row = tui.lookup_request(app.data["requests"], "manual_0001")
    assert row["user_id"] == "user_01"
    assert row["request_date"] == "2026-02-01"
    assert row["requested_amount"] == "250"
    assert row["desired_completion_date"] == "2026-02-01"
    assert row["allows_partial_payment"] == "true"
    assert row["request_type"] == "other"
    assert row["request_text"] == "New phone"
    assert app.manual_requests() == ["manual_0001"]
    assert calls == ["manual_0001"]
    assert "manual_0001" in buffer.getvalue()
    assert "affordable_with_plan" in buffer.getvalue()
    assert writes == []

    # Session-scoped: the row is visible to the other commands' lookup path.
    context = app._context("manual_0001")
    assert context is not None
    assert context.user_id == "user_01"

    # A second entry continues the sequence.
    app.input_fn = Answers(["user_01", "2026-02-02", "10", "", "", "", ""])
    app.cmd_new([])
    assert app.manual_requests() == ["manual_0001", "manual_0002"]


def test_manual_request_is_visible_to_session_commands(monkeypatch):
    def fake_solve(context, data):
        requested = context.row.get("requested_amount")
        safe = "125" if requested == "250" else "90"
        return {
            "request_id": context.row["request_id"],
            "amount_safe_to_pay": safe,
            "affordability_status": "affordable_now"
            if safe == requested
            else "affordable_with_plan",
            "recommended_payment_method": "full_payment",
            "payment_plan": f"2026-02-01:{safe}",
            "earliest_date_for_full_payment": "2026-02-01",
            "spending_changes_needed": "none",
            "decision_explanation": "synthetic manual explanation",
        }

    monkeypatch.setattr(tui, "_solve", fake_solve)
    app, buffer = make_app(["user_01", "2026-02-01", "250", "", "", "", ""])

    app.cmd_new([])
    app.cmd_list([])
    app.cmd_whatif(["manual_0001", "amount=90"])
    app.cmd_diff(["manual_0001"])
    app.cmd_explain(["manual_0001"])
    app.cmd_undo(["manual_0001"])

    output = buffer.getvalue()
    assert "manual_0001" in output
    assert "override for manual_0001" in output
    assert "no active overrides" not in output  # whatif added one
    assert "synthetic manual explanation" in output
    assert "undo manual_0001" in output
    assert app.manual_requests() == ["manual_0001"]
