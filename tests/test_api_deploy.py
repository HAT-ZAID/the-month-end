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


def test_explain_accepts_prose_that_only_repeats_kernel_figures(monkeypatch):
    from deploy import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_explain",
        lambda row: ("Your safe amount is 21843.925 and the decision is not_affordable.", "kilo/test"),
    )
    client = TestClient(app)
    body = client.get("/explain/request_01").json()
    assert body["explainer"] == "kilo-free"
    assert "21843.925" in body["explanation"]


def test_explain_rejects_a_figure_the_kernel_never_produced(monkeypatch):
    from deploy import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_explain",
        lambda row: ("You can safely pay 99999 today.", "kilo/test"),
    )
    client = TestClient(app)
    body = client.get("/explain/request_01").json()
    assert body["explainer"] is None
    assert "99999" not in body["explanation"]
    assert body["explanation"] == body["decision_explanation"]
    assert body["rejected_figures"] == ["99999"]


def test_explain_reports_an_unavailable_model(monkeypatch):
    from deploy import api as api_mod

    def boom(row):
        raise TimeoutError("slow")

    monkeypatch.setattr(api_mod, "_explain", boom)
    client = TestClient(app)
    response = client.get("/explain/request_01")
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_explain_needs_the_token(monkeypatch):
    monkeypatch.setenv("BUY_WAIT_API_TOKEN", "s3cret")
    client = TestClient(app)
    assert client.get("/explain/request_01").status_code == 401
