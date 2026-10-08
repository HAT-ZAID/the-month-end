# Task 1 Report: Linked-image OCR amount resolution

## Scope

Implemented the requirements in `task-1-brief.md` within the Task 1 write scope. Existing unrelated worktree changes were left untouched and no model configuration was changed.

## TDD evidence

RED was written before the production changes.

Initial command:

```text
python -m pytest tests/test_hybrid_evidence.py tests/test_ocr_probe.py -q
```

Initial collection output exposed a missing production symbol for the new preprocessing test:

```text
ImportError: cannot import name 'preprocess_image' from 'code.ocr'
1 error in 0.27s
```

After correcting the test import so the feature failure was observed as a test failure rather than collection setup, RED was:

```text
python -m pytest tests/test_hybrid_evidence.py tests/test_ocr_probe.py -q
```

```text
14 failed, 20 passed in 16.15s
```

The failures were the intended missing behaviors: blank linked amounts were not recovered, label-family and number-word validation was absent/incomplete, missing-image provenance was not explicit, OCR preprocessing was absent, provenance fields were absent, and the 16-link inventory did not resolve.

GREEN focused command and output:

```text
python -m pytest tests/test_hybrid_evidence.py tests/test_ocr_probe.py -q
34 passed in 16.94s
```

Full suite command and output:

```text
python -m pytest -q
276 passed, 1 skipped in 41.03s
```

## OCR inventory

Native OCR was available at runtime. Inventory command scanned `dataset/media/images` without emitting OCR text or image contents.

- Inventory count: 16
- Inventory IDs: `image_01 image_02 image_03 image_04 image_05 image_06 image_07 image_08 image_09 image_10 image_11 image_12 image_13 image_14 image_15 image_16`
- Resolved count: 15
- Resolved IDs: `image_01 image_02 image_03 image_04 image_05 image_06 image_07 image_08 image_09 image_10 image_11 image_12 image_13 image_15 image_16`
- Unresolved count: 1
- Unresolved ID: `image_14`
- Unresolved warning: `event_9421:AMOUNT_UNRESOLVED`

`image_14` is retained as blank and fails closed; it is not converted to zero or guessed from weak evidence.

## Implementation summary

- `code/ocr.py`: added supported-currency label extraction, semantic document-family routing, validated English number-word parsing including paise/cents, numeric/word agreement checks, unsupported-currency rejection, grayscale/contrast normalization with a bounded 2x upscale, and raw-path-preserving OCR helpers.
- `code/evidence.py`: treats empty/`NaN` amounts as blank, validates ownership/linkage, handles missing and unavailable evidence explicitly, reconciles multiple linked images by numeric equality, and records image ID, raw path, and OCR text provenance for resolved amounts.
- `code/main.py`: reads blank-sensitive CSV fields with `keep_default_na=False`, so unresolved event amounts cannot be mistaken for populated `nan` strings; unresolved user evidence fails closed before the deterministic oracle.
- `tests/test_hybrid_evidence.py`: added focused extraction, conflict, unsupported/unresolved, provenance, family-routing, number-word, and real 16-mapping tests.
- `tests/test_ocr_probe.py`: added preprocessing and family-label coverage.
- `tests/fixtures/ocr_expected_amounts.json`: records non-secret expected amounts for the 15 validated image mappings.

## Self-review

- Preserved `extract_amount_from_text(text, expected_currency)` and `resolve_event_amounts(images_df, events, image_dir, reader=None, ocr_available=None)` interfaces.
- Kept unresolved evidence fail-closed and never substituted zero for a blank amount.
- Restricted extraction to the five dataset currencies and rejected cross-user/cross-request links.
- Used event category/description for document-family selection; no event ID or organizer-only data is used.
- Preserved exact output-column construction in `main.py`.
- Verified focused and full pytest results after implementation.
- Kept logs/report free of secrets, OCR text, image contents, and PII.

## Concerns

- Runtime resolution depends on native Tesseract availability; when unavailable, the existing `OCR_UNAVAILABLE` path remains conservative.
- The handwritten pharmacy receipt (`image_14`) is intentionally unresolved and will cause the affected evidence path to fail closed.
- OCR remains inherently input-sensitive; the fixture and inventory test cover the current 16 supplied images, while the parser rejects ambiguity rather than guessing.
