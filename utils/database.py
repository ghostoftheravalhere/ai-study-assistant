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
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database() -> None:
    """Create the minimal activity table used for dashboard statistics."""
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
        rows = connection.execute(
            """SELECT activity_type, COUNT(*) AS total, AVG(score) AS average_score
               FROM study_activity GROUP BY activity_type"""
        ).fetchall()
    stats: dict[str, int | float] = {"documents": 0, "questions": 0, "quizzes": 0, "average_score": 0}
    for row in rows:
        if row["activity_type"] == "document_uploaded":
            stats["documents"] = row["total"]
        elif row["activity_type"] == "question_practiced":
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
