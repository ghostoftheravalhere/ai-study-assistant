"""SQLite helpers for real study activity records."""

import sqlite3
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Mapping, Sequence

from utils.config import DATABASE_PATH

DATABASE_SCHEMA_VERSION = 10


@contextmanager
def get_connection(database_path=None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection and always close it after use."""
    connection = sqlite3.connect(database_path or DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database(database_path=None) -> None:
    """Create new tables without changing or clearing existing user data."""
    try:
        database_path = database_path or DATABASE_PATH
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with get_connection(database_path) as connection:
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if current_version > DATABASE_SCHEMA_VERSION:
                raise sqlite3.DatabaseError("The database was created by a newer StudyMate version.")
            # Keep additive DDL, column backfills, indexes, and user_version in
            # one transaction so an interrupted upgrade can be retried cleanly.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS study_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    score REAL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL UNIQUE,
                    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes > 0),
                    page_count INTEGER NOT NULL CHECK (page_count > 0),
                    uploaded_at TEXT NOT NULL,
                    extraction_status TEXT NOT NULL CHECK (extraction_status IN ('ready', 'no_text')),
                    extracted_text TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL UNIQUE
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS chat_conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_document_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_conversations)")}
            if "chat_mode" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE chat_conversations ADD COLUMN chat_mode TEXT NOT NULL DEFAULT 'general'"
                )
                connection.execute(
                    "UPDATE chat_conversations SET chat_mode = 'document' WHERE source_document_id IS NOT NULL"
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    request_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    sources_json TEXT
                )"""
            )
            message_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")}
            if "sources_json" not in message_columns:
                connection.execute("ALTER TABLE chat_messages ADD COLUMN sources_json TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS document_index_status (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status IN ('not_indexed', 'indexed', 'out_of_date', 'missing_source', 'no_text', 'failed')),
                    source_sha256 TEXT,
                    model_name TEXT,
                    embedding_dimension INTEGER,
                    index_config_hash TEXT,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    indexed_at TEXT,
                    error_message TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS document_chunks (
                    faiss_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT NOT NULL UNIQUE,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    chunk_text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
                    embedding_vector BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, position)
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_model ON document_chunks(embedding_model, embedding_dimension)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS document_ocr_pages (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    status TEXT NOT NULL CHECK (status IN ('native_text', 'pending', 'blank', 'not_needed', 'complete', 'empty', 'failed')),
                    recognized_text TEXT NOT NULL DEFAULT '',
                    language TEXT,
                    dpi INTEGER,
                    engine_version TEXT,
                    confidence REAL,
                    recognized_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY(document_id, page_number)
                )"""
            )
            ocr_columns = {row[1] for row in connection.execute("PRAGMA table_info(document_ocr_pages)")}
            if "dpi" not in ocr_columns:
                connection.execute("ALTER TABLE document_ocr_pages ADD COLUMN dpi INTEGER")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_document_ocr_status ON document_ocr_pages(document_id, status, page_number)")
            connection.execute(
                """INSERT OR IGNORE INTO document_index_status (document_id, status)
                   SELECT id, 'not_indexed' FROM documents"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quizzes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    source_filename TEXT NOT NULL,
                    difficulty TEXT NOT NULL CHECK (difficulty IN ('Easy', 'Medium', 'Hard', 'Mixed')),
                    requested_question_count INTEGER NOT NULL CHECK (requested_question_count BETWEEN 5 AND 30),
                    timer_duration_seconds INTEGER CHECK (timer_duration_seconds IS NULL OR timer_duration_seconds > 0),
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quiz_questions (
                    id TEXT PRIMARY KEY,
                    quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position > 0),
                    question_text TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_option_id TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    topic TEXT,
                    source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    source_pages_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(quiz_id, position)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quiz_attempts (
                    id TEXT PRIMARY KEY,
                    quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                    original_attempt_id TEXT REFERENCES quiz_attempts(id) ON DELETE SET NULL,
                    started_at TEXT NOT NULL,
                    submitted_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'timed_out', 'cancelled')),
                    correct_answers INTEGER,
                    incorrect_answers INTEGER,
                    unanswered_questions INTEGER,
                    total_questions INTEGER NOT NULL CHECK (total_questions >= 0),
                    total_possible_marks INTEGER NOT NULL CHECK (total_possible_marks >= 0),
                    percentage REAL,
                    duration_seconds INTEGER,
                    timer_duration_seconds INTEGER CHECK (timer_duration_seconds IS NULL OR timer_duration_seconds > 0)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quiz_attempt_questions (
                    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
                    question_id TEXT NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position > 0),
                    PRIMARY KEY (attempt_id, question_id),
                    UNIQUE (attempt_id, position)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quiz_answers (
                    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
                    question_id TEXT NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
                    selected_option_id TEXT,
                    is_correct INTEGER CHECK (is_correct IS NULL OR is_correct IN (0, 1)),
                    awarded_marks REAL NOT NULL DEFAULT 0 CHECK (awarded_marks >= 0),
                    PRIMARY KEY (attempt_id, question_id)
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_source ON quizzes(source_document_id, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quiz_questions_quiz ON quiz_questions(quiz_id, position)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_history ON quiz_attempts(status, submitted_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_quiz ON quiz_attempts(quiz_id, started_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempt_questions_attempt ON quiz_attempt_questions(attempt_id, position)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_quiz_answers_question ON quiz_answers(question_id, is_correct)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flashcard_decks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT,
                    source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    source_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flashcards (
                    id TEXT PRIMARY KEY,
                    deck_id TEXT NOT NULL REFERENCES flashcard_decks(id) ON DELETE RESTRICT,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',
                    topic TEXT,
                    difficulty TEXT,
                    source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    source_filename TEXT,
                    source_pages_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flashcard_schedule (
                    card_id TEXT PRIMARY KEY REFERENCES flashcards(id) ON DELETE CASCADE,
                    review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
                    successful_recall_count INTEGER NOT NULL DEFAULT 0 CHECK (successful_recall_count >= 0),
                    lapse_count INTEGER NOT NULL DEFAULT 0 CHECK (lapse_count >= 0),
                    interval_days REAL NOT NULL DEFAULT 0 CHECK (interval_days >= 0),
                    ease_factor REAL NOT NULL DEFAULT 2.5 CHECK (ease_factor >= 1.3),
                    due_at TEXT NOT NULL,
                    last_reviewed_at TEXT,
                    learning_state TEXT NOT NULL DEFAULT 'new' CHECK (learning_state IN ('new', 'learning', 'review'))
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flashcard_reviews (
                    id TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL REFERENCES flashcards(id) ON DELETE RESTRICT,
                    session_id TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK (rating IN ('again', 'hard', 'good', 'easy')),
                    reviewed_at TEXT NOT NULL,
                    previous_due_at TEXT NOT NULL,
                    new_due_at TEXT NOT NULL,
                    previous_interval_days REAL NOT NULL,
                    new_interval_days REAL NOT NULL,
                    was_new INTEGER NOT NULL DEFAULT 0 CHECK (was_new IN (0, 1)),
                    recall_successful INTEGER NOT NULL CHECK (recall_successful IN (0, 1)),
                    UNIQUE(session_id, card_id)
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_flashcard_decks_active ON flashcard_decks(archived_at, title COLLATE NOCASE)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_flashcards_deck ON flashcards(deck_id, archived_at, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_flashcard_schedule_due ON flashcard_schedule(due_at, learning_state)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_card ON flashcard_reviews(card_id, reviewed_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_date ON flashcard_reviews(reviewed_at DESC)")
            # Phase 9 adds local planner tables only; existing study, quiz, and
            # flashcard records remain authoritative and untouched.
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_settings (
                id INTEGER PRIMARY KEY CHECK (id=1), daily_goal_minutes INTEGER NOT NULL DEFAULT 60 CHECK(daily_goal_minutes BETWEEN 0 AND 1440),
                available_minutes INTEGER NOT NULL DEFAULT 120 CHECK(available_minutes BETWEEN 1 AND 1440),
                study_days TEXT NOT NULL DEFAULT '[0,1,2,3,4]', max_tasks_per_day INTEGER NOT NULL DEFAULT 4 CHECK(max_tasks_per_day BETWEEN 1 AND 20), updated_at TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_subjects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, archived_at TEXT)""")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_planner_subject_active_name ON planner_subjects(name COLLATE NOCASE) WHERE archived_at IS NULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_exams (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, subject_id TEXT REFERENCES planner_subjects(id) ON DELETE SET NULL,
                subject_name TEXT NOT NULL DEFAULT '', exam_date TEXT NOT NULL, start_time TEXT, location TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'upcoming' CHECK(status IN ('upcoming','completed','archived')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_topics (
                id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES planner_subjects(id) ON DELETE RESTRICT,
                exam_id TEXT REFERENCES planner_exams(id) ON DELETE SET NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                estimated_minutes INTEGER NOT NULL DEFAULT 60 CHECK(estimated_minutes BETWEEN 1 AND 100000), priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
                source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL, source_filename TEXT, completed_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', task_type TEXT NOT NULL,
                subject_id TEXT REFERENCES planner_subjects(id) ON DELETE SET NULL, topic_id TEXT REFERENCES planner_topics(id) ON DELETE SET NULL,
                exam_id TEXT REFERENCES planner_exams(id) ON DELETE SET NULL, document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                quiz_id TEXT REFERENCES quizzes(id) ON DELETE SET NULL,
                document_filename TEXT, planned_date TEXT NOT NULL, estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes BETWEEN 1 AND 1440),
                priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5), status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','completed','cancelled','missed')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, completed_minutes INTEGER CHECK(completed_minutes IS NULL OR completed_minutes BETWEEN 1 AND 1440),
                unique_key TEXT UNIQUE)""")
            task_columns = {row[1] for row in connection.execute("PRAGMA table_info(planner_tasks)")}
            if "quiz_id" not in task_columns:
                connection.execute("ALTER TABLE planner_tasks ADD COLUMN quiz_id TEXT REFERENCES quizzes(id) ON DELETE SET NULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS planner_sessions (
                id TEXT PRIMARY KEY, task_id TEXT REFERENCES planner_tasks(id) ON DELETE SET NULL,
                subject_id TEXT REFERENCES planner_subjects(id) ON DELETE SET NULL, topic_id TEXT REFERENCES planner_topics(id) ON DELETE SET NULL,
                exam_id TEXT REFERENCES planner_exams(id) ON DELETE SET NULL, duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 1 AND 1440),
                note TEXT NOT NULL DEFAULT '', started_at TEXT, completed_at TEXT NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_planner_tasks_date_status ON planner_tasks(planned_date,status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_planner_tasks_exam ON planner_tasks(exam_id,planned_date)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_planner_topics_subject ON planner_topics(subject_id,archived_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_planner_sessions_completed ON planner_sessions(completed_at DESC)")
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(f"Could not initialize the local database at {DATABASE_PATH}: {error}") from error


def record_activity(activity_type: str, description: str, score: float | None = None) -> None:
    """Record a completed user action; intended for later feature pages."""
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, ?, ?)",
            (activity_type, description, score, datetime.now().astimezone().isoformat(timespec="minutes")),
        )


def get_dashboard_stats() -> dict[str, int | float]:
    """Return counts and the average score from recorded activity only."""
    with get_connection() as connection:
        document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        rows = connection.execute(
            """SELECT activity_type, COUNT(*) AS total, AVG(score) AS average_score
               FROM study_activity GROUP BY activity_type"""
        ).fetchall()
    stats: dict[str, int | float] = {"documents": document_count, "questions": 0, "quizzes": 0, "average_score": 0}
    for row in rows:
        if row["activity_type"] == "question_practiced":
            stats["questions"] = row["total"]
        elif row["activity_type"] == "quiz_completed":
            stats["quizzes"] = row["total"]
            stats["average_score"] = round(row["average_score"] or 0)
    return stats


def get_recent_activity(limit: int = 5) -> list[dict[str, str | float | None]]:
    """Return recent recorded activities for the dashboard."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT description, score, created_at FROM study_activity ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_ai_activity(activity_type: str, description: str) -> None:
    """Record a successful AI operation without storing its prompt or result."""
    allowed_types = {"ai_chat_response", "summary_generated", "questions_generated", "flashcards_generated"}
    if activity_type not in allowed_types:
        raise ValueError("Unsupported AI activity type.")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, NULL, ?)",
            (activity_type, description, datetime.now().astimezone().isoformat(timespec="minutes")),
        )


def create_conversation(source_document_id: str | None = None, chat_mode: str | None = None) -> str:
    """Create a local chat thread, optionally bound to one document."""
    chat_mode = chat_mode or ("document" if source_document_id else "general")
    if chat_mode not in {"general", "document", "all_documents"}:
        raise ValueError("Unsupported chat scope.")
    conversation_id = uuid.uuid4().hex
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO chat_conversations (id, title, source_document_id, created_at, updated_at, chat_mode) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, "New conversation", source_document_id, now, now, chat_mode),
        )
    return conversation_id


def list_conversations(
    source_document_id: str | None = None,
    chat_mode: str | None = None,
) -> list[dict[str, str | None]]:
    """List conversations for the selected chat mode/document."""
    chat_mode = chat_mode or ("document" if source_document_id else "general")
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, title, source_document_id, created_at, updated_at, chat_mode
               FROM chat_conversations WHERE source_document_id IS ? AND chat_mode = ?
               ORDER BY updated_at DESC, created_at DESC""",
            (source_document_id, chat_mode),
        ).fetchall()
    return [dict(row) for row in rows]


def get_chat_messages(conversation_id: str) -> list[dict[str, str]]:
    """Load a conversation's messages in chronological order."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT role, content, created_at, sources_json FROM chat_messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
    messages = []
    for row in rows:
        message = dict(row)
        try:
            message["sources"] = json.loads(message.pop("sources_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            message["sources"] = []
        messages.append(message)
    return messages


def save_chat_exchange(
    conversation_id: str,
    user_message: str,
    assistant_message: str,
    request_id: str,
    sources: Sequence[Mapping[str, object]] | None = None,
) -> bool:
    """Atomically persist one exchange and its activity, idempotently by request ID."""
    if not user_message.strip() or not assistant_message.strip() or not request_id:
        raise ValueError("A chat exchange requires both messages and a request ID.")
    now = datetime.now().astimezone().isoformat(timespec="microseconds")
    sources_json = json.dumps(list(sources or []), ensure_ascii=False, separators=(",", ":"))
    with get_connection() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM chat_messages WHERE request_id = ?", (request_id,)
        ).fetchone()
        if duplicate:
            return False

        conversation = connection.execute(
            "SELECT title FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise ValueError("The selected conversation no longer exists.")

        connection.execute(
            "INSERT INTO chat_messages (id, conversation_id, role, content, request_id, created_at) VALUES (?, ?, 'user', ?, ?, ?)",
            (uuid.uuid4().hex, conversation_id, user_message, request_id, now),
        )
        connection.execute(
            "INSERT INTO chat_messages (id, conversation_id, role, content, request_id, created_at, sources_json) VALUES (?, ?, 'assistant', ?, NULL, ?, ?)",
            (uuid.uuid4().hex, conversation_id, assistant_message, now, sources_json),
        )
        title = conversation["title"]
        if title == "New conversation":
            title = " ".join(user_message.split())[:72] or "Study conversation"
        connection.execute(
            "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conversation_id),
        )
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, NULL, ?)",
            ("ai_chat_response", "Answered an AI study question", now),
        )
    return True


def delete_conversation(conversation_id: str) -> bool:
    """Delete one conversation and its messages."""
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM chat_conversations WHERE id = ?", (conversation_id,))
    return cursor.rowcount == 1
