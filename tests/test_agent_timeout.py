"""CR-10: a provider deadline must reach the transport and be recorded honestly.

The old wrapper only stopped *waiting*: the daemon thread kept the request alive
for up to the SDK's 600 s transport default, and the trace could not say whether
the turn was still running.  These tests pin the two halves of the fix:

* the HTTP transport is lowered to the remaining deadline before the turn is
  sent (never widened beyond the configured ceiling);
* an expired join is reported as ``cancellation_confirmed = False`` and no
  second attempt is started while that call may still run.

The fake provider sleeps longer than the deadline on purpose.  It has no HTTP
transport, so it also pins that the deadline path is harmless for in-process
models.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code.agent import _invoke_with_timeout, run_agent_loop
from code.tools import DatasetBundle, Toolbox
from code.trace import TraceRecorder
from tests.support.fake_provider import FakeProvider

REQUEST_ID = "request_demo"
USER_ID = "user_demo"

_EMPTY_EVENTS = (
    "event_id,user_id,event_type,description,category,direction,amount,currency,"
    "event_date,settlement_date,status,linked_event_id,flexibility,"
    "minimum_allowed_amount\n"
)


def _tiny_repo(root: Path) -> Path:
    """A one-request dataset: the loop needs real frames, not a live model."""
    dataset = root / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    (dataset / "requests.csv").write_text(
        "request_id,user_id,request_date,request_type,requested_amount,"
        "desired_completion_date,allows_partial_payment,request_text\n"
        f"{REQUEST_ID},{USER_ID},2026-01-05,purchase,1000,2026-01-31,false,"
        "Is 1000 safe today?\n",
        encoding="utf-8",
    )
    (dataset / "financial_profiles.csv").write_text(
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
        "financial_priorities,expense_categories_to_protect,"
        "expense_categories_user_is_willing_to_reduce,"
        "expense_categories_user_is_willing_to_stop,"
        "payment_methods_user_will_consider,max_installment_months\n"
        f"{USER_ID},USD,5000,2000,,,,,full_payment,\n",
        encoding="utf-8",
    )
    (dataset / "financial_events.csv").write_text(_EMPTY_EVENTS, encoding="utf-8")
    (dataset / "exchange_rates.csv").write_text(
        "rate_date,from_currency,to_currency,rate\n", encoding="utf-8"
    )
    (dataset / "request_payment_options.csv").write_text(
        "payment_option_id,request_id,payment_method,payment_amount,"
        "number_of_payments,first_payment_date,payment_frequency_days,financing_fee,"
        "total_payable_amount\n"
        f"payment_option_01,{REQUEST_ID},full_payment,1000,1,2026-01-05,,0,1000\n",
        encoding="utf-8",
    )
    (dataset / "messages.csv").write_text(
        "message_id,user_id,request_id,related_event_id,sent_at,source_type,message_text\n",
        encoding="utf-8",
    )
    (dataset / "images.csv").write_text(
        "image_id,user_id,request_id,related_event_id\n", encoding="utf-8"
    )
    return root


def _toolbox(root: Path, recorder) -> Toolbox:
    return Toolbox(
        root,
        data=DatasetBundle.load(root),
        kernel_results={},
        trace=recorder,
        retriever=None,
    )


def _context() -> dict:
    return {
        "request_id": REQUEST_ID,
        "user_id": USER_ID,
        "request": {
            "request_id": REQUEST_ID,
            "user_id": USER_ID,
            "request_date": "2026-01-05",
            "request_type": "purchase",
            "requested_amount": "1000",
            "desired_completion_date": "2026-01-31",
            "allows_partial_payment": "false",
            "request_text": "Is 1000 safe today?",
        },
        "profile": {
            "user_id": USER_ID,
            "home_currency": "USD",
            "current_available_balance": "5000",
            "minimum_balance_to_keep": "2000",
            "payment_methods_user_will_consider": "full_payment",
        },
    }


# ---------------------------------------------------------------------------
# 1. An expired deadline is unconfirmed and is never retried
# ---------------------------------------------------------------------------


def test_expired_deadline_is_unconfirmed_and_never_retried(tmp_path):
    recorder = TraceRecorder("run-timeout", tmp_path / "traces")
    toolbox = _toolbox(_tiny_repo(tmp_path), recorder)
    provider = FakeProvider(replies=["too late"], delay=2.0)

    result = run_agent_loop(
        _context(),
        provider=provider,
        tools=toolbox,
        trace=recorder,
        timeout_s=0.1,
    )

    assert result.fallback_reason and "timeout" in result.fallback_reason
    # No second turn while the first invocation may still be running.
    assert provider.calls == 1

    payload = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    failures = [event for event in payload["events"] if event["kind"] == "provider_failure"]
    assert len(failures) == 1
    assert failures[0]["cancellation_confirmed"] is False
    # The fake has no HTTP transport, so there is nothing to bound.
    assert failures[0]["transport_timeout_s"] is None


def test_invoke_timeout_reports_unconfirmed_cancellation():
    provider = FakeProvider(replies=["too late"], delay=2.0)
    bound = provider.chat_model().bind_tools([])

    with pytest.raises(TimeoutError) as info:
        _invoke_with_timeout(bound, [], 0.1)

    assert getattr(info.value, "cancellation_confirmed", None) is False
    assert provider.calls == 1


# ---------------------------------------------------------------------------
# 2. The transport is lowered before the turn is sent
# ---------------------------------------------------------------------------


class _FakeTimeout:
    """The one attribute the code needs from an httpx timeout object."""

    def __init__(self, read: float):
        self.read = float(read)


class _FakeTransport:
    """An httpx-shaped client: a settable timeout and a send method."""

    def __init__(self, read: float = 600.0):
        self.timeout = _FakeTimeout(read)

    def send(self, *args, **kwargs):  # pragma: no cover - never called here
        raise AssertionError("the transport must not be used in this test")


class _TransportModel:
    """A chat model that owns a transport and records what it saw at send time."""

    def __init__(self, transport: _FakeTransport):
        self.http_client = transport
        self.timeout_seen: float | None = None

    def bind_tools(self, schemas, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.timeout_seen = self.http_client.timeout.read
        return "ok"


def test_transport_timeout_is_lowered_before_the_turn_is_sent():
    from code.providers import apply_transport_timeout

    transport = _FakeTransport(600.0)
    model = _TransportModel(transport)

    assert apply_transport_timeout(model, 12.5) == pytest.approx(12.5)
    assert transport.timeout.read == pytest.approx(12.5)

    assert _invoke_with_timeout(model.bind_tools([]), [], 5.0) == "ok"
    assert model.timeout_seen == pytest.approx(5.0)


def test_transport_timeout_never_widens_the_configured_ceiling():
    from code.providers import apply_transport_timeout

    transport = _FakeTransport(600.0)
    model = _TransportModel(transport)

    assert apply_transport_timeout(model, 30.0) == pytest.approx(30.0)
    # A longer deadline may restore the ceiling but must not exceed it.
    assert apply_transport_timeout(model, 3600.0) == pytest.approx(600.0)
    assert transport.timeout.read == pytest.approx(600.0)


def test_a_model_without_a_transport_is_left_alone():
    from code.providers import apply_transport_timeout

    provider = FakeProvider()
    assert apply_transport_timeout(provider.chat_model(), 1.0) is None
    assert not hasattr(provider.chat_model(), "timeout")
