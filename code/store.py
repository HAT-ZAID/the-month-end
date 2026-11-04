"""SQLite canonical store for the agentic layer (Task 5B).

The CSV files under ``dataset/`` stay the source of truth on disk.  This module
loads them into ``data/app.db`` so the agent can query *structured* facts with
parameterized SQL (numbers, dates, statuses) while the vector index only ever
serves citation text.  Nothing here calls a model or the network, and the CSVs
are never written to.

Layout
------

``requests``, ``profiles``, ``events``, ``payment_options`` and ``messages``
mirror the dataset columns as TEXT (the dataset is text; parsing is the kernel's
job, not the store's).  ``evidence_chunks`` holds one citable row per message,
per event, and per image *only when OCR text is already available* -- no OCR text
is invented.  ``evidence_fts`` is an FTS5 index over the chunk text, and
``traces`` is an optional one-line summary per agent run (the full trace stays in
JSON).

Determinism
-----------

* the build is idempotent: an up-to-date database is left untouched unless
  ``rebuild=True``;
* chunk ids are derived from dataset ids (``message:<id>``, ``event:<id>``,
  ``image:<id>``), so a rebuild produces identical rows;
* every query orders explicitly, and no query interpolates a value.

Usage::

    with LocalStore() as store:
        store.build()               # idempotent
        hits = store.search_fts("salary delayed", user_id="<user_id>")

    python -m code.store --rebuild  # rebuild data/app.db from the CSVs
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from code.schema import stable_json
from code.trace import sanitize

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "app.db"

#: Bumped whenever the table layout changes; ``user_version`` guards rebuilds.
SCHEMA_VERSION = 3

#: Canonical text table definitions, in creation order.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "requests": (
        "request_id",
        "user_id",
        "request_date",
        "request_type",
        "requested_amount",
        "desired_completion_date",
        "allows_partial_payment",
        "request_text",
    ),
    "profiles": (
        "user_id",
        "home_currency",
        "current_available_balance",
        "minimum_balance_to_keep",
        "financial_priorities",
        "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider",
        "max_installment_months",
    ),
    "events": (
        "event_id",
        "user_id",
        "event_type",
        "description",
        "category",
        "direction",
        "amount",
        "currency",
        "event_date",
        "settlement_date",
        "status",
        "linked_event_id",
        "flexibility",
        "minimum_allowed_amount",
    ),
    "payment_options": (
        "payment_option_id",
        "request_id",
        "payment_method",
        "payment_amount",
        "number_of_payments",
        "first_payment_date",
        "payment_frequency_days",
        "financing_fee",
        "total_payable_amount",
    ),
    "messages": (
        "message_id",
        "user_id",
        "request_id",
        "related_event_id",
        "sent_at",
        "source_type",
        "message_text",
    ),
    "evidence_chunks": (
        "chunk_id",
        "source_type",
        "source_id",
        "user_id",
        "request_id",
        "text",
        "metadata_json",
    ),
    "traces": (
        "trace_id",
        "run_id",
        "request_id",
        "provider",
        "model",
        "fallback_reason",
        "summary_json",
    ),
}

PRIMARY_KEYS: dict[str, str] = {
    "requests": "request_id",
    "profiles": "user_id",
    "events": "event_id",
    "payment_options": "payment_option_id",
    "messages": "message_id",
    "evidence_chunks": "chunk_id",
    "traces": "trace_id",
}

#: Dataset CSV backing each mirrored table (frames are read with dtype=str).
CSV_SOURCES: dict[str, str] = {
    "requests": "requests.csv",
    "profiles": "financial_profiles.csv",
    "events": "financial_events.csv",
    "payment_options": "request_payment_options.csv",
    "messages": "messages.csv",
}

MAX_SEARCH_TERMS = 12
#: Hard bound for ``chunks()`` (the vector index reads every chunk of a scope).
MAX_CHUNK_LIMIT = 100000


class StoreError(RuntimeError):
    """The local store could not be built or read."""


def _text(value) -> str:
    """CSV cell -> TEXT column (NaN/None become empty strings, never 'nan')."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    text = str(value)
    return text if text != "nan" else ""


def _columns_sql(columns) -> str:
    return ", ".join('"' + name + '" TEXT NOT NULL DEFAULT ' + "''" for name in columns)


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _search_terms(query) -> list[str]:
    """Lowercase alphanumeric search terms, de-duplicated in first-seen order."""
    seen: dict[str, None] = {}
    for token in re.split(r"[^0-9a-z]+", str(query or "").lower()):
        if len(token) < 2:
            continue
        seen.setdefault(token, None)
        if len(seen) >= MAX_SEARCH_TERMS:
            break
    return list(seen)


def _fts_match_expression(terms) -> str:
    """Quote every term so FTS5 operators in user text cannot change the query."""
    return " OR ".join('"' + term.replace('"', "") + '"' for term in terms)


