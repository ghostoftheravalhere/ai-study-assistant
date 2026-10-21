"""SQLite helpers for real study activity records."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from utils.config import DATABASE_PATH


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection and always close it after use."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database() -> None:
    """Create new tables without changing or clearing existing user data."""
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with get_connection() as connection:
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
            connection.execute(
                """CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    request_id TEXT UNIQUE,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, created_at)"
            )
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
    allowed_types = {"ai_chat_response", "summary_generated", "questions_generated"}
    if activity_type not in allowed_types:
        raise ValueError("Unsupported AI activity type.")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES (?, ?, NULL, ?)",
            (activity_type, description, datetime.now().astimezone().isoformat(timespec="minutes")),
        )


def create_conversation(source_document_id: str | None = None) -> str:
    """Create a local chat thread, optionally bound to one document."""
    import uuid

    conversation_id = uuid.uuid4().hex
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO chat_conversations (id, title, source_document_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, "New conversation", source_document_id, now, now),
        )
    return conversation_id


def list_conversations(source_document_id: str | None = None) -> list[dict[str, str | None]]:
    """List conversations for the selected chat mode/document."""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, title, source_document_id, created_at, updated_at
               FROM chat_conversations WHERE source_document_id IS ?
               ORDER BY updated_at DESC, created_at DESC""",
            (source_document_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_chat_messages(conversation_id: str) -> list[dict[str, str]]:
    """Load a conversation's messages in chronological order."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_chat_exchange(
    conversation_id: str,
    user_message: str,
    assistant_message: str,
    request_id: str,
) -> bool:
    """Atomically persist one exchange and its activity, idempotently by request ID."""
    if not user_message.strip() or not assistant_message.strip() or not request_id:
        raise ValueError("A chat exchange requires both messages and a request ID.")
    now = datetime.now().astimezone().isoformat(timespec="microseconds")
    import uuid

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
            "INSERT INTO chat_messages (id, conversation_id, role, content, request_id, created_at) VALUES (?, ?, 'assistant', ?, NULL, ?)",
            (uuid.uuid4().hex, conversation_id, assistant_message, now),
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
