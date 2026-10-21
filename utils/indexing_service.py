"""Transactional PDF chunking and local embedding-index management."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from utils.chunking import DocumentChunk, PageText, chunk_pages
from utils.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS, EMBEDDING_MODEL_NAME
from utils.database import get_connection
from utils.documents import resolve_stored_document_path
from utils.embedding_service import EmbeddingModelError, EmbeddingService
from utils.pdf_processing import PDFProcessingError, extract_pdf_pages
from utils.vector_store import VectorRecord, VectorStore


class IndexingError(RuntimeError):
    """Raised for an expected document indexing failure."""


@dataclass(frozen=True)
class IndexingResult:
    document_id: str
    filename: str
    status: str
    chunk_count: int
    skipped: bool = False
    message: str = ""


def indexing_config_hash(model_name: str | None = None) -> str:
    configuration = {
        "model": model_name or EMBEDDING_MODEL_NAME,
        "chunk_size_chars": CHUNK_SIZE_CHARS,
        "chunk_overlap_chars": CHUNK_OVERLAP_CHARS,
        "version": 1,
    }
    return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode("utf-8")).hexdigest()


def get_document_index_statuses() -> list[dict]:
    """Return real per-document indexing states without loading the model."""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT d.id, d.original_filename, d.stored_filename, d.extraction_status,
                      d.content_sha256, s.status, s.source_sha256, s.model_name,
                      s.embedding_dimension, s.index_config_hash, s.chunk_count,
                      s.indexed_at, s.error_message,
                      (SELECT COUNT(*) FROM document_chunks AS c WHERE c.document_id = d.id) AS actual_chunk_count
               FROM documents AS d LEFT JOIN document_index_status AS s ON s.document_id = d.id
               ORDER BY d.uploaded_at DESC, d.original_filename COLLATE NOCASE"""
        ).fetchall()
    config_hash = indexing_config_hash()
    results = []
    for row in rows:
        item = dict(row)
        try:
            source_exists = resolve_stored_document_path(item["stored_filename"]).is_file()
        except (OSError, ValueError):
            source_exists = False
        if not source_exists:
            item["status"] = "missing_source"
        elif item["extraction_status"] == "no_text":
            item["status"] = "no_text"
        elif item["status"] is None:
            item["status"] = "not_indexed"
        elif item["status"] == "indexed" and (
            item["source_sha256"] != item["content_sha256"]
            or item["model_name"] != EMBEDDING_MODEL_NAME
            or item["index_config_hash"] != config_hash
            or not item["chunk_count"]
            or item["actual_chunk_count"] != item["chunk_count"]
        ):
            item["status"] = "out_of_date"
        results.append(item)
    return results


