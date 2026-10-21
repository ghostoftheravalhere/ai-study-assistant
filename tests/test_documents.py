"""Automated tests for Phase 2 document storage and application integration."""

import ast
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import fitz
import pytest

from utils import database, documents
from utils.documents import DocumentError, DuplicateDocumentError
from utils.pdf_processing import PDFProcessingError, extract_pdf_text


@pytest.fixture
def document_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    db_path = tmp_path / "study_assistant.db"
    storage_path = tmp_path / "user-data" / "documents"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", storage_path)
    database.initialize_database()
    return db_path, storage_path


def make_pdf(*pages: str) -> bytes:
    pdf = fitz.open()
    for page_text in pages:
        page = pdf.new_page()
        if page_text:
            page.insert_text((72, 72), page_text)
    contents = pdf.tobytes()
    pdf.close()
    return contents


def test_fresh_database_and_phase_one_schema_are_preserved(document_environment: tuple[Path, Path]) -> None:
    db_path, _ = document_environment
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, ?, ?)",
            ("quiz_completed", "Existing Phase 1 record", 90, "2026-01-01T12:00+00:00"),
        )
    database.initialize_database()
    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        activity_count = connection.execute("SELECT COUNT(*) FROM study_activity").fetchone()[0]
    assert {"study_activity", "documents"}.issubset(tables)
    assert activity_count == 1
    assert database.get_dashboard_stats()["quizzes"] == 1


def test_extract_pdf_text_preserves_pages_and_page_count() -> None:
    result = extract_pdf_text(make_pdf("Algebra fundamentals", "Linear equations"))
    assert result.page_count == 2
    assert result.extraction_status == "ready"
    assert "--- Page 1 ---" in result.extracted_text
    assert "--- Page 2 ---" in result.extracted_text
    assert "Linear equations" in result.extracted_text


def test_empty_upload_is_rejected() -> None:
    with pytest.raises(PDFProcessingError, match="empty"):
        extract_pdf_text(b"")


def test_corrupted_pdf_is_rejected() -> None:
    with pytest.raises(PDFProcessingError, match="damaged or unreadable"):
        extract_pdf_text(b"%PDF-1.7\nnot really a PDF")


def test_pdf_without_selectable_text_is_detected() -> None:
    result = extract_pdf_text(make_pdf(""))
    assert result.page_count == 1
    assert result.extraction_status == "no_text"
    assert result.extracted_text == ""


def test_password_protected_pdf_is_rejected() -> None:
    pdf = fitz.open()
    pdf.new_page()
    encrypted_bytes = pdf.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    pdf.close()
    with pytest.raises(PDFProcessingError, match="Password-protected"):
        extract_pdf_text(encrypted_bytes)


def test_upload_persists_metadata_text_and_safe_storage_name(document_environment: tuple[Path, Path]) -> None:
    _, storage_path = document_environment
    saved = documents.save_uploaded_pdf("../../Course Notes.pdf", make_pdf("Cell biology"))
    listed = documents.list_documents()
    loaded = documents.get_document(saved["id"])

    assert saved["original_filename"] == "Course Notes.pdf"
    assert saved["page_count"] == 1
    assert saved["file_size_bytes"] > 0
    assert saved["uploaded_at"]
    assert saved["extraction_status"] == "ready"
    assert "Cell biology" in loaded["extracted_text"]
    assert len(listed) == 1
    assert Path(saved["stored_filename"]).name == saved["stored_filename"]
    assert (storage_path / saved["stored_filename"]).is_file()


def test_stored_document_symlink_outside_storage_is_rejected(document_environment: tuple[Path, Path], tmp_path: Path) -> None:
    _, storage_path = document_environment
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(make_pdf("private outside file"))
    storage_path.mkdir(parents=True, exist_ok=True)
    link = storage_path / "linked.pdf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this Windows setup.")
    with pytest.raises(DocumentError, match="symbolic link"):
        documents.resolve_stored_document_path("linked.pdf")


