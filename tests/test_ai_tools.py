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
from utils import gemini_service as gemini_service_module
from utils.gemini_service import (
    GEMINI_API_MODE_STATE_KEY,
    PERSONAL_GEMINI_API_KEY_STATE_KEY,
    GeminiConfigurationError,
    GeminiResponseError,
    GeminiService,
    GeminiServiceError,
    get_gemini_service_for_session,
    is_gemini_available_for_session,
    is_gemini_configured,
    load_gemini_api_key,
    load_gemini_api_keys,
    load_gemini_model,
    parse_question_response,
    select_document_context,
)


@pytest.fixture(autouse=True)
def isolate_gemini_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", *(f"GEMINI_API_KEY_{index}" for index in range(1, 5))):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(gemini_service_module, "_read_secret_source", lambda: {})


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


def make_failover_service(
    keys: list[str], outcomes: dict[str, Any]
) -> tuple[GeminiService, list[str], list[FakeClient]]:
    attempted: list[str] = []
    clients: list[FakeClient] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        outcome = outcomes[api_key]
        error = outcome if isinstance(outcome, Exception) else None
        models = FakeModels(SimpleNamespace(text=outcome) if error is None else None, error)
        client = FakeClient(models)
        clients.append(client)
        return client

    return GeminiService(api_keys=keys, model="test-model", client_factory=factory), attempted, clients


def test_app_and_ai_pages_import_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not is_gemini_configured()
    for module_name in ("app", "study_pages.ai_chat", "study_pages.summarizer", "study_pages.question_generator"):
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


def test_no_keys_one_key_four_keys_and_legacy_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_gemini_api_keys() == []
    monkeypatch.setenv("GEMINI_API_KEY_1", "one-key")
    assert [key.value for key in load_gemini_api_keys()] == ["one-key"]
    for index in range(2, 5):
        monkeypatch.setenv(f"GEMINI_API_KEY_{index}", f"key-{index}")
    assert [key.value for key in load_gemini_api_keys()] == ["one-key", "key-2", "key-3", "key-4"]
    monkeypatch.delenv("GEMINI_API_KEY_1")
    for index in range(2, 5):
        monkeypatch.delenv(f"GEMINI_API_KEY_{index}")
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")
    assert load_gemini_api_key() == "legacy-key"


def test_empty_duplicate_environment_and_secret_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "   ")
    monkeypatch.setenv("GEMINI_API_KEY_2", "same")
    monkeypatch.setenv("GEMINI_API_KEY_3", "same")
    keys = load_gemini_api_keys({"GEMINI_API_KEY_1": "same", "GEMINI_API_KEY_4": "secret-four"})
    assert [(key.slot, key.value) for key in keys] == [("key 1", "same"), ("key 4", "secret-four")]
    assert "same" not in repr(keys)


def test_environment_values_override_streamlit_secrets_by_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_2", "environment-two")
    keys = load_gemini_api_keys({"GEMINI_API_KEY_1": "secret-one", "GEMINI_API_KEY_2": "secret-two"})
    assert [(key.slot, key.value) for key in keys] == [
        ("key 1", "secret-one"),
        ("key 2", "environment-two"),
    ]


def test_service_uses_streamlit_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gemini_service_module,
        "_read_secret_source",
        lambda: {"GEMINI_API_KEY_2": "streamlit-secret-two", "GEMINI_MODEL": "secret-model"},
    )
    models = FakeModels()
    client = FakeClient(models)
    client_keys: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        client_keys.append(api_key)
        return client

    result = GeminiService(client_factory=factory).answer_chat([], "hello")
    assert result.text == "A clear model response."
    assert client_keys == ["streamlit-secret-two"]


def test_personal_key_takes_precedence_over_configured_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "application-key-do-not-use")
    attempted: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return FakeClient(FakeModels())

    service = GeminiService(
        personal_api_key="student-personal-key",
        api_keys=["explicit-application-key"],
        client_factory=factory,
    )
    service.answer_chat([], "Explain a concept")
    assert attempted == ["student-personal-key"]