def _load_document(document_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    return dict(row) if row else None


def _current_record(document_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM document_index_status WHERE document_id = ?", (document_id,)
        ).fetchone()
    return dict(row) if row else None


def _store_status(
    document_id: str,
    status: str,
    *,
    source_hash: str | None = None,
    model_name: str | None = None,
    dimension: int | None = None,
    config_hash: str | None = None,
    chunk_count: int = 0,
    indexed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO document_index_status (
                   document_id, status, source_sha256, model_name, embedding_dimension,
                   index_config_hash, chunk_count, indexed_at, error_message
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET status=excluded.status,
                   source_sha256=excluded.source_sha256, model_name=excluded.model_name,
                   embedding_dimension=excluded.embedding_dimension,
                   index_config_hash=excluded.index_config_hash, chunk_count=excluded.chunk_count,
                   indexed_at=excluded.indexed_at, error_message=excluded.error_message""",
            (document_id, status, source_hash, model_name, dimension, config_hash, chunk_count, indexed_at, error_message),
        )


def _record_failed_attempt(
    document_id: str,
    *,
    source_hash: str | None,
    model_name: str,
    config_hash: str,
    message: str,
) -> None:
    existing = _current_record(document_id)
    if existing and existing["status"] == "indexed":
        with get_connection() as connection:
            connection.execute(
                "UPDATE document_index_status SET error_message = ? WHERE document_id = ?",
                (message[:500], document_id),
            )
        return
    _store_status(
        document_id,
        "failed",
        source_hash=source_hash,
        model_name=model_name,
        config_hash=config_hash,
        error_message=message[:500],
    )


def index_document(
    document_id: str,
    *,
    embedding_service: EmbeddingService | None = None,
    force: bool = False,
    progress_callback: Callable[[float], None] | None = None,
) -> IndexingResult:
    """Index or transactionally replace one uploaded PDF's vector chunks."""
    document = _load_document(document_id)
    if document is None:
        raise IndexingError("That document is no longer in the library.")
    filename = document["original_filename"]
    model_name = embedding_service.model_name if embedding_service else EMBEDDING_MODEL_NAME
    config_hash = indexing_config_hash(model_name)
    try:
        source_path = resolve_stored_document_path(document["stored_filename"])
        if not source_path.is_file():
            _store_status(document_id, "missing_source", error_message="The stored PDF file is missing.")
            raise IndexingError(f"{filename}: the stored PDF file is missing.")
        file_bytes = source_path.read_bytes()
    except IndexingError:
        raise
    except OSError as error:
        raise IndexingError(f"{filename}: the stored PDF could not be read.") from error

    source_hash = hashlib.sha256(file_bytes).hexdigest()
    if source_hash != document["content_sha256"]:
        _store_status(
            document_id,
            "out_of_date",
            source_hash=source_hash,
            model_name=model_name,
            config_hash=config_hash,
            error_message="The stored PDF no longer matches its library record.",
        )
        raise IndexingError(f"{filename}: the stored PDF does not match its library record.")
    if document["extraction_status"] == "no_text":
        _store_status(document_id, "no_text", source_hash=source_hash, model_name=model_name, config_hash=config_hash)
        return IndexingResult(document_id, filename, "no_text", 0, message="No selectable text was found; OCR is not available.")

    existing = _current_record(document_id)
    if (
        not force
        and existing
        and existing["status"] == "indexed"
        and existing["source_sha256"] == source_hash
        and existing["model_name"] == model_name
        and existing["index_config_hash"] == config_hash
    ):
        with get_connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM document_chunks WHERE document_id = ?", (document_id,)).fetchone()[0]
        if count == existing["chunk_count"] and count > 0:
            return IndexingResult(document_id, filename, "indexed", count, skipped=True, message="Already up to date.")

    service = embedding_service or EmbeddingService(model_name)
    try:
        _, extracted_pages = extract_pdf_pages(file_bytes)
        pages = [PageText(page.page_number, page.text) for page in extracted_pages]
        chunks = chunk_pages(
            document_id,
            pages,
            chunk_size_chars=CHUNK_SIZE_CHARS,
            overlap_chars=CHUNK_OVERLAP_CHARS,
        )
        if not chunks:
            _store_status(document_id, "no_text", source_hash=source_hash, model_name=model_name, config_hash=config_hash)
            return IndexingResult(document_id, filename, "no_text", 0, message="No selectable text was found; OCR is not available.")

        vectors = []
        batch_size = 32
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors.extend(service.embed([chunk.text for chunk in batch], batch_size=batch_size))
            if progress_callback:
                progress_callback(min(0.9, (start + len(batch)) / len(chunks) * 0.9))
        dimension = service.dimension
        if len(vectors) != len(chunks):
            raise IndexingError("The embedding service returned an incomplete vector batch.")
        from utils.vector_store import serialize_vector

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        prepared = [
            (
                chunk.chunk_id,
                chunk.document_id,
                chunk.position,
                chunk.page_number,
                chunk.text,
                chunk.content_hash,
                model_name,
                dimension,
                serialize_vector(vector, dimension),
                now,
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        with get_connection() as connection:
            connection.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
            connection.executemany(
                """INSERT INTO document_chunks (
                       chunk_id, document_id, position, page_number, chunk_text, content_hash,
                       embedding_model, embedding_dimension, embedding_vector, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
            connection.execute(
                """INSERT INTO document_index_status (
                       document_id, status, source_sha256, model_name, embedding_dimension,
                       index_config_hash, chunk_count, indexed_at, error_message
                   ) VALUES (?, 'indexed', ?, ?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(document_id) DO UPDATE SET status='indexed', source_sha256=excluded.source_sha256,
                       model_name=excluded.model_name, embedding_dimension=excluded.embedding_dimension,
                       index_config_hash=excluded.index_config_hash, chunk_count=excluded.chunk_count,
                       indexed_at=excluded.indexed_at, error_message=NULL""",
                (document_id, source_hash, model_name, dimension, config_hash, len(chunks), now),
            )
    except (EmbeddingModelError, PDFProcessingError, OSError, sqlite3.Error, ValueError, IndexingError) as error:
        message = str(error) if isinstance(error, (EmbeddingModelError, PDFProcessingError, IndexingError)) else "Indexing failed due to a local processing or storage error."
        _record_failed_attempt(
            document_id,
            source_hash=source_hash,
            model_name=model_name,
            config_hash=config_hash,
            message=message,
        )
        if isinstance(error, IndexingError):
            raise
        raise IndexingError(f"{filename}: {message}") from error
    if progress_callback:
        progress_callback(1.0)
    return IndexingResult(document_id, filename, "indexed", len(chunks), message="Indexed successfully.")


def index_pending_documents(
    *,
    embedding_service: EmbeddingService | None = None,
    progress_callback: Callable[[str, float], None] | None = None,
) -> list[IndexingResult]:
    """Index all missing, failed, or stale text-based documents independently."""
    eligible = [
        item for item in get_document_index_statuses()
        if item["status"] in {"not_indexed", "out_of_date", "failed"}
        and item["extraction_status"] == "ready"
    ]
    results: list[IndexingResult] = []
    service = embedding_service
    for index, document in enumerate(eligible):
        def report(value: float, *, current_index: int = index, doc_id: str = document["id"]) -> None:
            if progress_callback:
                progress_callback(doc_id, (current_index + value) / max(len(eligible), 1))

        try:
            result = index_document(
                document["id"],
                embedding_service=service,
                force=document["status"] in {"out_of_date", "failed"},
                progress_callback=report,
            )
            results.append(result)
        except IndexingError as error:
            results.append(IndexingResult(document["id"], document["original_filename"], "failed", 0, message=str(error)))
    return results


def _active_chunk_rows(model_name: str, document_id: str | None = None) -> list[dict]:
    query = """SELECT c.faiss_id, c.chunk_id, c.document_id, c.position, c.page_number,
                      c.chunk_text, c.content_hash, c.embedding_model,
                      c.embedding_dimension, c.embedding_vector, d.original_filename,
                      d.stored_filename,
                      s.index_config_hash
               FROM document_chunks AS c
               JOIN documents AS d ON d.id = c.document_id
               JOIN document_index_status AS s ON s.document_id = c.document_id
               WHERE s.status = 'indexed' AND s.source_sha256 = d.content_sha256
                 AND s.model_name = ? AND s.index_config_hash = ?
                 AND c.embedding_model = ? AND c.embedding_dimension = s.embedding_dimension"""
    params: list[object] = [model_name, indexing_config_hash(model_name), model_name]
    if document_id is not None:
        query += " AND c.document_id = ?"
        params.append(document_id)
    query += " ORDER BY c.document_id, c.position"
    with get_connection() as connection:
        rows = [dict(row) for row in connection.execute(query, params).fetchall()]
    active = []
    for row in rows:
        try:
            if resolve_stored_document_path(row["stored_filename"]).is_file():
                active.append(row)
        except (OSError, ValueError, TypeError):
            continue
    return active


def list_active_chunks(model_name: str = EMBEDDING_MODEL_NAME, document_id: str | None = None) -> list[dict]:
    """Read only chunks from documents whose current index metadata is valid."""
    return _active_chunk_rows(model_name, document_id)


def rebuild_faiss_index(*, model_name: str = EMBEDDING_MODEL_NAME, vector_store: VectorStore | None = None) -> int:
    """Rebuild the all-documents FAISS cache from active SQLite vectors."""
    rows = _active_chunk_rows(model_name)
    records = [
        VectorRecord(row["faiss_id"], row["chunk_id"], row["embedding_model"], row["embedding_dimension"], row["embedding_vector"])
        for row in rows
    ]
    try:
        return (vector_store or VectorStore()).rebuild(records, model_name=model_name, scope_key="all")
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, IndexingError):
            raise
        raise IndexingError("The FAISS index could not be rebuilt from local SQLite records.") from error
