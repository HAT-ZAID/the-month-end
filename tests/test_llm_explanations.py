import json

import pytest

from code import explanations
from code.explanations import generate_explanation
from code.usage import new_usage, write_usage_report


def _packet(**overrides):
    packet = {
        "requested_amount": "1000",
        "amount_safe_to_pay": "400",
        "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "partial_payment",
        "payment_plan": "2026-09-13:400|2026-09-20:600",
        "earliest_date_for_full_payment": "2026-09-20",
        "minimum_balance": "200",
        "evidence_warnings": [],
    }
    packet.update(overrides)
    return packet


def test_generate_explanation_accepts_grounded_structured_response():
    packet = {
        "requested_amount": "1000",
        "amount_safe_to_pay": "400",
        "recommended_payment_method": "partial_payment",
        "payment_plan": "2026-09-13:400|2026-09-20:600",
        "earliest_date_for_full_payment": "2026-09-20",
        "minimum_balance": "200",
        "evidence_warnings": [],
    }

    result = generate_explanation(
        packet,
        call=lambda _: '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}',
    )

    assert result == "Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."


def test_generate_explanation_falls_back_when_llm_response_is_invalid():
    packet = {
        "requested_amount": "1000",
        "amount_safe_to_pay": "0",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "minimum_balance": "200",
        "evidence_warnings": [{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}],
    }

    result = generate_explanation(packet, call=lambda _: "not json")

    assert "event_01" in result
    assert "unresolved" in result.lower()


def test_generate_explanation_rejects_contradictory_model_text():
    packet = {
        "requested_amount": "1000",
        "amount_safe_to_pay": "0",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "minimum_balance": "200",
        "evidence_warnings": [{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}],
    }

    result = generate_explanation(
        packet,
        call=lambda _: '{"decision_explanation":"Buy it now with full payment; you can afford 1000."}',
    )

    assert "unresolved" in result.lower()


def test_generate_explanation_rejects_buy_now_claim_for_unsafe_request():
    packet = {
        "requested_amount": "1000",
        "amount_safe_to_pay": "400",
        "minimum_balance": "200",
        "recommended_payment_method": "partial_payment",
        "payment_plan": "2026-09-13:400|2026-09-20:600",
        "evidence_warnings": [],
    }
    result = generate_explanation(
        packet,
        call=lambda _: '{"decision_explanation":"Pay 400 now; you can pay 1000 now with full payment."}',
    )
    assert "partial_payment" in result.lower()


def test_usage_report_records_zero_calls_without_provider(tmp_path):
    usage = new_usage()
    write_usage_report(tmp_path / "usage_report.md", usage, request_count=2)
    report = (tmp_path / "usage_report.md").read_text(encoding="utf-8")
    assert "Model calls: 0" in report
    assert "Average tokens per request: 0" in report


def test_configured_call_preserves_content_and_provider_usage(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}'}}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 23, "total_tokens": 40},
            }).encode()

    monkeypatch.setenv("BUY_WAIT_LLM_ENDPOINT", "https://provider.invalid/completions")
    monkeypatch.setenv("BUY_WAIT_LLM_API_KEY", "test-key-not-secret")
    monkeypatch.setenv("BUY_WAIT_LLM_MODEL", "test-model")
    monkeypatch.setattr(explanations, "urlopen", lambda request, timeout: Response())

    result = explanations._configured_call(_packet())

    assert result["content"].startswith("{")
    assert result["provider"] == "openrouter"
    assert result["model"] == "test-model"
    assert result["input_tokens"] == 17
    assert result["output_tokens"] == 23
    assert result["total_tokens"] == 40


def test_generate_explanation_records_provider_tokens_and_cost_metadata():
    usage = new_usage()
    result = generate_explanation(
        _packet(_usage=usage),
        call=lambda _: {
            "content": '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}',
            "provider": "test-provider",
            "model": "test-model",
            "input_tokens": 17,
            "output_tokens": 23,
            "total_tokens": 40,
            "cost_usd": 0.0012,
        },
    )

    assert result.startswith("Use partial payment")
    assert usage["calls"] == 1
    assert usage["successful"] == 1
    assert usage["input_tokens"] == 17
    assert usage["output_tokens"] == 23
    assert usage["total_tokens"] == 40
    assert usage["estimated_cost_usd"] == pytest.approx(0.0012)


def test_missing_provider_usage_is_reported_without_inventing_tokens():
    usage = new_usage()
    generate_explanation(
        _packet(_usage=usage),
        call=lambda _: {
            "content": '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}',
            "provider": "test-provider",
            "model": "test-model",
        },
    )

    assert usage["calls"] == 1
    assert usage["successful"] == 1
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["total_tokens"] == 0
    assert usage["missing_usage"] == 1


