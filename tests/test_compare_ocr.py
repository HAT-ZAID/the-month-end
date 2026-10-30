"""OCR comparison tool rendering: offline, no model calls."""
from __future__ import annotations

from decimal import Decimal

from code.vision import VisionEvidence
from tools.compare_ocr import _cell, _resolve_amount, render


def _result(rows, backends=("tesseract", "vlm")):
    return {
        "rows": rows,
        "cache": {"enabled": True, "hits": 1, "misses": 2, "writes": 1},
        "backends": list(backends),
    }


def test_render_reports_agreement_and_disagreement():
    rows = [
        {
            "image_id": "image_01",
            "event_id": "event_01",
            "currency": "INR",
            "missing": False,
            "agree": True,
            "results": {
                "tesseract": {"amount": "100", "note": "", "backend": "tesseract"},
                "vlm": {"amount": "100", "note": "", "backend": "vlm"},
            },
        },
        {
            "image_id": "image_02",
            "event_id": "event_02",
            "currency": "INR",
            "missing": False,
            "agree": False,
            "results": {
                "tesseract": {"amount": "200", "note": "", "backend": "tesseract"},
                "vlm": {"amount": "250", "note": "", "backend": "vlm"},
            },
        },
    ]

    text = render(_result(rows))

    assert "| image_01 | event_01 | INR | 100 | 100 | yes |" in text
    assert "| image_02 | event_02 | INR | 200 | 250 | no |" in text
    assert "**Exact agreement:** 1/2" in text
    assert "tesseract 2/2, vlm 2/2" in text


def test_render_marks_unknown_agreement_when_a_reader_failed():
    rows = [
        {
            "image_id": "image_01",
            "event_id": "event_01",
            "currency": "INR",
            "missing": False,
            "agree": None,
            "results": {
                "tesseract": {"amount": "100", "note": "", "backend": "tesseract"},
                "vlm": {"amount": "error", "note": "offline", "backend": "vlm"},
            },
        }
    ]

    text = render(_result(rows))

    assert "| n/a |" in text
    assert "vlm: offline" in text


def test_resolve_amount_prefers_a_validated_structured_value():
    event = {"currency": "INR", "category": "salary", "description": "net pay"}

    evidence = VisionEvidence(
        backend="vlm", raw_text="Net Pay INR 48,500.00", amount=Decimal("48500.00"), currency="INR"
    )
    assert _resolve_amount(evidence, event) == "48500.00"

    conflicting = VisionEvidence(
        backend="vlm", raw_text="Net Pay INR 4,850.00", amount=Decimal("48500"), currency="INR"
    )
    # The text parser wins when the model's own number disagrees with its text.
    assert _resolve_amount(conflicting, event) == "4850.00"

    amount, note = _cell(conflicting, event)
    assert amount == "4850.00" and "rejected" in note


def test_missing_image_still_renders():
    text = render(_result([{"image_id": "image_09", "event_id": "event_09", "missing": True}]))
    assert "**Images compared:** 0 (1 missing on disk)" in text
