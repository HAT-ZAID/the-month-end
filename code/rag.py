"""RAG-first evidence retrieval with the SQLite store as the fallback (Task 5B).

Two rules shape this module:

1. **Citations only.**  A retriever returns *candidate evidence* -- chunk text
   plus a citation (``message_id`` / ``event_id`` / ``image_id``) -- never an
   authoritative number.  Amounts, balances and dates always come from
   :mod:`code.tools` / the kernel, and the SQLite store is the system of record.
2. **RAG first, SQLite always available.**  :class:`HybridEvidenceRetriever`
   tries the vector index first and falls back to SQLite FTS5 on any import,
   configuration, connection or query failure.  The failure is recorded as a
   ``retrieval_fallback`` warning, and no citation is dropped silently: results
   are merged by ``chunk_id`` and the cap is the caller's ``limit``.

Importing this module never touches the network and never imports LangChain: the
vector backend is built lazily on first ``search`` and degrades to FTS when
Ollama (or ``langchain-ollama``) is unavailable.  That is what lets the agent
tests run offline.

Backend selection (``BUY_WAIT_RAG_BACKEND``):

* ``hybrid`` (default) -- vector first, SQLite FTS fallback;
* ``fts`` -- SQLite only (useful offline or for reproducible checks);
* ``off``/``none`` -- no retriever; the tool layer uses its lexical fallback.
"""
from __future__ import annotations

import os
import sys
from typing import Protocol, runtime_checkable

from code.providers import (
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_BASE_URL_ENV,
    endpoint_privacy_warning,
    is_secure_or_local_url,
)
from code.store import LocalStore, StoreError

BACKEND_ENV = "BUY_WAIT_RAG_BACKEND"
EMBED_MODEL_ENV = "BUY_WAIT_EMBED_MODEL"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
BACKEND_CHOICES = ("hybrid", "fts", "off")

MAX_LIMIT = 50
DEFAULT_LIMIT = 8


class RetrievalError(RuntimeError):
    """A retrieval backend could not answer (the hybrid retriever falls back)."""


@runtime_checkable
class EvidenceRetriever(Protocol):
    """Candidate-evidence lookup shared by the vector, FTS and hybrid backends."""

    def search(self, query, *, user_id=None, request_id=None, limit=DEFAULT_LIMIT) -> list[dict]:
        ...


def _capped(limit) -> int:
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


class SQLiteFTSRetriever:
    """Authoritative full-text search over ``evidence_chunks`` (FTS5 or LIKE)."""

    backend = "fts"

    def __init__(self, store: LocalStore):
        self.store = store

    def search(self, query, *, user_id=None, request_id=None, limit=DEFAULT_LIMIT) -> list[dict]:
        try:
            return self.store.search_fts(
                query, user_id=user_id, request_id=request_id, limit=_capped(limit)
            )
        except StoreError as exc:
            raise RetrievalError(f"sqlite search failed: {exc}") from exc


