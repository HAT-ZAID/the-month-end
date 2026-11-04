"""The committed image-amount pin is what makes output.csv reproducible."""
import pandas as pd

from code.evidence import load_pinned_amounts, resolve_event_amounts


def test_a_pinned_amount_is_used_without_any_reader(tmp_path):
    events = [{"event_id": "e1", "user_id": "u1", "amount": ""}]
    images = pd.DataFrame(
        [{"image_id": "i1", "related_event_id": "e1", "user_id": "u1"}]
    )

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, pinned={"e1": "12.50"}
    )

    assert resolved[0]["amount"] == "12.50"
    assert resolved[0]["amount_source"] == "pinned_evidence"
    assert warnings == []


def test_an_unpinned_row_still_reports_unavailable_evidence(tmp_path):
    events = [{"event_id": "e2", "user_id": "u1", "amount": ""}]
    images = pd.DataFrame(
        [{"image_id": "i2", "related_event_id": "e2", "user_id": "u1"}]
    )

    resolved, warnings = resolve_event_amounts(
        images, events, tmp_path, pinned={"e1": "12.50"}, ocr_available=False
    )

    assert resolved[0]["amount"] == ""
    assert warnings and warnings[0]["event_id"] == "e2"


def test_the_committed_pin_covers_the_blank_fixture_amounts():
    pinned = load_pinned_amounts()
    assert pinned, "data/image_amounts.json is missing or empty"
    assert pinned["event_253"] == "4365000"
