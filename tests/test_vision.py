"""Vision backends: parsing, local-only guard, fallback chain, cache reuse."""
from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest

from code.cache import DiskCache
from code.vision import (
    _backend_model,
    ChainedBackend,
    DoclingBackend,
    QwenVLBackend,
    TesseractBackend,
    VisionEvidence,
    build_backend,
    parse_vlm_payload,
    read_evidence,
    validate_evidence_amount,
)


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _image(tmp_path, name="image_01.png", payload=b"fake-png-bytes"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# Parsing the model's answer
# ---------------------------------------------------------------------------


def test_parse_vlm_payload_normalizes_values():
    evidence = parse_vlm_payload(
        json.dumps(
            {
                "raw_text": "Total 120.00",
                "amount": "1,200.50",
                "currency": "inr",
                "date": "2026-01-05",
                "merchant": "Corner Shop",
                "confidence": 0.87,
            }
        )
    )

    assert evidence.ok
    assert evidence.amount == Decimal("1200.50")
    assert evidence.currency == "INR"
    assert evidence.merchant == "Corner Shop"
    assert evidence.confidence == 0.87


def test_parse_vlm_payload_accepts_fenced_json():
    text = '```json\n{"raw_text": "x", "amount": "10", "currency": "USD"}\n```'
    assert parse_vlm_payload(text).amount == Decimal("10")


@pytest.mark.parametrize(
    "text",
    ["no json at all", "{oops", "[1, 2, 3]", "null"],
)
def test_parse_vlm_payload_never_raises(text):
    evidence = parse_vlm_payload(text)
    assert evidence.error is not None
    assert evidence.amount is None


@pytest.mark.parametrize("amount", ["-5", "0", "10000000000000", "abc"])
def test_parse_vlm_payload_rejects_unusable_amounts(amount):
    evidence = parse_vlm_payload(json.dumps({"amount": amount}))
    assert evidence.amount is None
    assert evidence.error is None  # the read worked, the number is simply unusable


def test_parse_vlm_payload_drops_implausible_currency_and_confidence():
    evidence = parse_vlm_payload(json.dumps({"currency": "RUPEE", "confidence": 4}))
    assert evidence.currency is None
    assert evidence.confidence is None


# ---------------------------------------------------------------------------
# Local-only guard
# ---------------------------------------------------------------------------


def test_non_loopback_base_url_is_refused_without_opt_in(tmp_path):
    backend = QwenVLBackend(base_url="https://api.example.com", allow_remote=False)
    evidence = backend.read(_image(tmp_path))

    assert evidence.error and "non-loopback" in evidence.error
    assert evidence.raw_text == ""


def test_remote_base_url_allowed_when_explicitly_opted_in(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        body = json.dumps({"response": json.dumps({"raw_text": "Total 10", "amount": "10"})})
        return FakeResponse(body.encode())

    monkeypatch.setattr("code.vision.urllib.request.urlopen", fake_urlopen)
    backend = QwenVLBackend(base_url="https://api.example.com", allow_remote=True, model="m")

    evidence = backend.read(_image(tmp_path))

    assert evidence.ok and evidence.amount == Decimal("10")
    assert captured["url"] == "https://api.example.com/api/generate"


def test_connection_failure_returns_an_error_instead_of_raising(tmp_path, monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("code.vision.urllib.request.urlopen", boom)
    evidence = QwenVLBackend(base_url="http://127.0.0.1:11434").read(_image(tmp_path))

    assert evidence.error and "connection failed" in evidence.error


def test_http_error_is_reported(tmp_path, monkeypatch):
    import urllib.error

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)

    monkeypatch.setattr("code.vision.urllib.request.urlopen", boom)
    evidence = QwenVLBackend(base_url="http://127.0.0.1:11434").read(_image(tmp_path))

    assert evidence.error == "HTTP 500"


def test_vlm_request_is_local_and_deterministic(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"response": '{"raw_text": "", "amount": ""}'}).encode())

    monkeypatch.setattr("code.vision.urllib.request.urlopen", fake_urlopen)
    backend = QwenVLBackend(base_url="http://localhost:11434", model="qwen3-vl:2b", timeout=7)
    backend.read(_image(tmp_path, payload=b"\x89PNG"))

    payload = captured["payload"]
    assert payload["model"] == "qwen3-vl:2b"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["options"]["temperature"] == 0
    assert payload["images"] and isinstance(payload["images"][0], str)
    assert "untrusted" in payload["prompt"]
    assert captured["timeout"] == 7


