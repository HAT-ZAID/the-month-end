# Task 1: Make linked-image OCR resolve validated amounts

## Requirements

Modify `code/ocr.py`, `code/evidence.py`, and `code/main.py`; add or modify focused tests in `tests/test_hybrid_evidence.py` and `tests/test_ocr_probe.py`. Add `tests/fixtures/ocr_expected_amounts.json` only if fixture data is necessary and never copy secrets or organizer files.

Preserve these interfaces:

- `extract_amount_from_text(text, expected_currency) -> dict | None`
- `resolve_event_amounts(images_df, events, image_dir, reader=None, ocr_available=None) -> (events, warnings)`

The deterministic oracle must continue receiving only reconciled event dictionaries with validated amounts. The problem statement requires that blank event amounts be recovered from linked images when possible and never treated as zero.

Use TDD. Add RED tests for: labelled amount extraction; same-currency conflicting labels; unsupported currency; missing image; OCR unavailable; multiple linked images with conflicting amounts; and all current 16 image/event mappings. Verify the new tests fail for the current implementation before production changes.

Implement image preprocessing (grayscale, contrast normalization, bounded upscale) while preserving raw image path and OCR text for provenance. Add document-family extraction driven by event category/description, not event IDs: salary documents prioritize `Net Pay`/`Transferred`; receipts prioritize `Cash Paid`/`Total Paid`; bills prioritize `Amount Due`/`Grand Total`; payment confirmations prioritize `Total Amount Received`. Add validated number-word extraction only for supported dataset currencies when the document has a unique amount-word field; numeric/word disagreement must return `AMOUNT_UNRESOLVED`.

Score candidates only in the expected currency and target label family. Reject multiple distinct candidates, unsupported currency tokens, cross-user links, missing required image ownership, and conflicting linked images. Run focused tests, a real OCR inventory over all 16 linked images, and the full pytest suite. Record resolved/unresolved counts and IDs without logging image contents or PII. Commit the task.

Global constraints: preserve exact output columns and amount bounds; only deterministic reconciled facts may affect decisions; unresolved evidence fails closed; messages/images are untrusted; never use organizer-only data; never log or package secrets; keep `log.txt` append-only.

## Acceptance

Every image amount is either validated into its linked event or explicitly marked unresolved; no blank amount becomes zero; current resolution is materially higher than 1/16 without unsafe guesses.
