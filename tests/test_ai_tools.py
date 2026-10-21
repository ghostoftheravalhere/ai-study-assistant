"""Mocked tests for Gemini service, conversation storage, and Phase 3 UI."""

import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import streamlit as st
from google.genai import errors, types
from streamlit.testing.v1 import AppTest

from utils import database
from utils.gemini_service import (
    GeminiConfigurationError,
    GeminiResponseError,
    GeminiService,
    GeminiServiceError,
    is_gemini_configured,
    load_gemini_api_key,
    load_gemini_model,
    parse_question_response,
    select_document_context,
)


@pytest.fixture
def ai_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "phase3-test.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.initialize_database()
    return db_path


class FakeModels:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result or SimpleNamespace(text="A clear model response.")
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_service(
    *,
    text: str = "A clear model response.",
    error: Exception | None = None,
    api_key: str = "test-key-never-display",
) -> tuple[GeminiService, FakeModels, FakeClient]:
    models = FakeModels(SimpleNamespace(text=text), error)
    client = FakeClient(models)
    service = GeminiService(api_key=api_key, model="test-model", client_factory=lambda **_: client)
    return service, models, client


def test_app_and_ai_pages_import_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not is_gemini_configured()
    for module_name in ("app", "pages.ai_chat", "pages.summarizer", "pages.question_generator"):
        assert importlib.import_module(module_name)


def test_missing_key_fails_before_creating_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client_calls: list[bool] = []
    service = GeminiService(api_key="", client_factory=lambda **_: client_calls.append(True))
    with pytest.raises(GeminiConfigurationError, match="GEMINI_API_KEY") as error:
        service.answer_chat([], "Explain photosynthesis")
    assert not client_calls
    assert "test-key-never-display" not in str(error.value)


def test_key_and_model_load_from_environment_or_secrets_without_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert load_gemini_api_key({"GEMINI_API_KEY": "secret-value"}) == "secret-value"
    assert load_gemini_model({"GEMINI_MODEL": "gemini-custom"}) == "gemini-custom"
    monkeypatch.setenv("GEMINI_API_KEY", "environment-secret")
    monkeypatch.setenv("GEMINI_MODEL", "environment-model")
    assert load_gemini_api_key({"GEMINI_API_KEY": "different-secret"}) == "environment-secret"
    assert load_gemini_model({"GEMINI_MODEL": "different-model"}) == "environment-model"
    assert "environment-secret" not in repr(GeminiService())