# ---------------------------------------------------------------------------
# Docling
# ---------------------------------------------------------------------------


def test_docling_reports_a_clean_error_when_not_configured(tmp_path):
    evidence = DoclingBackend(python="", env={}).read(_image(tmp_path))

    assert evidence.error and "BUY_WAIT_DOCLING_PYTHON" in evidence.error


def test_docling_success_returns_text(tmp_path, monkeypatch):
    interpreter = tmp_path / "python.exe"
    interpreter.write_text("", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = json.dumps({"text": "# Receipt\n\nTotal 42"}) + "\n"
        stderr = ""

    monkeypatch.setattr("code.vision.subprocess.run", lambda *a, **k: Completed())
    evidence = DoclingBackend(python=str(interpreter), env={}).read(_image(tmp_path))

    assert evidence.ok
    assert "Total 42" in evidence.raw_text
    assert evidence.backend == "docling"


def test_docling_failure_is_reported_not_raised(tmp_path, monkeypatch):
    interpreter = tmp_path / "python.exe"
    interpreter.write_text("", encoding="utf-8")

    class Completed:
        returncode = 2
        stdout = ""
        stderr = "ModuleNotFoundError: No module named 'docling'"

    monkeypatch.setattr("code.vision.subprocess.run", lambda *a, **k: Completed())
    evidence = DoclingBackend(python=str(interpreter), env={}).read(_image(tmp_path))

    assert evidence.error and "docling" in evidence.error


# ---------------------------------------------------------------------------
# Fallback chain and factory
# ---------------------------------------------------------------------------


class StubBackend:
    def __init__(self, name, evidence):
        self.name = name
        self._evidence = evidence
        self.calls = 0

    def read(self, image_path):
        self.calls += 1
        return self._evidence


def test_chain_falls_through_to_the_first_working_backend(tmp_path):
    broken = StubBackend("vlm", VisionEvidence(backend="vlm", error="offline"))
    working = StubBackend("tesseract", VisionEvidence(backend="tesseract", raw_text="Total 9"))
    chain = ChainedBackend([broken, working])

    evidence = chain.read(_image(tmp_path))

    assert evidence.raw_text == "Total 9" and evidence.backend == "tesseract"
    assert broken.calls == 1 and working.calls == 1


def test_chain_reports_every_failure_when_nothing_works(tmp_path):
    chain = ChainedBackend(
        [
            StubBackend("vlm", VisionEvidence(backend="vlm", error="offline")),
            StubBackend("tesseract", VisionEvidence(backend="tesseract", error="missing binary")),
        ]
    )

    evidence = chain.read(_image(tmp_path))

    assert evidence.error and "vlm: offline" in evidence.error and "tesseract: missing binary" in evidence.error


def test_auto_backend_includes_docling_only_when_configured(monkeypatch, tmp_path):
    env = {}
    backend, reason = build_backend("auto", env=env)
    assert reason is None
    assert [b.name for b in backend.backends] == ["vlm", "tesseract"]

    interpreter = tmp_path / "python.exe"
    interpreter.write_text("", encoding="utf-8")
    backend, _ = build_backend("auto", env={"BUY_WAIT_DOCLING_PYTHON": str(interpreter)})
    assert [b.name for b in backend.backends] == ["vlm", "docling", "tesseract"]


def test_explicit_backends_and_unknown_kind():
    assert isinstance(build_backend("tesseract", env={})[0], TesseractBackend)
    assert isinstance(build_backend("vlm", env={})[0], QwenVLBackend)
    backend, reason = build_backend("magic", env={})
    assert backend is None and "expected one of" in reason


def test_environment_selects_the_backend():
    backend, _ = build_backend(env={"BUY_WAIT_OCR_BACKEND": "tesseract"})
    assert isinstance(backend, TesseractBackend)


# ---------------------------------------------------------------------------
# Cache reuse
# ---------------------------------------------------------------------------


def test_second_read_is_served_from_cache_without_touching_the_backend(tmp_path):
    clock = Clock()
    cache = DiskCache(tmp_path / "cache", ttl_days=30, clock=clock)
    stub = StubBackend("vlm", VisionEvidence(backend="vlm", raw_text="Total 5", amount=Decimal("5")))
    image = _image(tmp_path)

    first = read_evidence(image, stub, cache=cache)
    second = read_evidence(image, stub, cache=cache)

    assert stub.calls == 1
    assert first.amount == second.amount == Decimal("5")
    assert second.cached is True and first.cached is False


def test_cache_expires_and_the_backend_is_consulted_again(tmp_path):
    clock = Clock()
    cache = DiskCache(tmp_path / "cache", ttl_days=1, clock=clock)
    stub = StubBackend("vlm", VisionEvidence(backend="vlm", raw_text="x"))
    image = _image(tmp_path)

    read_evidence(image, stub, cache=cache)
    clock.advance(2 * 86400)
    read_evidence(image, stub, cache=cache)

    assert stub.calls == 2


def test_changed_bytes_invalidate_only_that_image(tmp_path):
    cache = DiskCache(tmp_path / "cache", ttl_days=30, clock=Clock())
    stub = StubBackend("vlm", VisionEvidence(backend="vlm", raw_text="x"))
    first = _image(tmp_path, "image_01.png", b"one")
    second = _image(tmp_path, "image_02.png", b"two")

    read_evidence(first, stub, cache=cache)
    read_evidence(second, stub, cache=cache)
    assert stub.calls == 2

    read_evidence(first, stub, cache=cache)
    assert stub.calls == 2, "unchanged image must not be re-read"

    first.write_bytes(b"changed")
    read_evidence(first, stub, cache=cache)
    assert stub.calls == 3


def test_failed_reads_are_not_cached(tmp_path):
    cache = DiskCache(tmp_path / "cache", ttl_days=30, clock=Clock())
    stub = StubBackend("vlm", VisionEvidence(backend="vlm", error="offline"))
    image = _image(tmp_path)

    read_evidence(image, stub, cache=cache)
    read_evidence(image, stub, cache=cache)

    assert stub.calls == 2


# ---------------------------------------------------------------------------
# Amount trust boundary
# ---------------------------------------------------------------------------


def test_structured_amount_is_accepted_when_it_agrees_with_the_text():
    evidence = VisionEvidence(
        backend="vlm", raw_text="Net Pay: 48,500.00", amount=Decimal("48500.00"), currency="INR"
    )

    trusted = validate_evidence_amount(evidence, expected_currency="INR", category="salary")

    assert trusted == {"amount": Decimal("48500.00"), "currency": "INR"}


def test_structured_amount_is_rejected_when_it_disagrees_with_the_text():
    evidence = VisionEvidence(
        backend="vlm", raw_text="Net Pay: 4,850.00", amount=Decimal("48500"), currency="INR"
    )

    assert validate_evidence_amount(evidence, expected_currency="INR", category="salary") is None


def test_structured_amount_is_rejected_on_currency_mismatch():
    evidence = VisionEvidence(backend="vlm", raw_text="", amount=Decimal("10"), currency="USD")

    assert validate_evidence_amount(evidence, expected_currency="INR") is None


def test_structured_amount_is_rejected_without_text_to_corroborate_it():
    # An unreadable image is exactly where a small model likes to invent a figure.
    evidence = VisionEvidence(backend="vlm", raw_text="", amount=Decimal("5000"), currency="")

    assert validate_evidence_amount(evidence, expected_currency="INR") is None


def test_structured_amount_is_rejected_when_the_parser_reads_no_amount():
    evidence = VisionEvidence(
        backend="vlm", raw_text="no totals on this page", amount=Decimal("5000"), currency="INR"
    )

    assert validate_evidence_amount(evidence, expected_currency="INR") is None


def test_cache_key_includes_the_model_of_every_chained_backend():
    first = ChainedBackend([QwenVLBackend(env={"BUY_WAIT_VLM_MODEL": "qwen3-vl:2b"}), TesseractBackend()])
    second = ChainedBackend([QwenVLBackend(env={"BUY_WAIT_VLM_MODEL": "qwen3-vl:7b"}), TesseractBackend()])

    assert _backend_model(first) != _backend_model(second)
    assert "qwen3-vl:2b" in _backend_model(first)
    assert _backend_model(TesseractBackend()) == ""


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1"), None])
def test_structured_amount_must_be_positive(amount):
    evidence = VisionEvidence(backend="vlm", amount=amount, currency="INR")
    assert validate_evidence_amount(evidence, expected_currency="INR") is None
