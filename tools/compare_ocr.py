"""Compare local image readers on the linked dataset images.

This is a **manual** tool, not part of the test suite: with ``--backend vlm`` it
talks to the local Qwen3-VL server and can take a while.  It never writes
``output.csv`` and never changes an event; it only reports what each reader sees.

    python -m tools.compare_ocr --backend both
    python -m tools.compare_ocr --backend tesseract --limit 5
    python -m tools.compare_ocr --backend vlm --no-cache --out ocr-vlm.md

Backends: ``tesseract``, ``vlm``, ``docling``, ``auto`` (the chain), or ``both``
(tesseract and vlm side by side, the usual comparison).
"""
from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.ocr import extract_amount_for_event  # noqa: E402
from code.vision import (  # noqa: E402
    VisionEvidence,
    build_backend,
    read_evidence,
    validate_evidence_amount,
)

BACKEND_CHOICES = ("tesseract", "vlm", "docling", "auto", "both")
COMPARE_SET = ("tesseract", "vlm")


def _load_links(root: Path):
    """Return ``[(image_id, event_row)]`` for images linked to a real event."""
    import pandas as pd

    images_path = root / "dataset" / "images.csv"
    events_path = root / "dataset" / "financial_events.csv"
    if not images_path.is_file() or not events_path.is_file():
        return []
    images = pd.read_csv(images_path, dtype=str, keep_default_na=False)
    events = pd.read_csv(events_path, dtype=str, keep_default_na=False).to_dict("records")
    by_event = {str(e.get("event_id", "")).strip(): e for e in events}
    links = []
    for _, image in images.iterrows():
        image_id = str(image.get("image_id", "")).strip()
        event_id = str(image.get("related_event_id", "")).strip()
        if image_id and event_id and event_id in by_event:
            links.append((image_id, by_event[event_id]))
    return links


def _resolve_amount(evidence: VisionEvidence, event, corroborating_texts=()) -> str:
    """The amount this reader supports, using the same trust rule as the pipeline."""
    currency = event.get("currency")
    parsed = validate_evidence_amount(
        evidence,
        expected_currency=currency,
        category=event.get("category"),
        description=event.get("description"),
        corroborating_texts=corroborating_texts,
    )
    if parsed is None and evidence.raw_text:
        parsed = extract_amount_for_event(
            evidence.raw_text, currency, event.get("category"), event.get("description")
        )
    if parsed is None:
        return "—"
    return f"{parsed['amount']}"


def _cell(evidence: VisionEvidence, event, corroborating_texts=()) -> tuple[str, str]:
    """``(amount cell, note)`` for one backend."""
    if evidence.error:
        return "error", evidence.error
    amount = _resolve_amount(evidence, event, corroborating_texts)
    note = "" if amount != "—" else "no labelled amount"
    if evidence.amount is not None and amount != "—":
        try:
            if Decimal(amount).normalize() != evidence.amount.normalize():
                note = "model amount rejected, text used"
        except Exception:
            note = ""
    if corroborating_texts and amount != "—" and note == "":
        note = "certified by a second local reading"
    if evidence.cached:
        note = f"{note} (cached)".strip() if note else "cached"
    return amount, note


def _corroborate(evidence: VisionEvidence, image_path: Path, event, cache, use_cache) -> tuple[str, str]:
    """Report a claimed amount only when another local reader reproduces it.

    This mirrors the pipeline: a vision model may propose a figure, but an
    independent deterministic read of the same image has to confirm it.
    """
    amount, note = _cell(evidence, event)
    if amount != "—" or evidence.amount is None or evidence.backend == "tesseract":
        return amount, note
    other = _run_backend("tesseract", image_path, cache, use_cache)
    if other.error or not other.raw_text:
        return amount, note
    certified = _cell(evidence, event, corroborating_texts=[other.raw_text])
    if certified[0] == "—":
        return amount, note
    return certified


def _run_backend(kind: str, image_path: Path, cache, use_cache: bool):
    backend, reason = build_backend(kind)
    if backend is None:
        return VisionEvidence(backend=kind, error=reason or "unavailable")
    started = time.monotonic()
    evidence = read_evidence(image_path, backend, cache=cache if use_cache else _NullCache())
    if evidence.latency_ms is None:
        evidence.latency_ms = int((time.monotonic() - started) * 1000)
    return evidence


class _NullCache:
    """A cache that never hits and never stores (``--no-cache``)."""

    enabled = False

    def get(self, namespace, payload):
        return None

    def put(self, namespace, payload, value):
        return False

    def stats(self):
        return {"enabled": False}


