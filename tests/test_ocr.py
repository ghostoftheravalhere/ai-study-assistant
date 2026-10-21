"""Local OCR, page assessment, persistence, and effective-text tests."""

import io
import sqlite3
from pathlib import Path

import fitz
from PIL import Image
import pytest

from utils import database, documents, indexing_service
from utils.document_text_service import get_effective_document, get_effective_pages
from utils.documents import get_ocr_page_status, save_uploaded_pdf
from utils.indexing_service import index_document
from utils.ocr_service import OCRError, TesseractEngine, run_document_ocr
from utils.pdf_processing import PDFProcessingError, assess_pdf_pages


@pytest.fixture
def ocr_env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "study.db")
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", tmp_path / "documents")
    database.initialize_database()
    return tmp_path


def scan_pdf(*, native_pages=(), total_pages=1):
    image = Image.new("RGB", (350, 350), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()
    pdf = fitz.open()
    for number in range(total_pages):
        page = pdf.new_page(width=400, height=400)
        if number in native_pages:
            page.insert_text((40, 50), "Native selectable text for a complete page.")
        else:
            page.insert_image(fitz.Rect(20, 20, 380, 380), stream=png)
    result = pdf.tobytes()
    pdf.close()
    return result


class FakeEngine:
    def __init__(self, outputs=None, fail_pages=()):
        self.outputs = outputs or {}
        self.fail_pages = set(fail_pages)
        self.calls = []

    def languages(self):
        return {"eng", "fra"}

    def recognize(self, image, language, timeout):
        self.calls.append((image.size, language, timeout))
        call = len(self.calls)
        if call in self.fail_pages:
            raise OCRError("OCR timed out on this page. Retry the document or use a lower OCR resolution.")
        return self.outputs.get(call, f"Recognized study text on page {call}.")

    def version(self):
        return "fake-tesseract-5"


def test_assessment_skips_native_and_identifies_scanned_mixed_blank_and_low_text_pages():
    scanned = scan_pdf(native_pages={0}, total_pages=3)
    _, pages = assess_pdf_pages(scanned)
    assert [page.status for page in pages] == ["native_text", "pending", "pending"]
    assert pages[0].native_text.startswith("Native selectable")

    blank = fitz.open()
    blank.new_page()
    blank_bytes = blank.tobytes()
    blank.close()
    _, blank_pages = assess_pdf_pages(blank_bytes)
    assert blank_pages[0].status == "blank"

    low_text = fitz.open()
    page = low_text.new_page()
    page.insert_text((72, 72), "A")
    low_bytes = low_text.tobytes()
    low_text.close()
    _, low_pages = assess_pdf_pages(low_bytes)
    assert low_pages[0].status == "not_needed"


@pytest.mark.parametrize("pdf_bytes, message", [(b"%PDF-bad", "damaged"), (b"", "readable")])
def test_damaged_pdf_assessment_fails_safely(pdf_bytes, message):
    with pytest.raises(PDFProcessingError, match=message):
        assess_pdf_pages(pdf_bytes)


def test_encrypted_pdf_assessment_reports_password_requirement():
    pdf = fitz.open()
    pdf.new_page()
    data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    pdf.close()
    with pytest.raises(PDFProcessingError, match="Password-protected"):
        assess_pdf_pages(data)


def test_local_ocr_saves_page_numbers_language_engine_and_effective_text(ocr_env):
    saved = save_uploaded_pdf("mixed.pdf", scan_pdf(native_pages={0}, total_pages=3))
    engine = FakeEngine()
    result = run_document_ocr(saved["id"], engine=engine, dpi=200)
    assert (result.processed, result.succeeded, result.remaining) == (2, 2, 0)
    assert len(engine.calls) == 2 and engine.calls[0][1:] == ("eng", 60)
    full = get_effective_document(saved["id"])
    assert "Native selectable text" in full["effective_text"]
    assert "Recognized study text on page 1" in full["effective_text"]
    assert "Page 2" in full["effective_text"] and "Page 3" in full["effective_text"]
    assert {page.source for page in full["effective_pages"]} == {"native", "ocr"}
    with database.get_connection() as connection:
        rows = connection.execute("SELECT page_number,status,language,dpi,engine_version,recognized_text FROM document_ocr_pages ORDER BY page_number").fetchall()
    assert [(row["page_number"], row["status"]) for row in rows] == [(1, "native_text"), (2, "complete"), (3, "complete")]
    assert rows[1]["language"] == "eng" and rows[1]["engine_version"] == "fake-tesseract-5"
    assert rows[1]["dpi"] == 200


def test_normal_text_page_does_not_invoke_ocr(ocr_env):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((50, 50), "This is selectable native text that exceeds the OCR threshold.")
    data = pdf.tobytes()
    pdf.close()
    saved = save_uploaded_pdf("native.pdf", data)
    engine = FakeEngine()
    result = run_document_ocr(saved["id"], engine=engine)
    assert result.processed == 0 and engine.calls == []


def test_native_only_pdf_does_not_initialize_tesseract(ocr_env):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((50, 50), "This is sufficient selectable native text on this PDF page.")
    data = pdf.tobytes(); pdf.close()
    saved = save_uploaded_pdf("native-only.pdf", data)

    class MustNotStart:
        def languages(self):
            raise AssertionError("Tesseract should not start for native pages")

    assert run_document_ocr(saved["id"], engine=MustNotStart()).processed == 0


def test_ocr_only_document_becomes_bulk_index_eligible_without_prior_index_row(ocr_env, monkeypatch):
    saved = save_uploaded_pdf("scan-only.pdf", scan_pdf())
    run_document_ocr(saved["id"], engine=FakeEngine())
    monkeypatch.setattr(indexing_service, "EMBEDDING_MODEL_NAME", "mock-ocr-model")
    status = next(row for row in indexing_service.get_document_index_statuses() if row["id"] == saved["id"])
    assert status["status"] == "not_indexed"

    class Embed:
        model_name = "mock-ocr-model"
        dimension = 2
        def embed(self, texts, batch_size=32):
            return [[0.1, 0.2] for _ in texts]

    results = indexing_service.index_pending_documents(embedding_service=Embed())
    assert any(item.document_id == saved["id"] and item.status == "indexed" for item in results)


def test_pre_phase8_document_page_assessment_is_backfilled_lazily(ocr_env):
    saved = save_uploaded_pdf("legacy.pdf", scan_pdf())
    with database.get_connection() as connection:
        connection.execute("DELETE FROM document_ocr_pages WHERE document_id=?", (saved["id"],))
    states = documents.assess_stored_document_pages(saved["id"])
    assert states["pending"] == 1


def test_document_library_exposes_explicit_ocr_action_and_reports_completion(ocr_env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pages import documents as documents_page

    saved = save_uploaded_pdf("visible-scan.pdf", scan_pdf())
    original_runner = run_document_ocr
    monkeypatch.setattr(
        documents_page,
        "run_document_ocr",
        lambda document_id, **kwargs: original_runner(document_id, engine=FakeEngine(), **kwargs),
    )
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    app.session_state["active_page"] = "My Documents"
    app.run()
    assert not app.exception
    assert app.button(key=f"run_ocr_{saved['id']}").label == "Run local OCR · 1 page(s)"
    app.button(key=f"run_ocr_{saved['id']}").click().run()
    assert not app.exception
    assert get_ocr_page_status(saved["id"])["complete"] == 1
    assert any("OCR recognized text on all 1 processed page(s)" in item.value for item in app.success)
    assert get_ocr_page_status(saved["id"]).get("pending", 0) == 0


def test_empty_output_and_failed_page_are_not_falsely_text_ready_and_can_retry(ocr_env):
    saved = save_uploaded_pdf("scanned.pdf", scan_pdf(total_pages=2))
    engine = FakeEngine(outputs={1: "   "}, fail_pages={2})
    result = run_document_ocr(saved["id"], engine=engine)
    assert result.processed == 2 and result.empty == 1 and result.failed == 1 and result.remaining == 2
    assert not get_effective_document(saved["id"])["text_ready"]
    retry = FakeEngine()
    second = run_document_ocr(saved["id"], engine=retry)
    assert second.succeeded == 2 and second.remaining == 0
    assert get_effective_document(saved["id"])["text_ready"]


def test_missing_language_and_invalid_config_fail_before_page_processing(ocr_env):
    saved = save_uploaded_pdf("scanned.pdf", scan_pdf())
    with pytest.raises(OCRError, match="language data is missing"):
        run_document_ocr(saved["id"], engine=FakeEngine(), language="deu")
    with pytest.raises(OCRError, match="language code"):
        run_document_ocr(saved["id"], engine=FakeEngine(), language="eng;bad")
    with pytest.raises(OCRError, match="between 72 and 300"):
        run_document_ocr(saved["id"], engine=FakeEngine(), dpi=500)


def test_missing_tesseract_executable_has_windows_setup_guidance(monkeypatch):
    from utils import ocr_service

    monkeypatch.setattr(ocr_service, "TESSERACT_CMD", "Z:/missing/tesseract.exe")
    with pytest.raises(OCRError, match="STUDY_ASSISTANT_TESSERACT_CMD"):
        TesseractEngine().languages()


def test_language_change_reprocesses_page_and_failure_preserves_previous_success(ocr_env):
    saved = save_uploaded_pdf("scanned.pdf", scan_pdf())
    run_document_ocr(saved["id"], engine=FakeEngine(), language="eng")
    previous = get_effective_document(saved["id"])["effective_text"]
    failed = run_document_ocr(saved["id"], engine=FakeEngine(fail_pages={1}), language="fra")
    assert failed.failed == 1
    after = get_effective_document(saved["id"])
    assert after["effective_text"] == previous
    with database.get_connection() as connection:
        row = connection.execute("SELECT status,language,recognized_text,last_error FROM document_ocr_pages WHERE document_id=?", (saved["id"],)).fetchone()
    assert row["status"] == "complete" and row["language"] == "eng" and row["recognized_text"]
    assert row["last_error"]


def test_resolution_change_reprocesses_completed_ocr_and_saves_setting(ocr_env):
    saved = save_uploaded_pdf("scan.pdf", scan_pdf())
    run_document_ocr(saved["id"], engine=FakeEngine(), dpi=200)
    engine = FakeEngine()
    result = run_document_ocr(saved["id"], engine=engine, dpi=250)
    assert result.succeeded == 1 and len(engine.calls) == 1
    with database.get_connection() as connection:
        row = connection.execute("SELECT dpi,language FROM document_ocr_pages WHERE document_id=?", (saved["id"],)).fetchone()
    assert row["dpi"] == 250 and row["language"] == "eng"


def test_partial_progress_is_persisted_and_original_document_and_quiz_source_survive(ocr_env):
    saved = save_uploaded_pdf("scan.pdf", scan_pdf(total_pages=3))
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO quizzes(id,title,source_document_id,source_filename,difficulty,requested_question_count,created_at) VALUES('q','quiz',?,'scan.pdf','Easy',5,'now')",
            (saved["id"],),
        )
        connection.execute("INSERT INTO flashcard_decks(id,title,source_document_id,source_filename,created_at,updated_at) VALUES('source-deck','saved deck',?,'scan.pdf','now','now')", (saved["id"],))
        connection.execute("INSERT INTO flashcards(id,deck_id,front,back,source_document_id,source_filename,created_at,updated_at) VALUES('source-card','source-deck','front','back',?,'scan.pdf','now','now')", (saved["id"],))
        connection.execute("INSERT INTO flashcard_schedule(card_id,due_at) VALUES('source-card','2099-01-01')")
        connection.execute("INSERT INTO flashcard_reviews(id,card_id,session_id,rating,reviewed_at,previous_due_at,new_due_at,previous_interval_days,new_interval_days,recall_successful) VALUES('review','source-card','session','easy','now','now','later',0,4,1)")
    progress = []
    result = run_document_ocr(saved["id"], engine=FakeEngine(fail_pages={2}), progress_callback=lambda done, total: progress.append((done, total)))
    assert result.succeeded == 2 and result.failed == 1
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert Path(documents.resolve_stored_document_path(saved["stored_filename"])).exists()
    assert get_ocr_page_status(saved["id"])["complete"] == 2
    documents.delete_document(saved["id"])
    with database.get_connection() as connection:
        quiz = connection.execute("SELECT source_document_id,source_filename FROM quizzes WHERE id='q'").fetchone()
        ocr_rows = connection.execute("SELECT COUNT(*) FROM document_ocr_pages WHERE document_id=?", (saved["id"],)).fetchone()[0]
    assert quiz["source_document_id"] is None and quiz["source_filename"] == "scan.pdf"
    with database.get_connection() as connection:
        card = connection.execute("SELECT source_document_id,source_filename FROM flashcards WHERE id='source-card'").fetchone()
        review = connection.execute("SELECT COUNT(*) FROM flashcard_reviews WHERE id='review'").fetchone()[0]
    assert card["source_document_id"] is None and card["source_filename"] == "scan.pdf"
    assert review == 1
    assert ocr_rows == 0


def test_ocr_marks_existing_index_stale_and_indexing_uses_actual_pdf_pages(ocr_env, monkeypatch):
    saved = save_uploaded_pdf("scan.pdf", scan_pdf(native_pages={0}, total_pages=2))
    monkeypatch.setattr(indexing_service, "EMBEDDING_MODEL_NAME", "mock-ocr-model")

    class Embed:
        model_name = "mock-ocr-model"
        dimension = 2
        def embed(self, texts, batch_size=32):
            return [[0.1, 0.2] for _ in texts]

    first = index_document(saved["id"], embedding_service=Embed())
    assert first.status == "indexed"
    run_document_ocr(saved["id"], engine=FakeEngine())
    from utils.indexing_service import get_document_index_statuses, list_active_chunks
    status = next(row for row in get_document_index_statuses() if row["id"] == saved["id"])
    assert status["status"] == "out_of_date"
    assert list_active_chunks("mock-ocr-model", saved["id"]) == []
    rebuilt = index_document(saved["id"], embedding_service=Embed(), force=True)
    assert rebuilt.status == "indexed"
    chunks = list_active_chunks("mock-ocr-model", saved["id"])
    assert {chunk["page_number"] for chunk in chunks} == {1, 2}
    assert any("Recognized study text" in chunk["chunk_text"] for chunk in chunks)
    from utils.retrieval_service import RetrievalService
    from utils.vector_store import VectorMatch

    class FirstPassageStore:
        def search(self, records, query_vector, **kwargs):
            return [VectorMatch(records[-1].faiss_id, 0.99)]

    retrieved = RetrievalService(
        model_name="mock-ocr-model", embedding_service=Embed(), vector_store=FirstPassageStore(),
    ).search("recognized", document_id=saved["id"])
    assert retrieved.passages[0].page_number == 2
    assert "Recognized study text" in retrieved.passages[0].text


def test_document_migration_adds_ocr_table_without_changing_existing_document_and_history(ocr_env):
    saved = save_uploaded_pdf("before.pdf", scan_pdf(native_pages={0}, total_pages=2))
    with database.get_connection() as connection:
        connection.execute("INSERT INTO flashcard_decks(id,title,created_at,updated_at) VALUES('deck','saved deck','now','now')")
        connection.execute("INSERT INTO flashcards(id,deck_id,front,back,created_at,updated_at) VALUES('card','deck','front','back','now','now')")
        connection.execute("INSERT INTO flashcard_schedule(card_id,due_at) VALUES('card','2099-01-01')")
        connection.execute("INSERT INTO flashcard_reviews(id,card_id,session_id,rating,reviewed_at,previous_due_at,new_due_at,previous_interval_days,new_interval_days,recall_successful) VALUES('review','card','session','good','now','now','later',0,2,1)")
    database.initialize_database()
    assert documents.get_document(saved["id"])["extracted_text"]
    with database.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM flashcards WHERE id='card'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM flashcard_reviews WHERE id='review'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_ocr_pages WHERE document_id=?", (saved["id"],)).fetchone()[0] == 2


def test_effective_page_text_uses_ocr_for_insufficient_native_and_does_not_duplicate(ocr_env):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "A")
    image = Image.new("RGB", (350, 350), "white")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    page.insert_image(fitz.Rect(20, 20, 580, 780), stream=buffer.getvalue())
    data = pdf.tobytes(); pdf.close()
    saved = save_uploaded_pdf("hybrid.pdf", data)
    run_document_ocr(saved["id"], engine=FakeEngine())
    effective = get_effective_document(saved["id"])
    assert "Recognized study text" in effective["effective_text"]
    assert "\nA\n" not in effective["effective_text"]
    assert len([page for page in get_effective_pages(saved["id"]) if page.text]) == 1