def load_ocr_texts(path) -> dict[str, str]:
    """Read an optional ``{image_id: text}`` JSON map of already-extracted OCR.

    Absent or malformed files yield an empty mapping: the store never fabricates
    OCR text and never runs an OCR engine.
    """
    if path is None:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in payload.items()
        if str(value or "").strip()
    }


class LocalStore:
    """Context manager around ``data/app.db`` (schema + parameterized reads)."""

    def __init__(self, db_path=None, repo_root=None, *, ocr_path=None):
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        self.db_path = Path(db_path) if db_path is not None else self.repo_root / "data" / "app.db"
        self.ocr_path = Path(ocr_path) if ocr_path is not None else self.repo_root / "data" / "ocr_text.json"
        self._connection: sqlite3.Connection | None = None
        self._fts_ok: bool | None = None

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "LocalStore":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(str(self.db_path))
            except (OSError, sqlite3.Error) as exc:
                raise StoreError(f"cannot open {self.db_path}: {exc}") from exc
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = OFF")
            self._connection = connection
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        return self.connect()

    @property
    def exists(self) -> bool:
        return self.db_path.is_file()

    # -- schema -----------------------------------------------------------

    def _user_version(self) -> int:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    def _table_names(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def is_current(self) -> bool:
        """True when the on-disk database matches this module's schema and data."""
        if not self.exists:
            return False
        try:
            if self._user_version() != SCHEMA_VERSION:
                return False
            if not set(TABLE_COLUMNS) <= self._table_names():
                return False
            counts = self.counts()
        except (sqlite3.Error, StoreError):
            return False
        # A zero-row build means a previous run died before loading; rebuild.
        return counts.get("requests", 0) > 0 and counts.get("evidence_chunks", 0) > 0

    def _create_schema(self) -> None:
        connection = self.connection
        for table, columns in TABLE_COLUMNS.items():
            key = PRIMARY_KEYS[table]
            connection.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" ('
                f'"{key}" TEXT PRIMARY KEY NOT NULL, '
                + _columns_sql([name for name in columns if name != key])
                + ")"
            )
        # FTS5 over the chunk text.  ``chunk_id`` is UNINDEXED so a hit maps back
        # to the canonical row; the canonical text of record stays in SQLite.
        try:
            connection.execute(
                'CREATE VIRTUAL TABLE IF NOT EXISTS "evidence_fts" '
                "USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')"
            )
            self._fts_ok = True
        except sqlite3.Error:
            # No FTS5 in this SQLite build: LIKE search keeps the store usable.
            self._fts_ok = False
        connection.commit()

    # -- build ------------------------------------------------------------

    def _frames(self):
        from code.tools import DatasetBundle

        bundle = DatasetBundle.load(self.repo_root)
        return {
            "requests": bundle.requests,
            "profiles": bundle.profiles,
            "events": bundle.events,
            "payment_options": bundle.options,
            "messages": bundle.messages,
            "images": bundle.images,
        }

    def build(self, *, rebuild: bool = False, frames=None) -> dict:
        """Load the dataset into the database; idempotent unless ``rebuild``.

        Returns ``{"status": "reused"|"built"|"rebuilt", "rows": {...}}``.
        """
        if not rebuild and self.is_current():
            return {"status": "reused", "rows": self.counts()}

        frames = self._frames() if frames is None else frames
        connection = self.connection
        rows = {
            table: self._dataset_rows(frames, table)
            for table in TABLE_COLUMNS
            if table in CSV_SOURCES
        }
        chunks = self._chunks(frames)
        rows["evidence_chunks"] = chunks

        with connection:  # one transaction: all-or-nothing, and fast
            for table in TABLE_COLUMNS:
                connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            connection.execute('DROP TABLE IF EXISTS "evidence_fts"')
            self._create_schema()
            for table, columns in TABLE_COLUMNS.items():
                payload = rows.get(table) or []
                if not payload:
                    continue
                names = ", ".join('"' + name + '"' for name in columns)
                connection.executemany(
                    f'INSERT INTO "{table}" ({names}) VALUES ({_placeholders(len(columns))})',
                    [tuple(row) for row in payload],
                )
            if self._fts_ok:
                connection.executemany(
                    'INSERT INTO "evidence_fts" (chunk_id, text) VALUES (?, ?)',
                    [(row[0], row[5]) for row in chunks if row[5]],
                )
            # Written last: a crash mid-build leaves user_version at the old
            # value, so the next run rebuilds instead of trusting a partial DB.
            connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        status = "rebuilt" if rebuild else "built"
        return {"status": status, "rows": self.counts()}

    def _dataset_rows(self, frames, table: str) -> list[tuple]:
        columns = TABLE_COLUMNS[table]
        records = _records(frames.get(table))
        return [tuple(_text(record.get(name)) for name in columns) for record in records]

    def _chunks(self, frames) -> list[tuple]:
        """One deterministic, citable chunk per message, event and known image."""
        ocr_texts = load_ocr_texts(self.ocr_path)
        chunks: list[tuple] = []

        message_columns = TABLE_COLUMNS["messages"]
        for record in _records(frames.get("messages")):
            values = {name: _text(record.get(name)) for name in message_columns}
            message_id = values["message_id"]
            if not message_id:
                continue
            metadata = {
                "sent_at": values["sent_at"],
                "source_type": values["source_type"],
                "related_event_id": values["related_event_id"],
            }
            chunks.append(
                (
                    f"message:{message_id}",
                    "message",
                    message_id,
                    values["user_id"],
                    values["request_id"],
                    values["message_text"],
                    stable_json(metadata),
                )
            )

        event_columns = TABLE_COLUMNS["events"]
        for record in _records(frames.get("events")):
            values = {name: _text(record.get(name)) for name in event_columns}
            event_id = values["event_id"]
            if not event_id:
                continue
            metadata = {
                "event_type": values["event_type"],
                "category": values["category"],
                "direction": values["direction"],
                "amount": values["amount"],
                "currency": values["currency"],
                "event_date": values["event_date"],
                "settlement_date": values["settlement_date"],
                "status": values["status"],
                "flexibility": values["flexibility"],
            }
            chunks.append(
                (
                    f"event:{event_id}",
                    "event",
                    event_id,
                    values["user_id"],
                    "",
                    values["description"],
                    stable_json(metadata),
                )
            )

        images = frames.get("images")
        for record in _records(images):
            image_id = _text(record.get("image_id"))
            text = ocr_texts.get(image_id, "")
            if not image_id or not text:
                # No OCR text on hand -> no chunk.  Inventing one would fabricate
                # evidence, which the decision rules forbid.
                continue
            metadata = {"ocr_available": True}
            chunks.append(
                (
                    f"image:{image_id}",
                    "image",
                    image_id,
                    _text(record.get("user_id")),
                    _text(record.get("request_id")),
                    text,
                    stable_json(metadata),
                )
            )

        chunks.sort(key=lambda chunk: chunk[0])
        return chunks

    # -- reads ------------------------------------------------------------

    def counts(self) -> dict:
        connection = self.connection
        result = {}
        for table in TABLE_COLUMNS:
            try:
                row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            except sqlite3.Error:
                result[table] = 0
                continue
            result[table] = int(row[0]) if row is not None else 0
        return result

    def search_fts(self, query, *, user_id=None, request_id=None, limit: int = 8) -> list[dict]:
        """Full-text search over ``evidence_chunks``; returns citation rows."""
        terms = _search_terms(query)
        if not terms:
            return []
        try:
            capped = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            capped = 8
        if self._fts_available():
            hits = self._search_fts5(terms, user_id=user_id, request_id=request_id, limit=capped)
            if hits:
                return hits
        return self._search_like(terms, user_id=user_id, request_id=request_id, limit=capped)

    def _fts_available(self) -> bool:
        if self._fts_ok is None:
            self._fts_ok = "evidence_fts" in self._table_names()
        return self._fts_ok

    def _search_fts5(self, terms, *, user_id, request_id, limit) -> list[dict]:
        clauses = []
        params: list = [_fts_match_expression(terms)]
        if user_id:
            clauses.append("c.user_id = ?")
            params.append(str(user_id))
        if request_id:
            # Event chunks (and most messages) carry no request id; they are
            # user-level evidence and must survive a request-scoped query.
            clauses.append("(c.request_id = ? OR c.request_id = '')")
            params.append(str(request_id))
        where = " AND ".join(["evidence_fts MATCH ?"] + clauses)
        sql = (
            "SELECT c.chunk_id, c.source_type, c.source_id, c.user_id, c.request_id, "
            "c.text, c.metadata_json, bm25(evidence_fts) AS score "
            "FROM evidence_fts JOIN evidence_chunks c ON c.chunk_id = evidence_fts.chunk_id "
            f"WHERE {where} ORDER BY score ASC, c.chunk_id ASC LIMIT ?"
        )
        params.append(capped_int(limit))
        try:
            rows = self.connection.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        return [_citation(row, "fts", row["score"]) for row in rows]

    def _search_like(self, terms, *, user_id, request_id, limit) -> list[dict]:
        clauses = ["(" + " OR ".join("c.text LIKE ?" for _ in terms) + ")"]
        params: list = [f"%{term}%" for term in terms]
        if user_id:
            clauses.append("c.user_id = ?")
            params.append(str(user_id))
        if request_id:
            clauses.append("(c.request_id = ? OR c.request_id = '')")
            params.append(str(request_id))
        sql = (
            "SELECT c.chunk_id, c.source_type, c.source_id, c.user_id, c.request_id, "
            "c.text, c.metadata_json "
            "FROM evidence_chunks c "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.chunk_id ASC LIMIT ?"
        )
        params.append(capped_int(limit))
        try:
            rows = self.connection.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        hits = []
        for row in rows:
            text = str(row["text"] or "").lower()
            score = float(sum(1 for term in terms if term in text))
            hits.append(_citation(row, "fts", -score))
        hits.sort(key=lambda hit: (hit["score"], hit["chunk_id"]))
        return hits

    def chunks(self, *, user_id=None, request_id=None, limit: int | None = None) -> list[dict]:
        """Citation chunks (optionally scoped) in stable chunk-id order.

        A ``request_id`` filter keeps chunks bound to that request *and* chunks
        with no request id (event-level evidence), mirroring the FTS query.
        """
        clauses = []
        params: list = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(str(user_id))
        if request_id:
            clauses.append("(request_id = ? OR request_id = '')")
            params.append(str(request_id))
        sql = (
            "SELECT chunk_id, source_type, source_id, user_id, request_id, text, metadata_json "
            "FROM evidence_chunks"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY chunk_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            try:
                params.append(max(1, min(int(limit), MAX_CHUNK_LIMIT)))
            except (TypeError, ValueError):
                params.append(MAX_CHUNK_LIMIT)
        rows = self.connection.execute(sql, params).fetchall()
        return [_citation(row, "store", None) for row in rows]

    def record_trace(self, summary: dict) -> None:
        """Store a one-line trace summary (the full trace stays a JSON file).

        The summary is sanitised first: a provider error can quote the endpoint
        it failed to reach, including a ``user:password@`` component and a
        bearer key, and this row outlives the JSON trace it came from.
        """
        payload = sanitize(dict(summary or {}))
        request_id = str(payload.get("request_id") or "")
        run_id = str(payload.get("run_id") or "")
        trace_id = f"{run_id}/{request_id}" if run_id else request_id
        if not request_id:
            raise StoreError("a trace summary needs a request_id")
        with self.connection as connection:
            connection.execute(
                'INSERT OR REPLACE INTO "traces" '
                '("trace_id", "run_id", "request_id", "provider", "model", '
                '"fallback_reason", "summary_json") VALUES (?, ?, ?, ?, ?, ?, ?)',
                (
                    trace_id,
                    run_id,
                    request_id,
                    _text(payload.get("provider")),
                    _text(payload.get("model")),
                    _text(payload.get("fallback_reason")),
                    stable_json(payload),
                ),
            )


def capped_int(value, default: int = 8) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return default


def _records(frame) -> list[dict]:
    """Rows from a pandas DataFrame, a tuple of dicts, or ``None``."""
    if frame is None:
        return []
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        return list(to_dict("records"))
    return [dict(row) for row in frame]


def _citation(row, backend: str, score) -> dict:
    metadata: dict = {}
    raw = row["metadata_json"] if "metadata_json" in row.keys() else ""
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            metadata = parsed
    return {
        "chunk_id": str(row["chunk_id"]),
        "source_type": str(row["source_type"]),
        "source_id": str(row["source_id"]),
        "user_id": str(row["user_id"]),
        "request_id": str(row["request_id"]),
        "text": str(row["text"]),
        "metadata": metadata,
        "score": None if score is None else round(float(score), 6),
        "backend": str(backend),
    }


def build_default_store(*, rebuild: bool = False, repo_root=None) -> dict:
    """Build (or reuse) the repo default database and return the build summary."""
    with LocalStore(repo_root=repo_root) as store:
        return store.build(rebuild=rebuild)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m code.store",
        description="Build data/app.db from the dataset CSVs (idempotent).",
    )
    parser.add_argument("--rebuild", action="store_true", help="drop and rebuild every table")
    parser.add_argument("--db", default=None, help="database path (default: data/app.db)")
    parser.add_argument("--repo-root", default=None, help="repository root (default: this checkout)")
    args = parser.parse_args(argv)

    with LocalStore(args.db, repo_root=args.repo_root) as store:
        summary = store.build(rebuild=args.rebuild)
        print(f"{summary['status']}: {store.db_path}")
        for table, count in summary["rows"].items():
            print(f"  {table}: {count}")
        print(f"  fts5: {'yes' if store._fts_available() else 'no (LIKE fallback)'}")
    return 0


__all__ = [
    "DEFAULT_DB_PATH",
    "LocalStore",
    "SCHEMA_VERSION",
    "StoreError",
    "TABLE_COLUMNS",
    "build_default_store",
    "load_ocr_texts",
]


if __name__ == "__main__":
    raise SystemExit(main())