def test_database_migrates_phase_two_schema_without_losing_documents_or_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "phase2.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE study_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, activity_type TEXT NOT NULL,
                description TEXT NOT NULL, score REAL, created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE documents (
                id TEXT PRIMARY KEY, original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL UNIQUE,
                file_size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL, uploaded_at TEXT NOT NULL,
                extraction_status TEXT NOT NULL, extracted_text TEXT NOT NULL, content_sha256 TEXT NOT NULL UNIQUE
            )"""
        )
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, ?, ?)",
            ("quiz_completed", "Existing Phase 1 activity", 87, "2026-10-20T10:00+00:00"),
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("existing-doc", "existing.pdf", "safe-existing.pdf", 50, 1, "2026-10-20", "ready", "Existing extracted text", "existing-hash"),
        )

    database.initialize_database()
    database.initialize_database()
    with sqlite3.connect(db_path) as connection:
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        activity_count = connection.execute("SELECT COUNT(*) FROM study_activity").fetchone()[0]
        chat_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")}
    assert {"chat_conversations", "chat_messages"}.issubset(table_names)
    assert {"conversation_id", "role", "content", "request_id"}.issubset(chat_columns)
    assert document_count == 1
    assert activity_count == 1
    assert database.get_dashboard_stats()["documents"] == 1


def test_chat_messages_persist_and_duplicate_request_is_ignored(ai_database: Path) -> None:
    conversation_id = database.create_conversation()
    assert database.save_chat_exchange(conversation_id, "What is a cell?", "The basic unit of life.", "request-1")
    assert not database.save_chat_exchange(conversation_id, "What is a cell?", "The basic unit of life.", "request-1")
    messages = database.get_chat_messages(conversation_id)
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "What is a cell?"),
        ("assistant", "The basic unit of life."),
    ]
    assert database.get_dashboard_stats()["quizzes"] == 0
    assert database.get_recent_activity()[0]["description"] == "Answered an AI study question"


def test_document_chat_conversations_are_separated_by_source(ai_database: Path) -> None:
    first = database.create_conversation("doc-1")
    second = database.create_conversation("doc-2")
    general = database.create_conversation()
    assert [item["id"] for item in database.list_conversations("doc-1")] == [first]
    assert [item["id"] for item in database.list_conversations("doc-2")] == [second]
    assert [item["id"] for item in database.list_conversations()] == [general]


def test_conversation_deletion_cascades_only_to_its_messages(ai_database: Path) -> None:
    first = database.create_conversation()
    second = database.create_conversation()
    database.save_chat_exchange(first, "A", "B", "request-a")
    database.save_chat_exchange(second, "C", "D", "request-c")
    assert database.delete_conversation(first)
    assert database.get_chat_messages(first) == []
    assert len(database.get_chat_messages(second)) == 2


def test_document_selection_returns_only_the_requested_database_text(ai_database: Path) -> None:
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("doc-1", "biology.pdf", "safe.pdf", 100, 2, "2026-10-21", "ready", "Cell biology content", "sha-1"),
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("doc-2", "history.pdf", "safe2.pdf", 100, 1, "2026-10-21", "ready", "History content", "sha-2"),
        )
    from utils.documents import get_document, list_documents

    available = list_documents()
    assert {item["id"] for item in available} == {"doc-1", "doc-2"}
    assert get_document("doc-1")["extracted_text"] == "Cell biology content"


def test_selected_document_text_is_sent_to_the_gemini_request() -> None:
    service, models, client = make_service()
    result = service.answer_chat([], "What does it say?", document_text="Private selected PDF text")
    call = models.calls[0]
    sent_content = call["contents"][-1].parts[0].text
    assert "Private selected PDF text" in sent_content
    assert "What does it say?" in sent_content
    assert "history.pdf" not in sent_content
    assert call["model"] == "test-model"
    assert "untrusted" in call["config"].system_instruction
    assert result.text == "A clear model response."
    assert client.closed


def test_empty_document_text_is_rejected_without_model_call() -> None:
    service, models, _ = make_service()
    with pytest.raises(GeminiServiceError, match="no selectable text"):
        service.answer_chat([], "Question", document_text="  \n  ")
    assert models.calls == []


def test_document_context_is_deterministically_limited() -> None:
    selection = select_document_context("0123456789ABCDEFGHIJ", max_chars=10)
    assert selection.text == "0123456789"
    assert selection.original_length == 20
    assert selection.truncated
    whole_text = select_document_context("short", max_chars=10)
    assert not whole_text.truncated


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "API key"),
        (429, "rate limit or quota"),
        (404, "model is unavailable"),
        (503, "temporarily unavailable"),
    ],
)
def test_gemini_api_errors_are_mapped_without_exposing_details(status: int, message: str) -> None:
    secret_marker = "PRIVATE_TEST_KEY_DO_NOT_PRINT"
    service, _, _ = make_service(error=errors.APIError(status, {"message": secret_marker}), api_key=secret_marker)
    with pytest.raises(GeminiServiceError, match=message) as error:
        service.answer_chat([], "hello")
    assert secret_marker not in str(error.value)


def test_gemini_network_errors_are_friendly() -> None:
    request = httpx.Request("POST", "https://example.invalid")
    service, _, _ = make_service(error=httpx.ConnectError("offline", request=request))
    with pytest.raises(GeminiServiceError, match="internet connection"):
        service.answer_chat([], "hello")


def test_summary_response_uses_selected_text_and_configured_format() -> None:
    service, models, _ = make_service(text="- Topic A\n- Topic B")
    result = service.summarize_document("Cell structures and functions", "Medium")
    assert result.text.startswith("- Topic A")
    assert "Cell structures and functions" in models.calls[0]["contents"]
    assert "balanced summary" in models.calls[0]["contents"]


def test_question_json_is_validated_and_malformed_output_rejected() -> None:
    valid = {
        "questions": [
            {
                "question": "Which organelle makes ATP?",
                "choices": ["Nucleus", "Mitochondrion", "Ribosome", "Golgi body"],
                "expected_answer": "Mitochondrion",
                "explanation": "It performs cellular respiration.",
            }
        ]
    }
    parsed = parse_question_response(
        "```json\n" + json.dumps(valid) + "\n```",
        question_type="Multiple choice",
        count=1,
        include_answers=True,
        include_explanations=True,
    )
    assert parsed[0]["expected_answer"] == "Mitochondrion"
    with pytest.raises(GeminiResponseError, match="unreadable"):
        parse_question_response("not JSON", question_type="Short answer", count=1, include_answers=False, include_explanations=False)
    invalid = {"questions": [{"question": "Q?", "choices": ["A", "B", "C", "D"], "expected_answer": "Z"}]}
    with pytest.raises(GeminiResponseError, match="did not match"):
        parse_question_response(json.dumps(invalid), question_type="Multiple choice", count=1, include_answers=True, include_explanations=False)


def test_summary_and_question_activity_is_recorded_only_after_success(ai_database: Path) -> None:
    service, _, _ = make_service(error=errors.APIError(503, {"message": "offline"}))
    with pytest.raises(GeminiServiceError):
        service.summarize_document("study text", "Short")
    assert database.get_recent_activity() == []
    database.record_ai_activity("summary_generated", "Generated a study summary")
    database.record_ai_activity("questions_generated", "Generated study questions")
    descriptions = {item["description"] for item in database.get_recent_activity()}
    assert descriptions == {"Generated a study summary", "Generated study questions"}
    assert database.get_dashboard_stats()["quizzes"] == 0


def test_sidebar_dashboard_and_ai_pages_render_without_a_key(
    ai_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run()
    assert not app.exception
    assert any("Documents Uploaded" in item.value for item in app.markdown)
    app.button(key="nav_AI Chat").click().run()
    assert not app.exception
    assert any("GEMINI_API_KEY" in item.value for item in app.info)
    app.button(key="nav_My Documents").click().run()
    assert not app.exception
    app.button(key="nav_AI Summarizer").click().run()
    assert not app.exception
    app.button(key="nav_Question Generator").click().run()
    assert not app.exception
    app.button(key="nav_Settings").click().run()
    assert not app.exception