def test_quiz_and_flashcard_generation_receive_effective_ocr_text_and_real_page_numbers(monkeypatch):
    text = "--- Page 7 ---\nRecognized OCR material about cellular respiration."

    class FakeGemini:
        def generate_quiz_questions(self, context, **kwargs):
            assert "Recognized OCR material" in context
            assert kwargs["allowed_source_pages"] == {7}
            return []

        def generate_flashcards(self, context, **kwargs):
            assert "Recognized OCR material" in context
            assert kwargs["allowed_source_pages"] == {7}
            return type("Result", (), {"cards": [], "context_truncated": False, "original_context_length": len(context)})()

    from utils import flashcard_service, quiz_service
    monkeypatch.setattr(quiz_service, "list_active_chunks", lambda *args: [])
    monkeypatch.setattr(flashcard_service, "list_active_chunks", lambda *args: [])
    quiz = quiz_service.generate_document_quiz(
        {"id": "doc", "effective_text": text}, count=5, difficulty="Easy", gemini_service=FakeGemini(),
    )
    cards = flashcard_service.generate_document_flashcards(
        {"id": "doc", "effective_text": text}, count=5, complexity="Basic", style="Definitions",
        gemini_service=FakeGemini(),
    )
    assert quiz.context_source == "bounded extracted PDF text"
    assert cards.cards == []
