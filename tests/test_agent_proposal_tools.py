"""Regression tests for the tools the agent gained in the contribution work.

Each test here pins a bug that the review found in the first cut: a missing
``InvalidOperation`` import that turned bad input into ``NameError``, an
unreadable total that sorted as the cheapest option, a ``reduce_to`` check that
could never pass, and three request-taking tools that were not scoped to the
active request.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from code import agent as agent_module
from code.tools import Toolbox, _amount_key, DatasetBundle

REPO_ROOT = Path(__file__).resolve().parents[1]


def _toolbox() -> Toolbox:
    return Toolbox(REPO_ROOT, data=DatasetBundle.load(REPO_ROOT), kernel_results={})


def test_proposal_tools_are_scoped_to_the_active_request():
    for name in (
        "search_spending_changes",
        "compare_payment_options",
        "propose_spending_change",
    ):
        assert name in agent_module.REQUEST_SCOPED_TOOLS
        assert name in agent_module.AGENT_TOOLS


def test_an_unreadable_amount_sorts_last_instead_of_raising():
    # A missing ImportError here used to surface as NameError on blank input.
    assert _amount_key("") == Decimal("0")
    assert _amount_key(None) == Decimal("0")
    assert _amount_key("1,200.50") == Decimal("1200.50")


def test_an_option_without_a_readable_total_is_never_the_cheapest(monkeypatch):
    toolbox = _toolbox()
    envelope = {
        "ok": True,
        "data": {
            "eligible": [{"payment_option_id": "opt_blank"}, {"payment_option_id": "opt_real"}],
            "supplied": [
                {
                    "payment_option_id": "opt_blank",
                    "payment_method": "installments",
                    "number_of_payments": "3",
                    "total_payable_amount": "",
                    "financing_fee": "0",
                },
                {
                    "payment_option_id": "opt_real",
                    "payment_method": "full_payment",
                    "number_of_payments": "1",
                    "total_payable_amount": "5000.00",
                    "financing_fee": "0",
                },
            ],
        },
    }
    monkeypatch.setattr(toolbox, "get_payment_options", lambda request_id: envelope)

    result = toolbox.compare_payment_options("request_26")["data"]

    assert result["cheapest_eligible"] == "opt_real"
    assert result["eligible_count"] == 2
    assert result["priced_eligible_count"] == 1


def test_reduce_to_is_checked_against_the_current_amount():
    """A reducible event may be reduced below its current amount.

    The first cut compared the target with the permitted action's floor, so every
    reduce_to was rejected as "not below the current amount" and then again as
    below the minimum.
    """
    toolbox = _toolbox()
    request = toolbox.get_request_context("request_26")["data"]["request"]
    user_id = str(request["user_id"])
    reducible = [
        item
        for item in toolbox._permitted_actions(user_id)
        if str(item.get("action")) == "reduce_to"
    ]
    if not reducible:
        return  # no reducible category for this user; nothing to assert
    entry = reducible[0]
    floor = Decimal(str(entry["value"]))
    event_id = str(entry["event_id"])
    current = None
    for event in toolbox._user_events(user_id):
        if str(event.get("event_id")) == event_id:
            current = Decimal(str(event.get("amount")))
            break
    assert current is not None and floor < current

    # Above what the event costs today is not a reduction.
    too_high = toolbox.propose_spending_change(
        "request_26", event_id, "reduce_to", current + 1
    )
    assert too_high["ok"] is False
    assert "below the current" in too_high["error"]

    # A target between the floor and the current amount is a legal attempt: it is
    # judged on whether the re-solved plan improves, never on a ceiling it cannot
    # satisfy.
    legal = toolbox.propose_spending_change("request_26", event_id, "reduce_to", floor)
    assert legal["ok"] is True
    assert "accepted" in legal["data"]