def test_session_key_selection_uses_app_keys_when_personal_mode_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "application-key")
    attempted: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return FakeClient(FakeModels())

    state = {GEMINI_API_MODE_STATE_KEY: "application", PERSONAL_GEMINI_API_KEY_STATE_KEY: "stored-but-inactive"}
    assert is_gemini_available_for_session(state)
    get_gemini_service_for_session(state, client_factory=factory).answer_chat([], "hello")
    assert attempted == ["application-key"]


def test_empty_personal_key_is_rejected_without_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "application-key-must-not-be-used")
    attempted: list[str] = []
    state = {GEMINI_API_MODE_STATE_KEY: "personal", PERSONAL_GEMINI_API_KEY_STATE_KEY: "  "}
    assert not is_gemini_available_for_session(state)
    service = get_gemini_service_for_session(state, client_factory=lambda **kwargs: attempted.append(kwargs))
    with pytest.raises(GeminiConfigurationError, match="personal Gemini API key is empty"):
        service.test_connection()
    assert attempted == []


def test_clearing_session_personal_key_restores_application_key_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "application-key")
    state = {GEMINI_API_MODE_STATE_KEY: "personal", PERSONAL_GEMINI_API_KEY_STATE_KEY: "student-key"}
    personal_attempts: list[str] = []
    app_attempts: list[str] = []
    get_gemini_service_for_session(
        state,
        client_factory=lambda **kwargs: (personal_attempts.append(kwargs["api_key"]) or FakeClient(FakeModels())),
    ).answer_chat([], "personal request")
    state.pop(PERSONAL_GEMINI_API_KEY_STATE_KEY)
    state[GEMINI_API_MODE_STATE_KEY] = "application"
    get_gemini_service_for_session(
        state,
        client_factory=lambda **kwargs: (app_attempts.append(kwargs["api_key"]) or FakeClient(FakeModels())),
    ).answer_chat([], "application request")
    assert personal_attempts == ["student-key"]
    assert app_attempts == ["application-key"]


def test_personal_auth_failure_is_safe_and_never_falls_back_to_app_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "student-private-key-marker"
    monkeypatch.setenv("GEMINI_API_KEY_1", "owner-key-must-not-be-used")
    attempted: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return FakeClient(FakeModels(error=errors.APIError(403, {"message": secret})))

    service = GeminiService(personal_api_key=secret, client_factory=factory)
    with pytest.raises(GeminiServiceError, match="personal API key") as error:
        service.answer_chat([], "hello")
    assert attempted == [secret]
    assert secret not in str(error.value)
    assert "owner-key-must-not-be-used" not in str(error.value)


def test_session_a_personal_key_is_not_selected_for_session_b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "application-key")
    attempted: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return FakeClient(FakeModels())

    session_a = {GEMINI_API_MODE_STATE_KEY: "personal", PERSONAL_GEMINI_API_KEY_STATE_KEY: "session-a-key"}
    session_b = {GEMINI_API_MODE_STATE_KEY: "application"}
    get_gemini_service_for_session(session_a, client_factory=factory).answer_chat([], "A")
    get_gemini_service_for_session(session_b, client_factory=factory).answer_chat([], "B")
    assert attempted == ["session-a-key", "application-key"]


def test_connection_test_is_a_small_explicit_request_and_uses_mocked_client() -> None:
    models = FakeModels(SimpleNamespace(text="OK"))
    client = FakeClient(models)
    attempted: list[str] = []

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return client

    service = GeminiService(personal_api_key="private-connection-key", model="test-model", client_factory=factory)
    service.test_connection()
    request = models.calls[0]
    assert attempted == ["private-connection-key"]
    assert request["model"] == "test-model"
    assert request["config"].max_output_tokens == 8
    assert "private-connection-key" not in str(request["contents"])
    assert client.closed


