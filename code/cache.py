"""Content-addressed disk cache for expensive local work.

Vision and OCR are the slowest parts of the pipeline: a small VLM can take tens
of seconds per image, and the same dataset is re-read on every run.  This module
stores the *result* of such work on disk so a second run on unchanged inputs does
no model work at all.

Design rules:

* **Content-addressed.**  A key is the sha256 of a canonical JSON payload, so the
  same inputs always map to the same file and a changed input can never be served
  a stale answer.
* **Per-path, not deduplicated.**  Callers put the image id or relative path into
  the payload, so two byte-identical images stay two independent entries.  There
  is deliberately no duplicate-collapsing layer.
* **Expiring.**  Every value carries ``stored_at``.  An entry older than
  :data:`DEFAULT_TTL_DAYS` is a miss, and :meth:`DiskCache.purge_expired` deletes
  expired files so the cache cannot grow without bound.
* **Fail-soft.**  A corrupt, unreadable or partially written file is a miss, never
  an exception.  A cache must never break a decision.

Environment:

============================  ==================================================
``BUY_WAIT_CACHE_DIR``        cache root (default ``data/cache``)
``BUY_WAIT_CACHE_DISABLE``    ``1`` disables reads and writes completely
``BUY_WAIT_CACHE_TTL_DAYS``   entry lifetime in days (default 30)
============================  ==================================================

Nothing here is financial logic: a cache hit returns exactly the value the
backend previously produced, and every amount is still re-validated downstream.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR_ENV = "BUY_WAIT_CACHE_DIR"
CACHE_DISABLE_ENV = "BUY_WAIT_CACHE_DISABLE"
CACHE_TTL_ENV = "BUY_WAIT_CACHE_TTL_DAYS"

#: Repository root anchors the default cache so one checkout owns one cache no
#: matter which directory the command is run from.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"
DEFAULT_TTL_DAYS = 30.0
KEY_VERSION = 1

# Namespaces used by the application.  Kept here so tests and purge code do not
# have to repeat string literals.
OCR_NAMESPACE = "ocr"
DECISIONS_NAMESPACE = "decisions"
RAG_NAMESPACE = "rag"


def canonical_json(payload) -> str:
    """Deterministic JSON text for hashing (sorted keys, no incidental spacing)."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def cache_key(namespace: str, payload) -> str:
    """Stable hex key for ``payload`` inside ``namespace``.

    The namespace is part of the digest so two namespaces can never collide even
    when the payloads are identical.
    """
    digest = hashlib.sha256()
    digest.update(f"v{KEY_VERSION}\x00{namespace}\x00".encode("utf-8"))
    digest.update(canonical_json(payload).encode("utf-8"))
    return digest.hexdigest()


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value, default: float) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


class DiskCache:
    """One directory-backed cache with per-entry expiry.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(self, root=None, *, ttl_days=None, enabled=None, env=None, clock=time.time):
        env = os.environ if env is None else env
        self.root = Path(root or env.get(CACHE_DIR_ENV) or DEFAULT_CACHE_DIR)
        if ttl_days is None:
            ttl_days = _as_float(env.get(CACHE_TTL_ENV), DEFAULT_TTL_DAYS)
        self.ttl_seconds = float(ttl_days) * 86400.0
        if enabled is None:
            enabled = not _truthy(env.get(CACHE_DISABLE_ENV))
        self.enabled = bool(enabled)
        self.clock = clock
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.expired = 0

    # -- paths ------------------------------------------------------------
    def path_for(self, namespace: str, payload) -> Path:
        return self.root / namespace / f"{cache_key(namespace, payload)}.json"

    # -- read -------------------------------------------------------------
    def get(self, namespace: str, payload):
        """Return the stored value, or ``None`` on miss/expiry/corruption."""
        if not self.enabled:
            return None
        path = self.path_for(namespace, payload)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            self.misses += 1
            return None
        try:
            record = json.loads(raw)
        except (ValueError, TypeError):
            self._discard(path)
            self.misses += 1
            return None
        if not isinstance(record, dict) or "value" not in record:
            self._discard(path)
            self.misses += 1
            return None
        if self._is_expired(record):
            self._discard(path)
            self.expired += 1
            self.misses += 1
            return None
        self.hits += 1
        return record["value"]

    # -- write ------------------------------------------------------------
    def put(self, namespace: str, payload, value):
        """Store ``value``; returns True when it was written."""
        if not self.enabled:
            return False
        path = self.path_for(namespace, payload)
        record = {
            "namespace": namespace,
            "key": path.stem,
            "stored_at": float(self.clock()),
            "value": value,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps(record, sort_keys=True, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError:
            # A cache that cannot write is still a working pipeline.
            return False
        self.writes += 1
        return True

    # -- maintenance ------------------------------------------------------
    def purge_expired(self, namespace: str | None = None) -> int:
        """Delete expired entries; returns how many files were removed."""
        if not self.enabled:
            return 0
        base = self.root / namespace if namespace else self.root
        if not base.exists():
            return 0
        removed = 0
        for path in base.glob("**/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                self._discard(path)
                removed += 1
                continue
            if not isinstance(record, dict) or self._is_expired(record):
                self._discard(path)
                removed += 1
        return removed

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "root": str(self.root),
            "ttl_days": round(self.ttl_seconds / 86400.0, 3) if self.ttl_seconds else None,
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "expired": self.expired,
        }

    # -- internals --------------------------------------------------------
    def _is_expired(self, record: dict) -> bool:
        try:
            stored_at = float(record.get("stored_at"))
        except (TypeError, ValueError):
            return True
        if self.ttl_seconds <= 0:
            return False
        return (float(self.clock()) - stored_at) > self.ttl_seconds

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass


def default_cache(**kwargs) -> DiskCache:
    """A cache configured from the environment, for callers without one."""
    return DiskCache(**kwargs)


def main(argv=None) -> int:
    """``python -m code.cache``: inspect or sweep the local cache.

    Sweeping is how expired entries leave the disk when nobody reads them again;
    reads already treat an expired entry as a miss and delete it.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m code.cache",
        description="Inspect or sweep the local OCR/VLM cache.",
    )
    parser.add_argument("--root", default=None, help="cache directory (default: BUY_WAIT_CACHE_DIR or data/cache)")
    parser.add_argument("--ttl-days", type=float, default=None, help="entry lifetime in days")
    parser.add_argument("--namespace", default=None, help="limit --purge to one namespace")
    parser.add_argument("--purge", action="store_true", help="delete expired entries")
    parser.add_argument("--stats", action="store_true", help="print cache settings and counters")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    cache = DiskCache(args.root, ttl_days=args.ttl_days)
    report = {"root": str(cache.root), "enabled": cache.enabled, "ttl_days": round(cache.ttl_seconds / 86400.0, 3)}
    if not cache.enabled:
        report["note"] = f"{CACHE_DISABLE_ENV} disables the cache; nothing to sweep"
    elif args.purge:
        report["purged"] = cache.purge_expired(args.namespace)
    else:
        report["entries"] = sum(1 for _ in cache.root.glob("**/*.json")) if cache.root.exists() else 0
    if args.stats:
        report["stats"] = cache.stats()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


__all__ = [
    "CACHE_DIR_ENV",
    "CACHE_DISABLE_ENV",
    "CACHE_TTL_ENV",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_TTL_DAYS",
    "DECISIONS_NAMESPACE",
    "DiskCache",
    "KEY_VERSION",
    "OCR_NAMESPACE",
    "RAG_NAMESPACE",
    "REPO_ROOT",
    "cache_key",
    "canonical_json",
    "default_cache",
    "main",
]
