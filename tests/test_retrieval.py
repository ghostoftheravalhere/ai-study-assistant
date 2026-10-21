"""Phase 4 tests with synthetic embeddings and a real small FAISS CPU index."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Sequence

import fitz
import numpy as np
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from utils import database, documents, indexing_service
from utils.chunking import PageText, chunk_pages
from utils.embedding_service import EmbeddingModelError, EmbeddingService
from utils.indexing_service import IndexingError, get_document_index_statuses, index_document, index_pending_documents
from utils.retrieval_service import RetrievalService, sanitize_source_citations
from utils.vector_store import (
    VectorRecord,
    VectorStore,
    VectorStoreError,
    deserialize_vector,
    serialize_vector,
)


MOCK_MODEL = "tests/mock-semantic-v1"


class FakeEmbeddingModel:
    def __init__(self, dimension: int = 4) -> None:
        self.dimension = dimension
        self.calls: list[tuple[list[str], int, dict]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts: Sequence[str], *, batch_size: int, **kwargs) -> np.ndarray:
        self.calls.append((list(texts), batch_size, kwargs))
        return np.vstack([fake_vector(text) for text in texts])


def fake_vector(text: str) -> np.ndarray:
    normalized = text.casefold()
    if any(word in normalized for word in ("force", "mass", "acceleration", "push", "motion")):
        return np.array([1, 0, 0, 0], dtype=np.float32)
    if any(word in normalized for word in ("cell", "biology", "mitochondria", "organism")):
        return np.array([0, 1, 0, 0], dtype=np.float32)
    if any(word in normalized for word in ("history", "empire", "century", "kingdom")):
        return np.array([0, 0, 1, 0], dtype=np.float32)
    return np.array([0, 0, 0, 1], dtype=np.float32)


class FakeEmbeddingService:
    model_name = MOCK_MODEL
    dimension = 4

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.calls.append(list(texts))
        if self.failure:
            raise self.failure
        return np.vstack([fake_vector(text) for text in texts]).astype(np.float32)


def make_pdf(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


@pytest.fixture
def phase4_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    database_path = tmp_path / "phase3_1.sqlite3"
    storage_path = tmp_path / "user-data" / "documents"
    index_path = tmp_path / "user-data" / "indexes"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", storage_path)
    monkeypatch.setattr(indexing_service, "EMBEDDING_MODEL_NAME", MOCK_MODEL)
    database.initialize_database()
    return database_path, storage_path, index_path


def index_pdf(name: str, *pages: str, embedding_service: FakeEmbeddingService | None = None) -> dict:
    saved = documents.save_uploaded_pdf(name, make_pdf(*pages))
    result = index_document(saved["id"], embedding_service=embedding_service or FakeEmbeddingService())
    assert result.status == "indexed"
    return saved


def test_phase_3_1_migration_preserves_documents_chats_activity_and_adds_phase4_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old-phase3_1.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """CREATE TABLE study_activity (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, activity_type TEXT NOT NULL,
                   description TEXT NOT NULL, score REAL, created_at TEXT NOT NULL);
               CREATE TABLE documents (
                   id TEXT PRIMARY KEY, original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL UNIQUE,
                   file_size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL, uploaded_at TEXT NOT NULL,
                   extraction_status TEXT NOT NULL, extracted_text TEXT NOT NULL, content_sha256 TEXT NOT NULL UNIQUE);
               CREATE TABLE chat_conversations (
                   id TEXT PRIMARY KEY, title TEXT NOT NULL, source_document_id TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
               CREATE TABLE chat_messages (
                   id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                   role TEXT NOT NULL, content TEXT NOT NULL, request_id TEXT UNIQUE, created_at TEXT NOT NULL);
               INSERT INTO study_activity VALUES (1, 'summary_generated', 'Old summary', NULL, '2026-01-01');
               INSERT INTO documents VALUES ('doc1', 'old.pdf', 'old.pdf', 10, 1, '2026-01-01', 'ready', 'Old page text', 'sha');
               INSERT INTO chat_conversations VALUES ('conv1', 'Old conversation', 'doc1', '2026-01-01', '2026-01-01');
               INSERT INTO chat_messages VALUES ('msg1', 'conv1', 'assistant', 'Old response', NULL, '2026-01-01');"""
        )

    database.initialize_database()
    database.initialize_database()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "chat_conversations", "chat_messages", "study_activity")
        }
        chat_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")}
        conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_conversations)")}
        status = connection.execute("SELECT status FROM document_index_status WHERE document_id='doc1'").fetchone()[0]
    assert {"document_chunks", "document_index_status"}.issubset(tables)
    assert counts == {"documents": 1, "chat_conversations": 1, "chat_messages": 1, "study_activity": 1}
    assert {"sources_json"}.issubset(chat_columns)
    assert "chat_mode" in conversation_columns
    assert status == "not_indexed"
    assert database.list_conversations("doc1", chat_mode="document")[0]["id"] == "conv1"
    assert database.get_chat_messages("conv1")[0]["content"] == "Old response"


def test_chunking_handles_empty_short_long_overlap_page_numbers_and_stable_ids() -> None:
    assert chunk_pages("doc", [PageText(1, "  "), PageText(2, "")]) == []
    short = chunk_pages("doc", [PageText(3, "A short paragraph. Another sentence.")], chunk_size_chars=100, overlap_chars=20)
    assert len(short) == 1 and short[0].page_number == 3
    text = " ".join(f"Sentence {index} describes an important concept." for index in range(1, 35))
    chunks = chunk_pages("doc", [PageText(1, text), PageText(2, text)], chunk_size_chars=180, overlap_chars=35)
    assert len(chunks) > 2
    assert all(chunk.text and len(chunk.text) <= 180 for chunk in chunks)
    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert any(chunks[index].text[-20:] in chunks[index + 1].text for index in range(len(chunks) - 1))
    assert chunks == chunk_pages("doc", [PageText(1, text), PageText(2, text)], chunk_size_chars=180, overlap_chars=35)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert all(chunk.chunk_id == hashlib.sha256(
        f"{chunk.document_id}\0{chunk.page_number}\0{chunk.position}\0{chunk.content_hash}".encode()
    ).hexdigest() for chunk in chunks)


def test_chunking_deduplicates_exact_duplicate_passages_but_not_page_sources() -> None:
    chunks = chunk_pages(
        "doc",
        [PageText(1, "Repeated passage."), PageText(1, "Repeated passage."), PageText(2, "Repeated passage.")],
        chunk_size_chars=100,
        overlap_chars=10,
    )
    assert len(chunks) == 2
    assert [chunk.page_number for chunk in chunks] == [1, 2]


def test_embedding_service_is_lazy_batched_float32_normalized_and_dimension_checked() -> None:
    model = FakeEmbeddingModel()
    loaded: list[str] = []

    def loader(name: str) -> FakeEmbeddingModel:
        loaded.append(name)
        return model

    service = EmbeddingService("custom/model", model_loader=loader)
    assert loaded == []
    vectors = service.embed(["force and mass", "cell biology"], batch_size=2)
    assert loaded == ["custom/model"]
    assert vectors.shape == (2, 4) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert model.calls[0][1] == 2
    assert service.embed(["history"]).shape == (1, 4)
    assert loaded == ["custom/model"]


def test_embedding_service_rejects_bad_dimension_and_reports_missing_model() -> None:
    class WrongDimension(FakeEmbeddingModel):
        def encode(self, texts, **kwargs):
            return np.ones((len(texts), 3), dtype=np.float32)

    with pytest.raises(EmbeddingModelError, match="unexpected shape or dimension"):
        EmbeddingService("wrong", model_loader=lambda _: WrongDimension()).embed(["text"])
    with pytest.raises(EmbeddingModelError, match="could not be loaded"):
        EmbeddingService("missing", model_loader=lambda _: (_ for _ in ()).throw(OSError("not cached"))).embed(["text"])


def test_float32_vector_serialization_round_trip_and_validation() -> None:
    original = np.array([1, 2, 3, 4], dtype=np.float64)
    blob = serialize_vector(original, 4)
    restored = deserialize_vector(blob, 4)
    assert restored.dtype == np.float32 and restored.shape == (4,)
    assert np.allclose(np.linalg.norm(restored), 1)
    with pytest.raises(ValueError, match="invalid shape"):
        serialize_vector(np.ones(3, dtype=np.float32), 4)
    with pytest.raises(VectorStoreError, match="byte length"):
        deserialize_vector(b"bad", 4)


def test_successful_index_stores_page_aware_chunks_and_skips_unchanged_pdf(phase4_environment) -> None:
    _, _, index_dir = phase4_environment
    embedding = FakeEmbeddingService()
    saved = documents.save_uploaded_pdf("pages.pdf", make_pdf("Force and motion.", "", "Cell biology."))
    indexed = index_document(saved["id"], embedding_service=embedding)
    assert indexed.chunk_count == 2
    assert {chunk["page_number"] for chunk in _chunks(saved["id"])} == {1, 3}
    assert all(len(chunk["embedding_vector"]) == 4 * 4 for chunk in _chunks(saved["id"]))
    status = next(item for item in get_document_index_statuses() if item["id"] == saved["id"])
    assert status["status"] == "indexed" and status["embedding_dimension"] == 4
    skipped = index_document(saved["id"], embedding_service=FakeEmbeddingService())
    assert skipped.skipped and skipped.chunk_count == 2
    assert not list(index_dir.glob("*.faiss"))


def _chunks(document_id: str) -> list[dict]:
    with database.get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM document_chunks WHERE document_id=? ORDER BY position", (document_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def test_index_all_documents_and_textless_pdf_status(phase4_environment) -> None:
    ready_a = documents.save_uploaded_pdf("a.pdf", make_pdf("Force causes acceleration."))
    ready_b = documents.save_uploaded_pdf("b.pdf", make_pdf("Cell biology studies organisms."))
    blank = documents.save_uploaded_pdf("blank.pdf", make_pdf(""))
    results = index_pending_documents(embedding_service=FakeEmbeddingService())
    assert {result.document_id for result in results} == {ready_a["id"], ready_b["id"]}
    statuses = {item["id"]: item["status"] for item in get_document_index_statuses()}
    assert statuses == {ready_a["id"]: "indexed", ready_b["id"]: "indexed", blank["id"]: "no_text"}


def test_failed_reindex_preserves_previous_valid_chunks_and_not_mark_complete(phase4_environment) -> None:
    saved = index_pdf("keep.pdf", "Force and motion.")
    original_chunks = _chunks(saved["id"])
    failing = FakeEmbeddingService(EmbeddingModelError("mock model failure"))
    with pytest.raises(IndexingError, match="mock model failure"):
        index_document(saved["id"], embedding_service=failing, force=True)
    assert _chunks(saved["id"]) == original_chunks
    status = next(item for item in get_document_index_statuses() if item["id"] == saved["id"])
    assert status["status"] == "indexed"
    assert status["error_message"] == "mock model failure"


def test_changed_model_or_chunk_configuration_marks_index_out_of_date(phase4_environment, monkeypatch) -> None:
    saved = index_pdf("stale.pdf", "History of an empire.")
    monkeypatch.setattr(indexing_service, "EMBEDDING_MODEL_NAME", "changed/model")
    status = next(item for item in get_document_index_statuses() if item["id"] == saved["id"])
    assert status["status"] == "out_of_date"


def test_missing_pdf_and_unextractable_pdf_are_reported(phase4_environment) -> None:
    missing = documents.save_uploaded_pdf("missing.pdf", make_pdf("History facts."))
    (documents.DOCUMENTS_DIR / missing["stored_filename"]).unlink()
    status = next(item for item in get_document_index_statuses() if item["id"] == missing["id"])
    assert status["status"] == "missing_source"
    with pytest.raises(IndexingError, match="stored PDF file is missing"):
        index_document(missing["id"], embedding_service=FakeEmbeddingService())

    scanned = documents.save_uploaded_pdf("scan.pdf", make_pdf(""))
    outcome = index_document(scanned["id"], embedding_service=FakeEmbeddingService())
    assert outcome.status == "no_text" and "OCR" in outcome.message


def test_retrieval_ranks_scope_correctly_and_uses_actual_page_citations(phase4_environment) -> None:
    _, _, index_dir = phase4_environment
    force_doc = index_pdf("physics.pdf", "Force and acceleration describe motion.")
    cell_doc = index_pdf("biology.pdf", "Cell biology studies mitochondria in each organism.")
    store = VectorStore(index_dir)
    search = RetrievalService(
        model_name=MOCK_MODEL,
        embedding_service=FakeEmbeddingService(),
        vector_store=store,
    )
    all_results = search.search("How does a push change motion?", top_k=5)
    assert all_results.passages[0].filename == "physics.pdf"
    assert {result.filename for result in all_results.passages} == {"physics.pdf", "biology.pdf"}
    scoped = search.search("How does a push change motion?", document_id=cell_doc["id"], top_k=5)
    assert scoped.passages and all(item.document_id == cell_doc["id"] for item in scoped.passages)
    assert scoped.passages[0].filename == "biology.pdf"
    assert "page_number" in scoped.context and "biology.pdf" in scoped.context
    assert force_doc["id"] not in scoped.context
    assert "--- Page" not in scoped.context


def test_retrieval_context_budget_top_k_validation_and_empty_scope(phase4_environment) -> None:
    saved = index_pdf("long.pdf", "Force and motion. " * 200)
    search = RetrievalService(
        model_name=MOCK_MODEL,
        embedding_service=FakeEmbeddingService(),
        vector_store=VectorStore(phase4_environment[2]),
        context_limit=500,
    )
    result = search.search("force motion", document_id=saved["id"], top_k=10)
    assert result.passages
    assert len(result.context) <= 500
    empty = search.search("anything", document_id="not-indexed")
    assert empty.passages == () and empty.context == ""
    with pytest.raises(ValueError, match="between 1 and 10"):
        search.search("force", top_k=11)
    with pytest.raises(ValueError, match="Enter a search"):
        search.search(" ")


def test_real_faiss_index_handles_empty_add_search_reload_and_no_duplicate_vectors(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    assert store.search([], np.ones(4, dtype=np.float32), model_name=MOCK_MODEL, top_k=5) == []
    records = [
        VectorRecord(1, "chunk-1", MOCK_MODEL, 4, serialize_vector(np.array([1, 0, 0, 0]), 4)),
        VectorRecord(2, "chunk-2", MOCK_MODEL, 4, serialize_vector(np.array([0, 1, 0, 0]), 4)),
    ]
    matches = store.search(records, np.array([0.9, 0.1, 0, 0], dtype=np.float32), model_name=MOCK_MODEL, top_k=2)
    assert [match.faiss_id for match in matches] == [1, 2]
    reloaded = store.search(records, np.array([0.9, 0.1, 0, 0]), model_name=MOCK_MODEL, top_k=2)
    assert [match.faiss_id for match in reloaded] == [1, 2]
    assert store.rebuild(records, model_name=MOCK_MODEL) == 2
    index_file = next(tmp_path.glob("*.faiss"))
    import faiss
    assert faiss.read_index(str(index_file)).ntotal == 2


def test_faiss_missing_or_corrupt_cache_rebuilds_from_database_records(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    records = [VectorRecord(8, "only", MOCK_MODEL, 4, serialize_vector(np.array([1, 0, 0, 0]), 4))]
    query = np.array([1, 0, 0, 0], dtype=np.float32)
    assert store.search(records, query, model_name=MOCK_MODEL, top_k=1)[0].faiss_id == 8
    index_file = next(tmp_path.glob("*.faiss"))
    index_file.write_bytes(b"corrupt index")
    assert store.search(records, query, model_name=MOCK_MODEL, top_k=1)[0].faiss_id == 8
    metadata_file = next(tmp_path.glob("*.json"))
    metadata_file.write_text("[]", encoding="utf-8")
    assert store.search(records, query, model_name=MOCK_MODEL, top_k=1)[0].faiss_id == 8
    index_file.unlink()
    assert store.search(records, query, model_name=MOCK_MODEL, top_k=1)[0].faiss_id == 8


def test_rebuilding_empty_faiss_cache_removes_old_artifacts(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    records = [VectorRecord(8, "only", MOCK_MODEL, 4, serialize_vector(np.array([1, 0, 0, 0]), 4))]
    store.search(records, np.array([1, 0, 0, 0]), model_name=MOCK_MODEL, top_k=1)
    assert list(tmp_path.glob("*.faiss")) and list(tmp_path.glob("*.json"))
    assert store.rebuild([], model_name=MOCK_MODEL) == 0
    assert not list(tmp_path.glob("*.faiss")) and not list(tmp_path.glob("*.json"))


def test_deleted_document_chunks_cascade_and_never_return_from_faiss(phase4_environment) -> None:
    _, _, index_dir = phase4_environment
    first = index_pdf("one.pdf", "Force and acceleration.")
    second = index_pdf("two.pdf", "Cell biology and mitochondria.")
    search = RetrievalService(model_name=MOCK_MODEL, embedding_service=FakeEmbeddingService(), vector_store=VectorStore(index_dir))
    assert search.search("force", document_id=first["id"]).passages
    assert documents.delete_document(first["id"])
    with database.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_chunks WHERE document_id=?", (first["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_index_status WHERE document_id=?", (first["id"],)).fetchone()[0] == 0
    all_results = search.search("force", top_k=10)
    assert all(result.document_id == second["id"] for result in all_results.passages)
    assert search.search("force", document_id=first["id"]).passages == ()


def test_source_citation_sanitizer_rejects_unknown_or_wrong_page_references(phase4_environment) -> None:
    saved = index_pdf("source.pdf", "Force and motion.")
    passage = RetrievalService(model_name=MOCK_MODEL, embedding_service=FakeEmbeddingService(), vector_store=VectorStore(phase4_environment[2])).search("force", document_id=saved["id"]).passages[0]
    result = sanitize_source_citations(
        f"Valid [Source 1, page {passage.page_number}], fake [Source 99, page 55], wrong [Source 1, page 99].",
        (passage,),
    )
    assert f"[Source 1, page {passage.page_number}]" in result
    assert result.count("[unverified source citation removed]") == 2


def test_chat_sources_persist_as_separate_source_metadata(phase4_environment) -> None:
    conversation = database.create_conversation(chat_mode="all_documents")
    sources = [{"source_id": "Source 1", "filename": "book.pdf", "page_number": 7, "snippet": "Evidence."}]
    assert database.save_chat_exchange(conversation, "Question?", "Answer [Source 1, page 7]", "unique", sources=sources)
    messages = database.get_chat_messages(conversation)
    assert messages[0]["sources"] == []
    assert messages[1]["sources"] == sources
    assert database.list_conversations(chat_mode="all_documents")[0]["id"] == conversation
    assert database.list_conversations(chat_mode="general") == []


def test_indexing_import_and_dashboard_do_not_load_or_download_model(phase4_environment, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("utils.embedding_service._load_sentence_transformer", lambda name: calls.append(name))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    assert calls == []
    app.button(key="nav_Semantic Search").click().run()
    assert not app.exception
    assert calls == []
