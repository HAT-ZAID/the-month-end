from copy import deepcopy
from pathlib import Path
import re

from code.ocr import extract_amount_for_event, read_image_text


def _blank_amount(value) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return not text or text in {"nan", "none", "nat", "null"}


def resolve_event_amounts(images_df, events, image_dir, reader=None, ocr_available=None):
    """Resolve blank event amounts from linked images without inventing values."""
    resolved = deepcopy(events or [])
    event_ids = [str(e.get("event_id", "") or "").strip() for e in resolved]
    duplicate_ids = {event_id for event_id in event_ids if event_id}
    duplicate_ids = {event_id for event_id in duplicate_ids if event_ids.count(event_id) > 1}
    by_event = {
        str(e.get("event_id", "") or "").strip(): e
        for e in resolved
        if str(e.get("event_id", "") or "").strip() and str(e.get("event_id", "") or "").strip() not in duplicate_ids
    }
    warnings = []
    read = reader or read_image_text

    if images_df is None:
        return resolved, warnings
    grouped = {}
    for _, image in images_df.iterrows():
        grouped.setdefault(str(image.get("related_event_id", "") or "").strip(), []).append(image)
    for event_id, image_rows in grouped.items():
        if not event_id or event_id in duplicate_ids:
            warnings.append({"event_id": event_id, "flag": "EVIDENCE_LINK_INVALID"})
            continue
        event = by_event.get(event_id)
        if not event or not _blank_amount(event.get("amount", "")):
            continue
        candidates = []
        invalid_link = False
        missing_image_ids = []
        for image in image_rows:
            image_id = str(image.get("image_id", "") or "").strip()
            image_user = str(image.get("user_id", "") or "").strip()
            event_user = str(event.get("user_id", "") or "").strip()
            image_request = str(image.get("request_id", "") or "").strip()
            event_request = str(event.get("request_id", "") or "").strip()
            has_user_column = "user_id" in image.index
            if (has_user_column and not image_user) or (image_user and event_user and image_user != event_user) or (image_request and event_request and image_request != event_request):
                invalid_link = True
                continue
            if reader is None and ocr_available is not True:
                continue
            image_path = Path(image_dir) / f"{image_id}.png"
            if not image_path.is_file():
                missing_image_ids.append(image_id)
                continue
            try:
                ocr_text = read(image_path)
                parsed = extract_amount_for_event(
                    ocr_text,
                    event.get("currency"),
                    event.get("category"),
                    event.get("description"),
                )
            except Exception:
                ocr_text = ""
                parsed = None
            if parsed is not None:
                candidates.append({
                    "amount": parsed["amount"],
                    "image_id": image_id,
                    "image_path": str(image_path),
                    "ocr_text": ocr_text,
                })
        if invalid_link:
            warnings.append({"event_id": event_id, "flag": "EVIDENCE_LINK_INVALID"})
        elif len({candidate["amount"] for candidate in candidates}) == 1 and candidates:
            candidate = sorted(candidates, key=lambda item: item["image_id"])[0]
            amount = candidate["amount"]
            if str(event.get("category", "")).strip().lower() in {"rent", "housing"} and amount == amount.to_integral_value():
                event["amount"] = str(int(amount))
            elif amount == amount.to_integral_value() and re.search(r"\.\s*00\b", candidate["ocr_text"]):
                event["amount"] = f"{int(amount)}.00"
            else:
                event["amount"] = str(amount)
            event["amount_source"] = "linked_image_ocr"
            event["amount_evidence_id"] = candidate["image_id"]
            event["amount_evidence_path"] = candidate["image_path"]
            event["amount_ocr_text"] = candidate["ocr_text"]
        elif len(candidates) > 1 or not candidates or (reader is None and ocr_available is not True):
            flag = "OCR_UNAVAILABLE" if reader is None and ocr_available is not True else "AMOUNT_UNRESOLVED"
            warning = {"event_id": event_id, "flag": flag}
            if missing_image_ids and not candidates:
                warning["image_ids"] = sorted(missing_image_ids)
            warnings.append(warning)
    return resolved, warnings