def test_invalid_extension_and_pdf_content_are_rejected() -> None:
    with pytest.raises(DocumentError, match="Only PDF"):
        documents.save_uploaded_pdf("notes.txt", b"text")
    with pytest.raises(DocumentError, match="does not appear to be a PDF"):
        documents.save_uploaded_pdf("notes.pdf", b"not a PDF")


def test_empty_upload_is_rejected_by_document_service() -> None:
    with pytest.raises(DocumentError, match="empty"):
        documents.save_uploaded_pdf("empty.pdf", b"")


def test_configured_upload_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "MAX_UPLOAD_SIZE_BYTES", 4)
    monkeypatch.setattr(documents, "MAX_UPLOAD_SIZE_MB", 1)
    with pytest.raises(DocumentError, match="1 MB upload limit"):
        documents.save_uploaded_pdf("large.pdf", b"%PDF-1.7 oversized")


def test_duplicate_content_is_rejected_without_second_file(document_environment: tuple[Path, Path]) -> None:
    _, storage_path = document_environment
    pdf_bytes = make_pdf("Same content")
    documents.save_uploaded_pdf("one.pdf", pdf_bytes)
    with pytest.raises(DuplicateDocumentError, match="already stored"):
        documents.save_uploaded_pdf("two.pdf", pdf_bytes)
    assert len(documents.list_documents()) == 1
    assert len(list(storage_path.glob("*.pdf"))) == 1


def test_delete_removes_only_selected_document(document_environment: tuple[Path, Path]) -> None:
    _, storage_path = document_environment
    first = documents.save_uploaded_pdf("first.pdf", make_pdf("First"))
    second = documents.save_uploaded_pdf("second.pdf", make_pdf("Second"))

    assert documents.delete_document(first["id"])
    assert documents.get_document(first["id"]) is None
    assert documents.get_document(second["id"]) is not None
    assert not (storage_path / first["stored_filename"]).exists()
    assert (storage_path / second["stored_filename"]).exists()


def test_delete_succeeds_if_pdf_file_is_missing(document_environment: tuple[Path, Path]) -> None:
    saved = documents.save_uploaded_pdf("missing.pdf", make_pdf("Missing file"))
    (documents.DOCUMENTS_DIR / saved["stored_filename"]).unlink()
    assert documents.delete_document(saved["id"])
    assert documents.get_document(saved["id"]) is None


def test_delete_restores_pdf_if_database_delete_fails(
    document_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, storage_path = document_environment
    saved = documents.save_uploaded_pdf("restore.pdf", make_pdf("Keep this file"))
    original_connection = documents.get_connection
    connection_calls = 0

    @contextmanager
    def fail_delete_connection():
        nonlocal connection_calls
        connection_calls += 1
        if connection_calls == 2:
            raise sqlite3.OperationalError("simulated database failure")
        with original_connection() as connection:
            yield connection

    monkeypatch.setattr(documents, "get_connection", fail_delete_connection)
    with pytest.raises(DocumentError, match="could not be deleted"):
        documents.delete_document(saved["id"])
    assert (storage_path / saved["stored_filename"]).is_file()
    monkeypatch.setattr(documents, "get_connection", original_connection)
    assert documents.get_document(saved["id"]) is not None


def test_dashboard_document_count_uses_stored_documents(document_environment: tuple[Path, Path]) -> None:
    assert database.get_dashboard_stats()["documents"] == 0
    documents.save_uploaded_pdf("counted.pdf", make_pdf("Count me"))
    assert database.get_dashboard_stats()["documents"] == 1


def test_python_source_files_parse_without_syntax_errors() -> None:
    root = Path(__file__).resolve().parents[1]
    for source in root.rglob("*.py"):
        if ".venv" not in source.parts and "__pycache__" not in source.parts:
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def test_dashboard_navigation_regression(document_environment: tuple[Path, Path]) -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    for page in ("AI Chat", "My Documents", "AI Summarizer", "Question Generator", "Practice Quiz", "Study Progress", "Settings", "Dashboard"):
        app.button(key=f"nav_{page}").click().run()
        assert not app.exception
