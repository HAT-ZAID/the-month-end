from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from code.evidence import resolve_event_amounts
from code.ocr import configure_tesseract, extract_amount_from_text


def test_extract_amount_from_ocr_text_matches_expected_currency():
    result = extract_amount_from_text("Settlement amount: INR 12,345", "INR")
    assert result == {"amount": Decimal("12345"), "currency": "INR"}


def test_extract_amount_from_ocr_text_rejects_ambiguous_candidates():
    assert extract_amount_from_text("INR 100 or INR 200", "INR") is None


def test_extract_amount_from_ocr_text_uses_document_total_label():
    result = extract_amount_from_text("Subtotal INR 900\nNet Pay INR 4,365,000", "INR")
    assert result == {"amount": Decimal("4365000"), "currency": "INR"}


def test_extract_amount_from_ocr_text_rejects_conflicting_labeled_amounts():
    text = "Net Pay INR 100\nTransferred to IDR 200"
    assert extract_amount_from_text(text, "INR") is None


def test_extract_amount_from_ocr_text_rejects_currency_mismatch():
    assert extract_amount_from_text("Total: EUR 100", "INR") is None


def test_extract_amount_from_ocr_text_rejects_unsupported_currency():
    assert extract_amount_from_text("Net Pay XYZ 100", "INR") is None


def test_resolve_event_amounts_fills_blank_amount_from_linked_image(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_01"}])
    events = [{
        "event_id": "event_01",
        "user_id": "user_01",
        "amount": "",
        "currency": "INR",
    }]

    resolved, warnings = resolve_event_amounts(
        images,
        events,
        tmp_path,
        reader=lambda _: "Amount INR 12,345",
    )

    assert resolved[0]["amount"] == "12345"
    assert warnings == []


def test_resolve_event_amounts_keeps_unresolved_blank_and_warns(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "amount": "", "currency": "USD"}]

    resolved, warnings = resolve_event_amounts(
        images,
        events,
        tmp_path,
        reader=lambda _: "No monetary amount",
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}]


def test_resolve_event_amounts_rejects_cross_user_image_link(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{
        "image_id": "image_01",
        "related_event_id": "event_01",
        "user_id": "user_other",
    }])
    events = [{"event_id": "event_01", "user_id": "user_01", "amount": "", "currency": "INR"}]

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, reader=lambda _: "INR 100"
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{"event_id": "event_01", "flag": "EVIDENCE_LINK_INVALID"}]


def test_resolve_event_amounts_respects_ocr_unavailable(tmp_path):
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "amount": "", "currency": "USD"}]

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, ocr_available=False
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{"event_id": "event_01", "flag": "OCR_UNAVAILABLE"}]


def test_resolve_event_amounts_rejects_conflicting_images_for_one_event(tmp_path):
    for name in ("image_01.png", "image_02.png"):
        (tmp_path / name).write_bytes(b"png")
    images = pd.DataFrame([
        {"image_id": "image_01", "related_event_id": "event_01", "user_id": "user_01"},
        {"image_id": "image_02", "related_event_id": "event_01", "user_id": "user_01"},
    ])
    events = [{"event_id": "event_01", "user_id": "user_01", "amount": "", "currency": "INR"}]
    texts = {"image_01.png": "Total INR 100", "image_02.png": "Total INR 200"}

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, reader=lambda p: texts[p.name]
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}]


def test_extract_amount_from_text_prefers_bill_label_over_other_numeric_lines():
    result = extract_amount_from_text(
        "Subtotal INR 900\nAmount Due: INR 704.05", "INR"
    )
    assert result == {"amount": Decimal("704.05"), "currency": "INR"}


def test_extract_amount_from_text_rejects_same_currency_conflicting_unmarked_labels():
    assert extract_amount_from_text("Net Pay 100\nTransferred 200", "INR") is None


def test_extract_amount_from_text_rejects_numeric_word_disagreement():
    assert extract_amount_from_text(
        "Total Paid: INR 100\nAmount in Words: Two Hundred Rupees Only", "INR"
    ) is None


def test_resolve_event_amounts_marks_missing_image_explicitly(tmp_path):
    images = pd.DataFrame([{"image_id": "image_missing", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "user_id": "user_01", "amount": "", "currency": "INR"}]

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, reader=lambda _: "Total Paid: INR 100"
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{
        "event_id": "event_01",
        "flag": "AMOUNT_UNRESOLVED",
        "image_ids": ["image_missing"],
    }]


