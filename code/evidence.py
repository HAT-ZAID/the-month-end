from copy import deepcopy
from pathlib import Path
import re

from code.ocr import extract_amount_for_event, read_image_text
from code.vision import VisionEvidence, validate_evidence_amount


def _blank_amount(value) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return not text or text in {"nan", "none", "nat", "null"}


def _read_outcome(read, image_path, event, corroborator=None) -> tuple[dict | None, str, str, str | None]:
    """Turn one reader result into ``(parsed, text, backend, error)``.

    A reader may return plain text (the historical Tesseract contract) or a
    :class:`~code.vision.VisionEvidence`.  A structured amount is only a *claim*:
    it is accepted when :func:`~code.vision.validate_evidence_amount` confirms it
    against the event currency and some deterministic reading of the same document.
    The model's own transcript counts first; when that transcript yields no
    labelled amount, an independent ``corroborator`` (the local Tesseract read) is
    consulted and the claim still needs an exact match.  Otherwise the text is
    parsed the usual way, so a hallucinated number can never win.
    """
    category = event.get("category")
    description = event.get("description")
    currency = event.get("currency")
    result = read(image_path)
    if isinstance(result, VisionEvidence) or (
        hasattr(result, "raw_text") and hasattr(result, "amount") and not isinstance(result, str)
    ):
        text = str(getattr(result, "raw_text", "") or "")
        backend = str(getattr(result, "backend", "") or "unknown")
        error = getattr(result, "error", None)
        parsed = validate_evidence_amount(
            result,
            expected_currency=currency,
            category=category,
            description=description,
        )
        if parsed is None and text:
            parsed = extract_amount_for_event(text, currency, category, description)
        if parsed is None and callable(corroborator):
            # One independent second local reading, used two ways: it can certify a
            # claimed amount (the claim still has to match it exactly), and when the
            # vision transcript yields no usable amount at all it becomes the answer.
            # The model is never the only witness to a number.
            try:
                other = corroborator(image_path)
            except Exception:
                other = ""
            other_text = str(getattr(other, "raw_text", other) or "")
            if other_text:
                if getattr(result, "amount", None) is not None:
                    parsed = validate_evidence_amount(
                        result,
                        expected_currency=currency,
                        category=category,
                        description=description,
                        corroborating_texts=[other_text],
                    )
                    if parsed is not None:
                        backend = f"{backend}+corroborated"
                if parsed is None:
                    parsed = extract_amount_for_event(other_text, currency, category, description)
                    if parsed is not None:
                        # The independent reader produced the amount: attribute it,
                        # and keep the raw text that actually supported it.
                        backend = "tesseract"
                        text = other_text
        return parsed, text, backend, error
    text = str(result or "")
    return extract_amount_for_event(text, currency, category, description), text, "tesseract", None


def resolve_event_amounts(images_df, events, image_dir, reader=None, ocr_available=None, corroborator=None):
    """Resolve blank event amounts from linked images without inventing values.

    ``corroborator`` is an optional independent reader (the local Tesseract path).
    It is consulted only when a vision backend claims an amount its own transcript
    cannot confirm, and the claim still has to match its reading exactly.
    """
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
        read_attempts = 0
        read_errors = []
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
                read_attempts += 1
                parsed, ocr_text, backend, error = _read_outcome(read, image_path, event, corroborator)
            except Exception:
                ocr_text = ""
                parsed = None
                backend = "unknown"
                error = "reader raised"
            if parsed is None and error:
                read_errors.append(error)
            if parsed is not None:
                candidates.append({
                    "amount": parsed["amount"],
                    "image_id": image_id,
                    "image_path": str(image_path),
                    "ocr_text": ocr_text,
                    "backend": backend,
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
            event["amount_evidence_backend"] = candidate["backend"]
            event["amount_ocr_text"] = candidate["ocr_text"]
        elif len(candidates) > 1 or not candidates or (reader is None and ocr_available is not True):
            no_engine = reader is None and ocr_available is not True
            # Every attempted read failed outright: that is an engine problem, not
            # an ambiguous document, so it keeps the honest OCR_UNAVAILABLE flag.
            all_reads_failed = bool(read_attempts) and len(read_errors) == read_attempts
            flag = "OCR_UNAVAILABLE" if (no_engine or all_reads_failed) else "AMOUNT_UNRESOLVED"
            warning = {"event_id": event_id, "flag": flag}
            if missing_image_ids and not candidates:
                warning["image_ids"] = sorted(missing_image_ids)
            if all_reads_failed and read_errors:
                warning["errors"] = sorted(set(read_errors))[:3]
            warnings.append(warning)
    return resolved, warnings