def test_session_personal_key_never_enters_sqlite_or_chat_records(ai_database: Path) -> None:
    secret = "private-session-key-marker"
    conversation_id = database.create_conversation()
    database.save_chat_exchange(conversation_id, "hello", "safe answer", "byok-test-request")
    with sqlite3.connect(ai_database) as connection:
        all_text = " ".join(
            str(value)
            for table in ("chat_conversations", "chat_messages", "study_activity")
            for row in connection.execute(f"SELECT * FROM {table}")
            for value in row
        )
        columns = {row[1].casefold() for row in connection.execute("PRAGMA table_info(chat_conversations)")}
    assert secret not in all_text
    assert not any("api_key" in column or "credential" in column for column in columns)


def test_personal_key_errors_do_not_leak_through_logs_or_user_error(caplog: pytest.LogCaptureFixture) -> None:
    secret = "private-key-error-marker"
    service, _, _ = make_service(error=errors.APIError(403, {"message": secret}), api_key=secret)
    with pytest.raises(GeminiServiceError) as error:
        GeminiService(personal_api_key=secret, client_factory=service._client_factory).answer_chat([], "hello")
    assert secret not in str(error.value)
    assert secret not in caplog.text


def test_personal_session_service_routes_chat_rag_summary_and_questions_through_one_key() -> None:
    secret = "student-session-key"
    question_json = json.dumps({"questions": [{"question": "What?", "expected_answer": "This."}]})

    class QueuedModels:
        def __init__(self) -> None:
            self.responses = ["General answer.", "RAG answer.", "A study summary.", question_json]
            self.calls: list[dict[str, Any]] = []

        def generate_content(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return SimpleNamespace(text=self.responses.pop(0))

    models = QueuedModels()
    attempted: list[str] = []
    client = FakeClient(models)

    def factory(*, api_key: str) -> FakeClient:
        attempted.append(api_key)
        return client

    state = {GEMINI_API_MODE_STATE_KEY: "personal", PERSONAL_GEMINI_API_KEY_STATE_KEY: secret}
    service = get_gemini_service_for_session(state, client_factory=factory)
    assert service.answer_chat([], "general").text == "General answer."
    assert service.answer_chat([], "RAG", retrieved_context="Retrieved source passage.").text == "RAG answer."
    assert service.summarize_document("Study facts", "Short").text == "A study summary."
    questions = service.generate_questions(
        "Study facts", question_type="Short answer", difficulty="Easy", count=1,
        include_answers=True, include_explanations=False,
    )
    assert questions.questions[0]["expected_answer"] == "This."
    assert attempted == [secret] * 4


def test_api_settings_page_exposes_masked_key_and_explicit_test_controls(ai_database: Path) -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run()
    app.button(key="nav_Settings").click().run()
    assert not app.exception
    assert any("API Settings" in item.value for item in app.markdown)
    app.radio(key=GEMINI_API_MODE_STATE_KEY).set_value("personal").run()
    assert any(
        item.label == "Your Gemini API Key"
        and item.proto.DESCRIPTOR.fields_by_name["type"].enum_type.values_by_number[item.proto.type].name == "PASSWORD"
        for item in app.text_input
    )
    assert any(item.label == "Test Connection" for item in app.button)


def test_api_settings_activation_and_clear_are_session_only(ai_database: Path) -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run()
    app.button(key="nav_Settings").click().run()
    app.radio(key=GEMINI_API_MODE_STATE_KEY).set_value("personal").run()
    app.text_input(key="personal_gemini_api_key_input").set_value("student-session-only-secret").run()
    app.button(key="activate_personal_gemini_key").click().run()
    assert not app.exception
    assert app.session_state[PERSONAL_GEMINI_API_KEY_STATE_KEY] == "student-session-only-secret"
    assert app.session_state[GEMINI_API_MODE_STATE_KEY] == "personal"
    assert app.session_state["personal_gemini_api_key_input"] == ""
    assert not any("student-session-only-secret" in item.value for item in app.markdown)
    app.button(key="clear_personal_gemini_key").click().run()
    assert not app.exception
    assert PERSONAL_GEMINI_API_KEY_STATE_KEY not in app.session_state
    assert app.session_state[GEMINI_API_MODE_STATE_KEY] == "application"


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


def test_retrieved_document_context_is_sent_instead_of_full_document_text() -> None:
    service, models, client = make_service()
    context = '[{"source_id":"Source 1","filename":"biology.pdf","page_number":4,"passage":"Cell division."}]'
    result = service.answer_chat([], "What does it say?", retrieved_context=context)
    call = models.calls[0]
    sent_content = call["contents"][-1].parts[0].text
    assert "Cell division." in sent_content
    assert "biology.pdf" in sent_content and "page_number" in sent_content
    assert "What does it say?" in sent_content
    assert "history.pdf" not in sent_content
    assert call["model"] == "test-model"
    assert "untrusted" in call["config"].system_instruction
    assert "citation" in call["config"].system_instruction
    assert result.text == "A clear model response."
    assert client.closed


def test_empty_retrieved_context_is_rejected_without_model_call() -> None:
    service, models, _ = make_service()
    with pytest.raises(GeminiServiceError, match="No relevant passages"):
        service.answer_chat([], "Question", retrieved_context="  \n  ")
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


def test_authentication_failure_uses_next_key_and_succeeds() -> None:
    secret = "key-secret-one"
    service, attempted, clients = make_failover_service(
        [secret, "key-secret-two"],
        {secret: errors.APIError(401, {"status": "UNAUTHENTICATED"}), "key-secret-two": "success"},
    )
    assert service.answer_chat([], "hello").text == "success"
    assert attempted == [secret, "key-secret-two"]
    assert all(client.closed for client in clients)


def test_first_configured_key_succeeds_without_using_later_keys() -> None:
    service, attempted, _ = make_failover_service(["first", "second"], {"first": "success", "second": "unused"})
    assert service.answer_chat([], "hello").text == "success"
    assert attempted == ["first"]


def test_rate_limit_triggers_bounded_failover() -> None:
    service, attempted, _ = make_failover_service(
        ["one", "two", "three"],
        {"one": errors.APIError(429, {"status": "RESOURCE_EXHAUSTED"}), "two": "success", "three": "unused"},
    )
    assert service.summarize_document("study text", "Short").text == "success"
    assert attempted == ["one", "two"]


def test_all_four_keys_fail_without_exceeding_four_attempts_or_leaking_keys() -> None:
    keys = [f"private-key-{index}" for index in range(1, 6)]
    outcomes = {key: errors.APIError(403, {"status": "PERMISSION_DENIED"}) for key in keys}
    service, attempted, _ = make_failover_service(keys, outcomes)
    with pytest.raises(GeminiServiceError, match="No configured Gemini API key could complete") as error:
        service.generate_questions(
            "study text", question_type="Short answer", difficulty="Easy", count=1,
            include_answers=False, include_explanations=False,
        )
    assert attempted == keys[:4]
    assert len(set(attempted)) == 4
    assert all(key not in str(error.value) for key in keys)
    assert "key 1" in str(error.value) and "key 4" in str(error.value)


def test_invalid_request_does_not_fail_over() -> None:
    service, attempted, _ = make_failover_service(
        ["one", "two"], {"one": errors.APIError(400, {"status": "INVALID_ARGUMENT"}), "two": "unused"}
    )
    with pytest.raises(GeminiServiceError, match="HTTP 400"):
        service.answer_chat([], "hello")
    assert attempted == ["one"]


def test_timeout_does_not_resubmit_with_another_key() -> None:
    timeout = httpx.ReadTimeout("timed out", request=httpx.Request("POST", "https://example.invalid"))
    service, attempted, _ = make_failover_service(["one", "two"], {"one": timeout, "two": "unused"})
    with pytest.raises(GeminiServiceError, match="not resubmitted"):
        service.answer_chat([], "hello")
    assert attempted == ["one"]


def test_authentication_error_does_not_expose_credentials_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    secret = "super-secret-key-value"
    service, _, _ = make_failover_service([secret], {secret: errors.APIError(401, {"message": secret})})
    with pytest.raises(GeminiServiceError) as error:
        service.answer_chat([], "hello")
    assert secret not in str(error.value)
    assert error.value.__cause__ is None
    assert secret not in caplog.text


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