@pytest.mark.parametrize("failure", [RuntimeError("http failure"), TimeoutError("timeout")])
def test_provider_failures_fall_back_and_are_counted(failure):
    usage = new_usage()

    result = generate_explanation(_packet(_usage=usage), call=lambda _: (_ for _ in ()).throw(failure))

    assert "partial_payment" in result
    assert usage["calls"] == 1
    assert usage["successful"] == 0
    assert usage["failed"] == 1
    assert usage["fallbacks"] == 1


def test_malformed_provider_json_is_rejected_and_falls_back():
    usage = new_usage()

    result = generate_explanation(_packet(_usage=usage), call=lambda _: {"content": "not json"})

    assert "partial_payment" in result
    assert usage["calls"] == 1
    assert usage["successful"] == 0
    assert usage["rejected"] == 1
    assert usage["fallbacks"] == 1


def test_call_cap_rejects_before_issuing_provider_call():
    usage = new_usage()
    usage["max_calls"] = 1
    calls = []

    call = lambda _: calls.append(True) or '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}'
    generate_explanation(_packet(_usage=usage), call=call)
    generate_explanation(_packet(_usage=usage), call=call)

    assert len(calls) == 1
    assert usage["calls"] == 1
    assert usage["rejected"] == 1
    assert usage["fallbacks"] == 1


def test_configured_call_preserves_usage_when_content_missing(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": None, "reasoning": "analysis only"}}],
                "usage": {"prompt_tokens": 33, "completion_tokens": 71, "total_tokens": 104},
            }).encode()

    monkeypatch.setenv("BUY_WAIT_LLM_ENDPOINT", "https://provider.invalid/completions")
    monkeypatch.setenv("BUY_WAIT_LLM_API_KEY", "test-key-not-secret")
    monkeypatch.setenv("BUY_WAIT_LLM_MODEL", "test-model")
    monkeypatch.setattr(explanations, "urlopen", lambda request, timeout: Response())

    result = explanations._configured_call(_packet())

    assert result["input_tokens"] == 33
    assert result["output_tokens"] == 71
    assert result["total_tokens"] == 104
    assert result["usage_present"] is True


def test_configured_call_bounds_generation_with_max_tokens(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": '{"decision_explanation":"Use partial payment: pay 400 now and 600 on 2026-09-20 while preserving the 200 minimum balance."}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("BUY_WAIT_LLM_ENDPOINT", "https://provider.invalid/completions")
    monkeypatch.setenv("BUY_WAIT_LLM_API_KEY", "test-key-not-secret")
    monkeypatch.setenv("BUY_WAIT_LLM_MODEL", "test-model")
    monkeypatch.setattr(explanations, "urlopen", fake_urlopen)

    explanations._configured_call(_packet())

    assert 1 <= int(captured["body"].get("max_tokens", 0)) <= 1000
    assert 1.0 <= float(captured["timeout"]) <= 15.0


def test_usage_report_marks_unknown_pricing_and_has_per_model_and_overall_rows(tmp_path):
    usage = new_usage()
    usage.update({
        "provider": "test-provider",
        "model": "test-model",
        "calls": 1,
        "input_tokens": 17,
        "output_tokens": 23,
        "total_tokens": 40,
        "estimated_cost_usd": None,
        "cost_status": "unavailable",
    })
    write_usage_report(tmp_path / "usage_report.md", usage, request_count=2)
    report = (tmp_path / "usage_report.md").read_text(encoding="utf-8")

    assert "Cost status: unavailable" in report
    assert "| test-provider | test-model |" in report
    assert "| Overall | all models |" in report


def test_provider_must_return_schema_fields_or_fallback():
    usage = new_usage()
    result = generate_explanation(
        _packet(_usage=usage),
        call=lambda _: {"content": '{"note":"looks fine to me"}'},
    )
    assert "partial_payment" in result
    assert usage["rejected"] == 1


def test_provider_insufficient_evidence_is_accepted_when_grounded():
    usage = new_usage()
    result = generate_explanation(
        _packet(
            _usage=usage,
            amount_safe_to_pay="0",
            recommended_payment_method="not_recommended",
            evidence_warnings=[{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}],
        ),
        call=lambda _: {"content": json.dumps({
            "decision_explanation": (
                "Not recommended: evidence for event_01 is unresolved, so safety "
                "cannot be verified while preserving the minimum balance."
            ),
            "evidence_sufficient": False,
            "missing_evidence": ["event_01"],
            "supporting_facts": ["amount_safe_to_pay=0", "minimum_balance=200"],
        })},
    )
    assert "unresolved" in result.lower()
    assert usage["successful"] == 1
