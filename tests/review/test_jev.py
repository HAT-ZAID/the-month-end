"""Jev gate contract: typed answers in, verdicts out, credentials never leak."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from code.jev import (
    implied_choice,
    DEFAULT_MODEL,
    JevAnswer,
    JevGate,
    JevOutcome,
    METHOD_OPTIONS,
    OPENCODE_API_KEY_ENV,
    STATUS_OPTIONS,
    SYSTEMONE_URL,
    evaluate,
    gate_questions,
    load_api_key,
    summarize,
)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _set_key(monkeypatch, value="k-test"):
    monkeypatch.setenv(OPENCODE_API_KEY_ENV, value)


def test_load_api_key_prefers_the_environment(monkeypatch):
    _set_key(monkeypatch)
    key, source = load_api_key()
    assert key == "k-test"
    assert source == "environment"


def test_load_api_key_reads_opencode_auth_json(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"openrouter": {"key": "other"}, "opencode": {"key": "zen-key"}}), encoding="utf-8")
    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENCODE_AUTH_JSON", str(auth))
    key, source = load_api_key()
    assert key == "zen-key"
    assert "auth.json" in source


def test_missing_key_is_reported_as_a_reason(monkeypatch, tmp_path):
    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENCODE_AUTH_JSON", str(tmp_path / "missing.json"))
    key, reason = load_api_key()
    assert key is None
    assert "OPENCODE_API_KEY" in reason


def test_describe_reports_availability_without_the_key(monkeypatch):
    _set_key(monkeypatch, "k-secret")
    described = JevGate().describe()
    assert described["available"] is True
    assert described["key_source"] == "environment"
    assert described["model"] == DEFAULT_MODEL
    assert described["base_url"] == SYSTEMONE_URL
    assert "k-secret" not in json.dumps(described)


def test_ask_without_a_key_fails_open(monkeypatch):
    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENCODE_AUTH_JSON", "Z:/nowhere/auth.json")
    outcome = JevGate().ask({"x": 1}, {"q": {"type": "noul"}})
    assert outcome.ok is False
    assert outcome.answers == {}
    assert "no API key" in outcome.error


def test_ask_posts_the_typed_body_and_parses_answers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        # urllib capitalises stored header keys, so ask for "User-agent".
        captured["user_agent"] = request.get_header("User-agent")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        envelope = {
            "model": "jev-1.13-free",
            "answers": {
                "status_pred": {"type": "choice", "choice": "affordable_later", "probabilities": {"affordable_now": 0.2, "affordable_later": 0.8}},
                "decision_safety": {"type": "noul", "noul": 0.1},
                "evidence_sufficiency": {"type": "score", "score": 0.75},
            },
            "usage": {"input_tokens": 320, "output_tokens": 12, "cost": 0, "opencode": {"billing_source": "free"}},
        }
        return FakeResponse(json.dumps(envelope).encode("utf-8"))

    monkeypatch.setattr("code.jev._urlopen", fake_urlopen)
    _set_key(monkeypatch)

    gate = JevGate(model="jev-1.13-free", timeout=5)
    questions = gate_questions()
    outcome = gate.ask({"balance": "176"}, questions)

    assert outcome.ok
    assert captured["url"] == SYSTEMONE_URL
    assert captured["auth"] == "Bearer k-test"
    assert (
        captured.get("user_agent") == "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    ), "the edge protection needs a normal client User-Agent or it answers 403"
    body = captured["body"]
    assert body["model"] == "jev-1.13-free"
    assert set(body["questions"]) == set(questions)
    assert all(spec["type"] == "noul" for spec in body["questions"].values())
    assert len(body["questions"]) == 11
    assert outcome.answers["status_pred"].value == "affordable_later"  # unknown qid still parses
    assert outcome.answers["decision_safety"].value == pytest.approx(0.1)
    assert outcome.answers["evidence_sufficiency"].value == pytest.approx(0.75)
    assert outcome.usage["input_tokens"] == 320
    assert outcome.usage["billing_source"] == "free"


def test_http_error_fails_open_without_raising(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            SYSTEMONE_URL, 401, "Unauthorized", None, io.BytesIO(b'{"error": "bad key"}')
        )

    monkeypatch.setattr("code.jev._urlopen", fake_urlopen)
    _set_key(monkeypatch, "k-wrong")
    outcome = JevGate().ask({}, gate_questions())
    assert outcome.ok is False
    assert "HTTP 401" in outcome.error
    assert "bad key" in outcome.error


def test_network_error_fails_open(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("code.jev._urlopen", fake_urlopen)
    _set_key(monkeypatch)
    outcome = JevGate().ask({}, gate_questions())
    assert outcome.ok is False
    assert "URLError" in outcome.error


def test_malformed_body_fails_open(monkeypatch):
    monkeypatch.setattr("code.jev._urlopen", lambda request, timeout=None: FakeResponse(b"<html>nope"))
    _set_key(monkeypatch)
    outcome = JevGate().ask({}, gate_questions())
    assert outcome.ok is False
    assert "malformed" in outcome.error


def test_summarize_is_serializable_and_hides_raw_answers():
    outcome = JevOutcome(
        answers={"decision_safety": JevAnswer(qid="decision_safety", type="noul", value=0.1, raw={"deep": {"nested": 1}})},
        usage={"input_tokens": 100, "output_tokens": 5, "cost": 0},
        model=DEFAULT_MODEL,
        latency_ms=42,
    )
    text = json.dumps(summarize(outcome))
    assert '"raw"' not in text
    assert json.loads(text)["answers"]["decision_safety"]["value"] == pytest.approx(0.1)


def test_summarize_error_shape():
    text = json.dumps(summarize(JevOutcome(error="HTTP 401", model=DEFAULT_MODEL)))
    parsed = json.loads(text)
    assert parsed["ok"] is False and parsed["error"] == "HTTP 401"


def test_gate_questions_cover_the_audit_surface():
    # The free decision model answers noul propositions only: every choice or
    # score question is refused with 422 "Endpoint is unavailable".
    questions = gate_questions()
    assert all(f"status_is_{status}" in questions for status in STATUS_OPTIONS)
    assert all(f"method_is_{method}" in questions for method in METHOD_OPTIONS)
    assert questions["decision_safety"]["type"] == "noul"
    assert questions["evidence_sufficiency"]["type"] == "noul"
    assert len(questions) == 11
    assert all(spec["type"] == "noul" for spec in questions.values())
    assert questions["decision_safety"]["type"] == "noul"


def test_evaluate_uses_the_full_preset(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["questions"] = json.loads(request.data.decode("utf-8"))["questions"]

        def _reply(envelope_payload):
            return FakeResponse(json.dumps(envelope_payload).encode("utf-8"))

        return _reply({"model": "jev-1.13-free", "answers": {}})

    monkeypatch.setattr("code.jev._urlopen", fake_urlopen)
    _set_key(monkeypatch)
    outcome = evaluate(JevGate(), {"request": "request_01"})
    assert outcome.ok
    assert set(seen["questions"]) == set(gate_questions())


def test_implied_choice_takes_the_highest_noul():
    outcome = JevOutcome(
        answers={
            "status_is_affordable_now": JevAnswer("status_is_affordable_now", "noul", 0.2, None, None),
            "status_is_not_affordable": JevAnswer("status_is_not_affordable", "noul", 0.78, None, None),
            "method_is_wait": JevAnswer("method_is_wait", "noul", 0.5, None, None),
            "method_is_not_recommended": JevAnswer("method_is_not_recommended", "noul", 0.5, None, None),
        },
        model="jev-1.13-free",
    )

    assert implied_choice(outcome, "status_is_", STATUS_OPTIONS) == ("not_affordable", 0.78)
    # A tie keeps the first option in the contract's order, never a random pick.
    assert implied_choice(outcome, "method_is_", METHOD_OPTIONS) == ("wait", 0.5)


def test_implied_choice_is_none_without_a_usable_outcome():
    failed = JevOutcome(model="jev-1.13-free", error="HTTP 422")
    assert implied_choice(failed, "status_is_", STATUS_OPTIONS) == (None, None)
    empty = JevOutcome(answers={}, model="jev-1.13-free")
    assert implied_choice(empty, "status_is_", STATUS_OPTIONS) == (None, None)


# ---------------------------------------------------------------------------
# The endpoint and the transport are guarded, not just configured
# ---------------------------------------------------------------------------


def test_a_plaintext_remote_endpoint_is_refused_before_the_key_is_sent(monkeypatch):
    gate = JevGate(base_url="http" "://systemone.example.invalid/v1", env={})
    calls = []

    def transport(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the transport must not be reached")

    monkeypatch.setattr("code.jev._urlopen", transport)
    outcome = gate.ask({"balance": "100"}, {}, api_key="k-test")
    assert outcome.error
    assert "https" in outcome.error
    assert calls == []
def test_the_jev_transport_refuses_a_redirect():
    from code import jev

    handler = jev._RefuseRedirects()
    target = "http" "://" "elsewhere" ".invalid" "/v1/chat"
    assert handler.redirect_request(None, None, 302, "Found", {}, target) is None