class VectorEvidenceRetriever:
    """LangChain ``InMemoryVectorStore`` over the chunk text, embeddings lazy.

    The index is built per user scope on first use (and cached): the dataset has
    ~25k chunks, and evidence is always user-scoped, so a global index would cost
    one embedding call per chunk per run for no extra recall.  An unscoped query
    falls back to a bounded global index (``global_max_documents``).

    Nothing is imported or contacted until :meth:`search` runs, and any failure
    raises :class:`RetrievalError` so the hybrid retriever can fall back.  Tests
    inject ``embeddings`` (a LangChain ``Embeddings`` instance) and never need a
    running Ollama server.
    """

    backend = "vector"

    def __init__(
        self,
        store: LocalStore,
        *,
        embeddings=None,
        embed_model=None,
        base_url=None,
        max_documents: int = 50000,
        global_max_documents: int = 2000,
    ):
        self.store = store
        self._embeddings = embeddings
        self.embed_model = embed_model or os.environ.get(EMBED_MODEL_ENV) or DEFAULT_EMBED_MODEL
        self.base_url = base_url or os.environ.get(OLLAMA_BASE_URL_ENV) or DEFAULT_OLLAMA_BASE_URL
        # The embeddings client reads the same env var as the chat provider, so
        # it obeys the same two rules: a remote endpoint must be https, and a
        # remote endpoint gets the privacy notice before chunk text leaves.
        if not is_secure_or_local_url(self.base_url):
            raise RetrievalError(
                "refusing a plaintext embedding endpoint: use https or this machine"
            )
        notice = endpoint_privacy_warning(self.base_url, honour_acceptance=False)
        if notice:
            # The batch, the Jev gate and the TUI all say this to stderr; an
            # embeddings host that is about to receive every evidence chunk
            # must not be the one place that stays quiet.
            print(f"privacy: {notice}", file=sys.stderr)
        # One index per user scope: a full-dataset index would mean one embedding
        # call per chunk (~25k) on every run, and evidence is always user-scoped.
        self.max_documents = int(max_documents)
        self.global_max_documents = int(global_max_documents)
        self._stores: dict[str, object] = {}
        self._chunks: dict[str, list[dict]] = {}

    # -- lazy build -------------------------------------------------------

    def documents(self, user_id=None) -> list[dict]:
        """Chunks with usable text for one scope, in stable chunk-id order."""
        key = str(user_id or "")
        if key not in self._chunks:
            limit = self.max_documents if user_id else self.global_max_documents
            try:
                rows = self.store.chunks(user_id=user_id or None, limit=limit)
            except StoreError as exc:
                raise RetrievalError(f"cannot read evidence chunks: {exc}") from exc
            self._chunks[key] = [row for row in rows if str(row.get("text") or "").strip()]
        return self._chunks[key]

    def _make_embeddings(self):
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError as exc:  # optional dependency missing
            raise RetrievalError(f"langchain-ollama is not installed: {exc}") from exc
        try:
            return OllamaEmbeddings(
                model=self.embed_model,
                base_url=self.base_url,
                # A remote https endpoint can still redirect the chunk text
                # somewhere else, so the client refuses to follow one.
                client_kwargs={"follow_redirects": False},
            )
        except Exception as exc:  # bad config, incompatible client, ...
            raise RetrievalError(
                f"cannot configure embeddings {self.embed_model!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def _ensure(self, user_id=None):
        key = str(user_id or "")
        cached = self._stores.get(key)
        if cached is not None:
            return cached
        try:
            from langchain_core.documents import Document
            from langchain_core.vectorstores import InMemoryVectorStore
        except ImportError as exc:
            raise RetrievalError(f"langchain-core vector store is not installed: {exc}") from exc

        embeddings = self._embeddings if self._embeddings is not None else self._make_embeddings()
        documents = []
        for chunk in self.documents(user_id):
            documents.append(
                Document(
                    page_content=str(chunk["text"]),
                    metadata={
                        "chunk_id": chunk["chunk_id"],
                        "source_type": chunk["source_type"],
                        "source_id": chunk["source_id"],
                        "user_id": chunk["user_id"],
                        "request_id": chunk["request_id"],
                        "citation": chunk.get("metadata") or {},
                    },
                )
            )
        if not documents:
            raise RetrievalError("no evidence chunks to index for this scope")
        try:
            store = InMemoryVectorStore.from_documents(documents, embeddings)
        except Exception as exc:  # embedding server down, dimension mismatch, ...
            raise RetrievalError(
                f"vector index build failed: {type(exc).__name__}: {exc}"
            ) from exc
        self._stores[key] = store
        return store

    # -- search -----------------------------------------------------------

    def search(self, query, *, user_id=None, request_id=None, limit=DEFAULT_LIMIT) -> list[dict]:
        capped = _capped(limit)
        store = self._ensure(user_id)
        try:
            pairs = store.similarity_search_with_score(query, k=min(capped * 4, 64))
        except Exception as exc:
            raise RetrievalError(
                f"vector query failed: {type(exc).__name__}: {exc}"
            ) from exc

        hits: list[dict] = []
        for document, score in pairs:
            metadata = dict(document.metadata or {})
            if user_id and str(metadata.get("user_id") or "") != str(user_id):
                continue
            if request_id:
                # Blank request ids are user-level evidence (events, most
                # messages) and must not be filtered out by request scoping.
                chunk_request = str(metadata.get("request_id") or "")
                if chunk_request and chunk_request != str(request_id):
                    continue
            hits.append(
                {
                    "chunk_id": str(metadata.get("chunk_id") or ""),
                    "source_type": str(metadata.get("source_type") or ""),
                    "source_id": str(metadata.get("source_id") or ""),
                    "user_id": str(metadata.get("user_id") or ""),
                    "request_id": str(metadata.get("request_id") or ""),
                    "text": str(document.page_content),
                    "metadata": dict(metadata.get("citation") or {}),
                    "score": round(float(score), 6),
                    "backend": "vector",
                }
            )
            if len(hits) >= capped:
                break
        return hits


class HybridEvidenceRetriever:
    """Vector first, SQLite FTS as the safety net, merged by ``chunk_id``."""

    def __init__(self, *, vector=None, fts=None, warnings=None, circuit_breaker: bool = True):
        if vector is None and fts is None:
            raise ValueError("HybridEvidenceRetriever needs at least one backend")
        self.vector = vector
        self.fts = fts
        self.warnings: list[dict] = list(warnings or [])
        self.last_backend = "fts" if vector is None else "vector"
        self.circuit_breaker = bool(circuit_breaker)
        self._vector_down = False

    def backend_available(self, name: str) -> bool:
        if name == "vector":
            return self.vector is not None and not self._vector_down
        return self.fts is not None

    def close(self) -> None:
        for backend in (self.vector, self.fts):
            store = getattr(backend, "store", None)
            closer = getattr(store, "close", None)
            if callable(closer):
                closer()

    def search(self, query, *, user_id=None, request_id=None, limit=DEFAULT_LIMIT) -> list[dict]:
        capped = _capped(limit)
        merged: list[dict] = []
        seen: set[str] = set()

        def add(hits) -> None:
            for hit in hits:
                chunk_id = str(hit.get("chunk_id") or "")
                key = chunk_id or f"{hit.get('source_type')}:{hit.get('source_id')}:{len(seen)}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(hit)

        vector_failed = False
        if self.vector is not None and not self._vector_down:
            try:
                add(self.vector.search(query, user_id=user_id, request_id=request_id, limit=capped))
            except Exception as exc:  # import/config/connection/query failure
                vector_failed = True
                # One stall per run is enough: keep serving from SQLite instead
                # of retrying a dead backend on every question.
                if self.circuit_breaker:
                    self._vector_down = True
                self.warnings.append(
                    {
                        "code": "retrieval_fallback",
                        "backend": "vector",
                        "fallback": "fts",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        if len(merged) < capped and self.fts is not None:
            try:
                add(self.fts.search(query, user_id=user_id, request_id=request_id, limit=capped))
            except Exception as exc:
                self.warnings.append(
                    {
                        "code": "retrieval_failed",
                        "backend": "fts",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        if vector_failed:
            backend = "fallback"
        elif any(hit.get("backend") == "vector" for hit in merged):
            backend = "vector"
        else:
            backend = "fts"
        self.last_backend = backend
        return merged[:capped]


def build_retriever(
    repo_root=None,
    *,
    db_path=None,
    backend=None,
    embeddings=None,
    warnings=None,
    rebuild: bool = False,
):
    """Build the configured retriever (``None`` when retrieval is turned off).

    The store is built idempotently first; a build failure returns ``None`` so
    the agent still runs with the tool layer's lexical fallback.
    """
    kind = str(backend or os.environ.get(BACKEND_ENV) or "hybrid").strip().lower()
    if kind in ("off", "none", "disabled"):
        return None
    if kind not in BACKEND_CHOICES:
        raise ValueError(
            f"unknown {BACKEND_ENV} {kind!r}; expected one of {', '.join(BACKEND_CHOICES)}"
        )

    store = LocalStore(db_path, repo_root=repo_root)
    try:
        store.build(rebuild=rebuild)
    except Exception:
        store.close()
        return None

    fts = SQLiteFTSRetriever(store)
    vector = None
    if kind == "hybrid":
        vector = VectorEvidenceRetriever(store, embeddings=embeddings)
    return HybridEvidenceRetriever(vector=vector, fts=fts, warnings=warnings)


__all__ = [
    "BACKEND_ENV",
    "BACKEND_CHOICES",
    "DEFAULT_EMBED_MODEL",
    "EvidenceRetriever",
    "HybridEvidenceRetriever",
    "RetrievalError",
    "SQLiteFTSRetriever",
    "VectorEvidenceRetriever",
    "build_retriever",
]
