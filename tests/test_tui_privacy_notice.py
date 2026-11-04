"""The TUI must warn before a free provider receives free text.

The privacy policy lives in ``AGENTS.md`` §2 and in ``code.providers``; this file
pins the *surface* half of it: `ask` prints the warning once per session, says
plainly that a free tier may train on the data, and stays silent for the local
and paid classes.

No live terminal and no solver call: ``build_provider`` and ``run_task`` are
replaced with fakes, so nothing here can block on stdin or touch the network.
"""
from __future__ import annotations

import builtins
import io

import pytest
from rich.console import Console

import tools.tui as tui
from code.providers import FREE_TIER_WARNING, PRIVACY_FREE, PRIVACY_LOCAL, PRIVACY_PAID


def make_data() -> dict:
    return {
        "requests": [
            {
                "request_id": "request_01",
                "user_id": "user_01",
                "request_date": "2026-01-05",
                "requested_amount": "100",
                "allows_partial_payment": "false",
            }
        ],
        "profiles": {
            "user_01": {
                "user_id": "user_01",
                "home_currency": "USD",
                "minimum_balance_to_keep": "50",
                "payment_methods_user_will_consider": "full_payment|wait",
            }
        },
        "events_by_user": {"user_01": []},
        "fx": [],
        "options": None,
        "messages": None,
        "warnings": [],
    }


def make_app():
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=110, force_terminal=False, highlight=False, markup=False
    )
    app = tui.TUI(data=make_data(), input_fn=lambda prompt="": "", console=console)
    return app, buffer


def fake_provider(privacy: str):
    """Minimal stand-in: ``cmd_ask`` only reads ``privacy``."""

    return type("FakeProvider", (), {"privacy": privacy, "name": "fake", "model": "m"})()


class FakeResult:
    steps: list = []
    summary = "ok"
    changed: list = []
    actions_applied: list = []
    trace_path = None

    @property
    def ok(self) -> bool:
        return True


@pytest.fixture
def patched(monkeypatch):
    """Patch the two lazy imports ``cmd_ask`` performs at call time."""

    import code.providers
    import code.task_agent

    state = {"provider": None, "reason": None, "calls": 0}

    def build_provider(env=None):
        state["calls"] += 1
        return state["provider"], state["reason"]

    def run_task(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(code.providers, "build_provider", build_provider)
    monkeypatch.setattr(code.task_agent, "run_task", run_task)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    return state


def ask(app, line: str = "ask can I afford 40"):
    app.cmd_ask(line)


def warning_text() -> str:
    return " ".join(FREE_TIER_WARNING.split())


def plain(buffer) -> str:
    """Flatten the console's line wrapping so assertions read the sentence."""
    return " ".join(buffer.getvalue().split())


def test_a_free_provider_warns_before_it_receives_free_text(patched):
    patched["provider"] = fake_provider(PRIVACY_FREE)
    app, buffer = make_app()

    ask(app)

    output = plain(buffer)
    assert warning_text() in output
    assert "train" in output.lower()


def test_the_warning_names_what_must_not_be_entered(patched):
    patched["provider"] = fake_provider(PRIVACY_FREE)
    app, buffer = make_app()

    ask(app)

    output = plain(buffer).lower()
    assert "sensitive" in output
    assert "card numbers" in output


def test_the_warning_is_shown_once_per_session(patched):
    patched["provider"] = fake_provider(PRIVACY_FREE)
    app, buffer = make_app()

    ask(app)
    ask(app)

    assert plain(buffer).count(warning_text()) == 1


def test_a_local_provider_never_warns(patched):
    patched["provider"] = fake_provider(PRIVACY_LOCAL)
    app, buffer = make_app()

    ask(app)

    assert warning_text() not in plain(buffer)


def test_a_paid_provider_never_warns(patched):
    patched["provider"] = fake_provider(PRIVACY_PAID)
    app, buffer = make_app()

    ask(app)

    assert warning_text() not in plain(buffer)


def test_an_accepted_free_tier_still_warns_in_the_tui(monkeypatch, patched):
    from code.providers import ACCEPT_FREE_TIER_ENV

    # The acceptance switch exists for batch and task runs. An interactive
    # surface still owes the person the notice, so a copied .env cannot hide it.
    monkeypatch.setenv(ACCEPT_FREE_TIER_ENV, "1")
    patched["provider"] = fake_provider(PRIVACY_FREE)
    app, buffer = make_app()

    ask(app)

    assert warning_text() in plain(buffer)


def test_no_provider_means_no_privacy_warning(patched):
    patched["provider"] = None
    patched["reason"] = "BUY_WAIT_LLM_PROVIDER is unset or 'none'"
    app, buffer = make_app()

    ask(app)

    output = buffer.getvalue()
    assert warning_text() not in output
    assert "no model configured" in output
