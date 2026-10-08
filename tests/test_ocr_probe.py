"""P1 OCR availability routing (TDD): no binary -> flagged reserve, never invented figures."""
from pathlib import Path

import pandas as pd

from code.ocr import (
    _install_dir_from_uninstall,
    configure_tesseract,
    resolve_tesseract_path,
    scan_images,
    verdict_for,
)

REPO = Path(__file__).resolve().parent.parent
MEDIA = REPO / "dataset" / "media" / "images"


def test_absent_binary_routes_to_flagged_reserve():
    v = verdict_for(False)
    assert v["path"] == "reserve"
    assert v["flag"] == "OCR_UNAVAILABLE"
    assert v["amount"] is None  # reserve never invents a figure


def test_scan_inventories_all_sixteen_pngs_as_reserve():
    rows = scan_images(MEDIA, binary_present=False)
    assert len(rows) == 16
    assert all(r["path"] == "reserve" for r in rows)
    assert all(r["flag"] == "OCR_UNAVAILABLE" for r in rows)
    assert all(r["amount"] is None for r in rows)


def test_resolve_tesseract_prefers_explicit_environment_path(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("TESSERACT_CMD", str(exe))
    assert resolve_tesseract_path() == exe


def test_resolve_tesseract_uses_path_when_environment_missing(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr("code.ocr.shutil.which", lambda name: str(exe) if name == "tesseract" else None)
    assert resolve_tesseract_path() == exe


def test_resolve_tesseract_accepts_registry_discovered_candidate(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr("code.ocr.shutil.which", lambda name: None)
    monkeypatch.setattr("code.ocr._registry_tesseract_paths", lambda: [exe])
    assert resolve_tesseract_path() == exe


def test_resolve_tesseract_accepts_drive_discovered_candidate(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr("code.ocr.shutil.which", lambda name: None)
    monkeypatch.setattr("code.ocr._registry_tesseract_paths", lambda: [])
    monkeypatch.setattr("code.ocr._drive_tesseract_paths", lambda: [exe])
    assert resolve_tesseract_path() == exe


def test_uninstall_string_parser_handles_quotes_and_arguments():
    assert _install_dir_from_uninstall('"D:\\Tesseract_OCR\\tesseract-uninstall.exe" /S') == Path("D:/Tesseract_OCR")
    assert _install_dir_from_uninstall("D:\\Tesseract_OCR\\tesseract-uninstall.exe /S") == Path("D:/Tesseract_OCR")


def test_configure_tesseract_sets_pytesseract_command(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("TESSERACT_CMD", str(exe))
    assert configure_tesseract() == exe
    import pytesseract
    assert Path(pytesseract.pytesseract.tesseract_cmd) == exe


def test_main_runtime_configuration_delegates(monkeypatch, tmp_path):
    exe = tmp_path / "tesseract.exe"
    monkeypatch.setattr("code.main.configure_tesseract", lambda: exe)
    from code.main import configure_runtime
    assert configure_runtime() == exe


def test_preprocess_image_grayscales_contrasts_and_boundedly_upscales(tmp_path):
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (400, 200), color=(120, 120, 120)).save(source)

    from code import ocr
    processed = ocr.preprocess_image(source)

    assert processed.mode == "L"
    assert processed.size == (800, 400)
    assert max(processed.size) <= 2400


def test_read_image_text_preprocesses_to_grayscale_with_bounded_upscale(monkeypatch, tmp_path):
    from PIL import Image
    import code.ocr as ocr
    import pytesseract

    image_path = tmp_path / "source.png"
    Image.new("RGB", (100, 50), "white").save(image_path)
    observed = {}

    monkeypatch.setattr(
        pytesseract,
        "image_to_string",
        lambda image: observed.update(mode=image.mode, size=image.size) or "",
        raising=False,
    )

    ocr.read_image_text(image_path)

    assert observed == {"mode": "L", "size": (200, 100)}


def test_all_current_image_event_mappings_resolve_with_family_labels(tmp_path):
    events = pd.read_csv(REPO / "dataset" / "financial_events.csv", dtype=str, keep_default_na=False)
    images = pd.read_csv(REPO / "dataset" / "images.csv", dtype=str, keep_default_na=False)
    blank_events = events[events["amount"] == ""]
    expected = {}
    event_by_id = blank_events.set_index("event_id").to_dict("index")
    for index, row in images.iterrows():
        event = event_by_id[row["related_event_id"]]
        if event["category"] == "salary":
            label = "Net Pay"
        elif event["category"] in {"rent", "utilities", "housing", "healthcare"}:
            label = "Amount Due"
        else:
            label = "Cash Paid"
        expected[row["image_id"]] = f"{label} {event['currency']} {1000 + index}"
        (tmp_path / f"{row['image_id']}.png").write_bytes(b"png")

    resolved, warnings = __import__("code.evidence", fromlist=["resolve_event_amounts"]).resolve_event_amounts(
        images,
        blank_events.to_dict("records"),
        tmp_path,
        reader=lambda path: expected[path.stem],
    )

    assert len(images) == 16
    assert warnings == []
    assert all(str(row["amount"]).strip() for row in resolved)
    assert all(row["amount_evidence_path"] == str(tmp_path / f"{row['amount_evidence_id']}.png") for row in resolved)
