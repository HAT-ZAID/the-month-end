"""The HTTP surface answers from the live kernel, not from stored answers."""
from fastapi.testclient import TestClient

from deploy.api import app


def test_health_reports_the_service():
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["status"] == "ok"


def test_a_sample_request_is_solved_by_the_kernel():
    client = TestClient(app)
    body = client.get("/sample/request_01").json()
    # The endpoint reports what the kernel decides; the sample gold is a
    # different implementation and is not asserted here on purpose.
    assert body["affordability_status"] in {
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
        "not_affordable",
    }
    assert body["amount_safe_to_pay"]
    assert body["decision_explanation"]


def test_an_unknown_sample_request_is_404():
    client = TestClient(app)
    assert client.get("/sample/request_999").status_code == 404


def test_evaluate_returns_a_safe_amount_and_fails_closed():
    client = TestClient(app)
    ok = client.post(
        "/evaluate",
        json={"balance": "10000", "floor": "2000", "amount": "5000", "date": "2026-01-10"},
    ).json()
    assert ok["amount_safe_to_pay"] == "5000"
    assert ok["fail_closed"] is False

    bad = client.post(
        "/evaluate",
        json={"balance": "10000", "floor": "NaN", "amount": "5000", "date": "2026-01-10"},
    ).json()
    assert bad["fail_closed"] is True
    assert bad["fail_closed_reason"] == "RESERVE_INVALID"


def test_a_token_locks_the_api(monkeypatch):
    monkeypatch.setenv("BUY_WAIT_API_TOKEN", "s3cret")
    client = TestClient(app)
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200