def test_resolve_event_amounts_normalizes_same_amounts_across_linked_images(tmp_path):
    for name in ("image_01.png", "image_02.png"):
        (tmp_path / name).write_bytes(b"png")
    images = pd.DataFrame([
        {"image_id": "image_01", "related_event_id": "event_01", "user_id": "user_01"},
        {"image_id": "image_02", "related_event_id": "event_01", "user_id": "user_01"},
    ])
    events = [{"event_id": "event_01", "user_id": "user_01", "amount": "", "currency": "INR"}]
    texts = {"image_01.png": "Total Paid: INR 100", "image_02.png": "Total Paid: INR 100.00"}

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, reader=lambda p: texts[p.name]
    )

    assert resolved[0]["amount"] == "100"
    assert warnings == []
    assert resolved[0]["amount_evidence_id"] == "image_01"
    assert resolved[0]["amount_evidence_path"].endswith("image_01.png")
    assert resolved[0]["amount_ocr_text"] == "Total Paid: INR 100"


def test_all_sixteen_current_image_event_mappings_resolve_or_fail_closed():
    if configure_tesseract() is None:
        pytest.skip("native OCR unavailable")
    repo = Path(__file__).resolve().parent.parent
    images = pd.read_csv(repo / "dataset/images.csv", dtype=str, keep_default_na=False)
    events = pd.read_csv(repo / "dataset/financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
    expected = json.loads((repo / "tests/fixtures/ocr_expected_amounts.json").read_text(encoding="utf-8"))

    resolved, warnings = resolve_event_amounts(
        images, events, repo / "dataset/media/images", ocr_available=True
    )
    by_event = {event["event_id"]: event for event in resolved}
    by_image = dict(zip(images["image_id"], images["related_event_id"]))
    assert set(by_image) == {f"image_{i:02d}" for i in range(1, 17)}
    for image_id, amount in expected.items():
        assert by_event[by_image[image_id]]["amount"] == amount
    assert {warning["event_id"] for warning in warnings} == {by_image["image_14"]}
    assert warnings[0]["flag"] == "AMOUNT_UNRESOLVED"


def test_salary_family_ignores_receipt_total_when_net_pay_is_unique(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_salary"}])
    events = [{
        "event_id": "event_salary",
        "user_id": "user_01",
        "amount": "",
        "currency": "INR",
        "category": "salary",
        "description": "Monthly net salary",
    }]

    resolved, warnings = resolve_event_amounts(
        images,
        events,
        tmp_path,
        reader=lambda _: "Net Pay INR 90\nTotal Paid INR 100",
    )

    assert resolved[0]["amount"] == "90"
    assert warnings == []


def test_unique_number_words_are_validated_for_supported_currency():
    result = extract_amount_from_text(
        "Amount in words: twelve thousand three hundred INR",
        "INR",
    )
    assert result == {"amount": Decimal("12300"), "currency": "INR"}


def test_numeric_and_number_word_disagreement_is_unresolved(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "amount": "", "currency": "INR", "category": "groceries"}]

    resolved, warnings = resolve_event_amounts(
        images,
        events,
        tmp_path,
        reader=lambda _: "Total Paid INR 100\nAmount in words: one hundred one rupees",
    )

    assert resolved[0]["amount"] == ""
    assert warnings == [{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}]


def test_missing_image_is_explicitly_unresolved_with_image_id(tmp_path):
    images = pd.DataFrame([{"image_id": "image_missing", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "amount": "", "currency": "USD"}]

    resolved, warnings = resolve_event_amounts(images, events, tmp_path, reader=lambda _: "USD 100")

    assert resolved[0]["amount"] == ""
    assert warnings == [{
        "event_id": "event_01",
        "flag": "AMOUNT_UNRESOLVED",
        "image_ids": ["image_missing"],
    }]


def test_resolved_amount_preserves_ocr_text_and_raw_path(tmp_path):
    image = tmp_path / "image_01.png"
    image.write_bytes(b"png")
    images = pd.DataFrame([{"image_id": "image_01", "related_event_id": "event_01"}])
    events = [{"event_id": "event_01", "amount": "", "currency": "USD", "category": "transport"}]
    ocr_text = "Cash Paid USD 25"

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, reader=lambda _: ocr_text
    )

    assert warnings == []
    assert resolved[0]["amount"] == "25"
    assert resolved[0]["amount_evidence_path"] == str(image)
    assert resolved[0]["amount_ocr_text"] == ocr_text