def compare(root: Path, image_dir: Path, backends, limit=None, use_cache=True) -> dict:
    from code.cache import default_cache

    links = _load_links(root)
    if limit is not None:
        links = links[: max(0, int(limit))]
    cache = default_cache() if use_cache else _NullCache()

    rows = []
    for image_id, event in links:
        image_path = image_dir / f"{image_id}.png"
        if not image_path.is_file():
            rows.append({"image_id": image_id, "event_id": event.get("event_id"), "missing": True})
            continue
        row = {
            "image_id": image_id,
            "event_id": str(event.get("event_id", "")),
            "currency": str(event.get("currency", "") or ""),
            "category": str(event.get("category", "") or ""),
            "missing": False,
            "results": {},
        }
        for kind in backends:
            evidence = _run_backend(kind, image_path, cache, use_cache)
            amount, note = _corroborate(evidence, image_path, event, cache, use_cache)
            row["results"][kind] = {
                "amount": amount,
                "note": note,
                "backend": evidence.backend,
                "latency_ms": evidence.latency_ms,
                "cached": evidence.cached,
            }
        decided = [r["amount"] for r in row["results"].values() if r["amount"] not in {"error", "—"}]
        # Agreement is numeric, not textual: ``723`` and ``723.00`` are the same
        # money, and readers differ only in how they print it.
        values = set()
        for amount in decided:
            try:
                values.add(Decimal(str(amount)))
            except Exception:
                values.add(str(amount))
        # Agreement is only meaningful when at least two readers produced a number.
        row["agree"] = None if len(decided) < 2 else len(values) == 1
        rows.append(row)

    return {"rows": rows, "cache": cache.stats(), "backends": list(backends)}


def render(result: dict) -> str:
    backends = result["backends"]
    header = ["image", "event", "currency"] + backends + ["agree", "notes"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    resolved = {kind: 0 for kind in backends}
    exact = 0
    compared = 0
    missing = 0
    for row in result["rows"]:
        if row["missing"]:
            missing += 1
            continue
        cells = []
        notes = []
        for kind in backends:
            result_row = row["results"][kind]
            amount = result_row["amount"]
            if amount not in {"error", "—"}:
                resolved[kind] += 1
            cells.append(amount)
            if result_row["note"]:
                notes.append(f"{kind}: {result_row['note']}")
        if len(backends) > 1 and all(c not in {"error", "—"} for c in cells):
            compared += 1
            if row["agree"]:
                exact += 1
        lines.append(
            "| "
            + " | ".join(
                [row["image_id"], row["event_id"], row["currency"] or "—"] + cells + [
                    {True: "yes", False: "no", None: "n/a"}[row["agree"]],
                    "; ".join(notes) or "—",
                ]
            )
            + " |"
        )

    total = len([row for row in result["rows"] if not row["missing"]])
    summary = [
        "",
        f"**Images compared:** {total}" + (f" ({missing} missing on disk)" if missing else ""),
        f"**Resolved an amount:** " + ", ".join(f"{kind} {resolved[kind]}/{total}" for kind in backends),
    ]
    if len(backends) > 1:
        summary.append(f"**Exact agreement:** {exact}/{compared}" if compared else "**Exact agreement:** n/a")
    stats = result["cache"]
    if stats.get("enabled"):
        summary.append(
            f"**Cache:** hits {stats.get('hits')}, misses {stats.get('misses')}, writes {stats.get('writes')}"
        )
    else:
        summary.append("**Cache:** disabled")
    return "\n".join(lines + summary) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.compare_ocr",
        description="Compare local OCR/VLM readers on the linked dataset images.",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="repository root")
    parser.add_argument(
        "--image-dir", default=None,
        help="image directory (default <repo>/dataset/media/images)",
    )
    parser.add_argument("--backend", default="both", help="tesseract, vlm, docling, auto, both, or a comma-separated list")
    parser.add_argument("--limit", type=int, default=None, help="only the first N linked images")
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not write the OCR cache")
    parser.add_argument("--out", default=None, help="write the Markdown report here")
    parser.add_argument("--json", default=None, help="also write the raw per-image result as JSON")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    image_dir = Path(args.image_dir) if args.image_dir else root / "dataset" / "media" / "images"
    if not image_dir.is_dir():
        print(f"image directory not found: {image_dir}", file=sys.stderr)
        return 2
    backends = COMPARE_SET if args.backend == "both" else (
        tuple(s.strip() for s in str(args.backend).split(",") if s.strip())
        if "," in args.backend
        else (args.backend,)
    )
    unknown = [name for name in backends if name not in BACKEND_CHOICES]
    if not backends or unknown:
        parser.error(f"--backend must be one or more of {', '.join(BACKEND_CHOICES)}; got {args.backend!r}")

    result = compare(root, image_dir, backends, limit=args.limit, use_cache=not args.no_cache)
    if not result["rows"]:
        print("no linked images found in dataset/images.csv", file=sys.stderr)
        return 2
    text = render(result)
    print(text)
    if args.out:
        out_path = Path(args.out)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out_path}")
    if args.json:
        import json

        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
