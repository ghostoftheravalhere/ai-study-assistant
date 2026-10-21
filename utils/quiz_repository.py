"""Transactional persistence for saved quizzes, attempts, and answer reviews."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from utils.database import get_connection
from utils.quiz_logic import DIFFICULTIES, MAX_QUIZ_QUESTIONS, MIN_QUIZ_QUESTIONS, score_quiz_answers


class QuizRepositoryError(RuntimeError):
    """Raised for safe, user-displayable quiz persistence errors."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _decode(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def create_quiz_and_attempt(
    *,
    title: str,
    source_document_id: str,
    source_filename: str,
    difficulty: str,
    requested_question_count: int,
    questions: Sequence[Mapping[str, Any]],
    timer_duration_seconds: int | None,
) -> tuple[str, str]:
    """Persist a validated quiz and its first attempt in one transaction."""
    clean_title = " ".join(title.split())
    if not clean_title or len(clean_title) > 120:
        raise QuizRepositoryError("Enter a quiz title between 1 and 120 characters.")
    if difficulty not in DIFFICULTIES:
        raise QuizRepositoryError("Choose a supported quiz difficulty.")
    if not MIN_QUIZ_QUESTIONS <= requested_question_count <= MAX_QUIZ_QUESTIONS:
        raise QuizRepositoryError("Choose between 5 and 30 requested questions.")
    if not MIN_QUIZ_QUESTIONS <= len(questions) <= requested_question_count:
        raise QuizRepositoryError("The generated quiz did not contain enough valid questions to start.")
    if timer_duration_seconds is not None and timer_duration_seconds <= 0:
        raise QuizRepositoryError("The quiz timer must be a positive duration.")

    quiz_id, attempt_id = _new_id(), _new_id()
    started_at = _iso()
    with get_connection() as connection:
        if not connection.execute("SELECT 1 FROM documents WHERE id = ?", (source_document_id,)).fetchone():
            raise QuizRepositoryError("The selected source document is no longer available.")
        connection.execute(
            """INSERT INTO quizzes (
                   id, title, source_document_id, source_filename, difficulty,
                   requested_question_count, timer_duration_seconds, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (quiz_id, clean_title, source_document_id, source_filename, difficulty,
             requested_question_count, timer_duration_seconds, started_at),
        )
        question_ids = []
        for position, question in enumerate(questions, start=1):
            question_id = str(question["question_id"])
            question_ids.append(question_id)
            connection.execute(
                """INSERT INTO quiz_questions (
                       id, quiz_id, position, question_text, options_json, correct_option_id,
                       explanation, topic, source_document_id, source_pages_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    question_id, quiz_id, position, question["question"],
                    json.dumps(question["options"], ensure_ascii=False, separators=(",", ":")),
                    question["correct_option_id"], question["explanation"], question.get("topic"),
                    question.get("source_document_id") or source_document_id,
                    json.dumps(question.get("source_pages", []), separators=(",", ":")),
                ),
            )
        _insert_attempt(connection, attempt_id, quiz_id, question_ids, started_at, timer_duration_seconds, None)
    return quiz_id, attempt_id


def _insert_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    quiz_id: str,
    question_ids: Sequence[str],
    started_at: str,
    timer_duration_seconds: int | None,
    original_attempt_id: str | None,
) -> None:
    if not question_ids:
        raise QuizRepositoryError("A quiz attempt must contain at least one question.")
    connection.execute(
        """INSERT INTO quiz_attempts (
               id, quiz_id, original_attempt_id, started_at, status, total_questions,
               total_possible_marks, timer_duration_seconds
           ) VALUES (?, ?, ?, ?, 'in_progress', ?, ?, ?)""",
        (attempt_id, quiz_id, original_attempt_id, started_at, len(question_ids), len(question_ids), timer_duration_seconds),
    )
    connection.executemany(
        "INSERT INTO quiz_attempt_questions (attempt_id, question_id, position) VALUES (?, ?, ?)",
        [(attempt_id, question_id, position) for position, question_id in enumerate(question_ids, start=1)],
    )


def create_new_attempt(
    quiz_id: str,
    *,
    original_attempt_id: str | None = None,
    question_ids: Sequence[str] | None = None,
    use_quiz_timer: bool = True,
) -> str:
    """Start a fresh full or selected-question attempt without altering history."""
    attempt_id, started_at = _new_id(), _iso()
    with get_connection() as connection:
        quiz = connection.execute(
            "SELECT timer_duration_seconds FROM quizzes WHERE id = ?", (quiz_id,)
        ).fetchone()
        if quiz is None:
            raise QuizRepositoryError("That saved quiz is no longer available.")
        if original_attempt_id:
            original = connection.execute(
                "SELECT 1 FROM quiz_attempts WHERE id = ? AND quiz_id = ? AND status IN ('completed', 'timed_out')",
                (original_attempt_id, quiz_id),
            ).fetchone()
            if original is None:
                raise QuizRepositoryError("Only a submitted quiz can be used to create a retry.")
        if question_ids is None:
            rows = connection.execute(
                "SELECT id FROM quiz_questions WHERE quiz_id = ? ORDER BY position", (quiz_id,)
            ).fetchall()
            selected_ids = [row["id"] for row in rows]
        else:
            requested_ids = set(question_ids)
            rows = connection.execute(
                "SELECT id FROM quiz_questions WHERE quiz_id = ? ORDER BY position", (quiz_id,)
            ).fetchall()
            all_ids = [row["id"] for row in rows]
            if not requested_ids or not requested_ids.issubset(set(all_ids)):
                raise QuizRepositoryError("The selected retry questions are invalid.")
            selected_ids = [question_id for question_id in all_ids if question_id in requested_ids]
        timer = quiz["timer_duration_seconds"] if use_quiz_timer else None
        _insert_attempt(connection, attempt_id, quiz_id, selected_ids, started_at, timer, original_attempt_id)
    return attempt_id


def save_attempt_answer(attempt_id: str, question_id: str, selected_option_id: str | None) -> None:
    """Save one answer while an attempt is in progress, validating scope and option."""
    with get_connection() as connection:
        attempt = connection.execute("SELECT status FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None or attempt["status"] != "in_progress":
            raise QuizRepositoryError("This quiz is no longer accepting answers.")
        question = connection.execute(
            """SELECT q.options_json FROM quiz_questions AS q
               JOIN quiz_attempt_questions AS aq ON aq.question_id = q.id
               WHERE aq.attempt_id = ? AND q.id = ?""",
            (attempt_id, question_id),
        ).fetchone()
        if question is None:
            raise QuizRepositoryError("That question is not part of this quiz attempt.")
        if selected_option_id is not None:
            options = _decode(question["options_json"], [])
            if not any(isinstance(option, dict) and option.get("id") == selected_option_id for option in options):
                raise QuizRepositoryError("Choose one of the options shown for this question.")
        connection.execute(
            """INSERT INTO quiz_answers (attempt_id, question_id, selected_option_id, is_correct, awarded_marks)
               VALUES (?, ?, ?, NULL, 0)
               ON CONFLICT(attempt_id, question_id) DO UPDATE SET
                   selected_option_id=excluded.selected_option_id, is_correct=NULL, awarded_marks=0""",
            (attempt_id, question_id, selected_option_id),
        )


def submit_attempt(attempt_id: str, *, timed_out: bool = False) -> dict[str, Any] | None:
    """Score and close an attempt atomically; a second submission returns None."""
    submitted_time = _now()
    with get_connection() as connection:
        attempt = connection.execute("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None:
            raise QuizRepositoryError("This quiz attempt could not be found.")
        if attempt["status"] != "in_progress":
            return None
        rows = connection.execute(
            """SELECT q.id AS question_id, q.question_text AS question, q.options_json,
                      q.correct_option_id, q.explanation, q.topic, q.source_document_id,
                      q.source_pages_json, a.selected_option_id
               FROM quiz_attempt_questions AS aq
               JOIN quiz_questions AS q ON q.id = aq.question_id
               LEFT JOIN quiz_answers AS a ON a.attempt_id = aq.attempt_id AND a.question_id = q.id
               WHERE aq.attempt_id = ? ORDER BY aq.position""",
            (attempt_id,),
        ).fetchall()
        if not rows:
            raise QuizRepositoryError("An empty quiz attempt cannot be submitted.")
        questions = [
            {"question_id": row["question_id"], "options": _decode(row["options_json"], []),
             "correct_option_id": row["correct_option_id"]}
            for row in rows
        ]
        selected = {row["question_id"]: row["selected_option_id"] for row in rows}
        score = score_quiz_answers(questions, selected)
        started = datetime.fromisoformat(attempt["started_at"])
        elapsed = max(0, int((submitted_time - started.astimezone(timezone.utc)).total_seconds()))
        timer = attempt["timer_duration_seconds"]
        timed_out = timed_out or (timer is not None and elapsed >= timer)
        if timed_out and timer is not None:
            elapsed = min(elapsed, timer)
        final_status = "timed_out" if timed_out else "completed"
        for result in score["evaluated_answers"]:
            connection.execute(
                """INSERT INTO quiz_answers (attempt_id, question_id, selected_option_id, is_correct, awarded_marks)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(attempt_id, question_id) DO UPDATE SET
                       selected_option_id=excluded.selected_option_id, is_correct=excluded.is_correct,
                       awarded_marks=excluded.awarded_marks""",
                (attempt_id, result["question_id"], result["selected_option_id"],
                 None if result["is_correct"] is None else int(result["is_correct"]), result["awarded_marks"]),
            )
        updated = connection.execute(
            """UPDATE quiz_attempts SET submitted_at=?, status=?, correct_answers=?, incorrect_answers=?,
                   unanswered_questions=?, total_questions=?, total_possible_marks=?, percentage=?, duration_seconds=?
               WHERE id=? AND status='in_progress'""",
            (submitted_time.isoformat(timespec="seconds"), final_status, score["correct_answers"],
             score["incorrect_answers"], score["unanswered_questions"], score["total_questions"],
             score["total_possible_marks"], score["percentage"], elapsed, attempt_id),
        )
        if updated.rowcount != 1:
            return None
        answered_count = score["correct_answers"] + score["incorrect_answers"]
        connection.executemany(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES ('question_practiced', 'Practised a quiz question', NULL, ?)",
            [(_iso(submitted_time),) for _ in range(answered_count)],
        )
        connection.execute(
            "INSERT INTO study_activity (activity_type, description, score, created_at) VALUES ('quiz_completed', 'Completed an interactive quiz', ?, ?)",
            (score["percentage"], _iso(submitted_time)),
        )
        return {
            "attempt_id": attempt_id,
            "status": final_status,
            "submitted_at": submitted_time.isoformat(timespec="seconds"),
            "duration_seconds": elapsed,
            **score,
        }


def cancel_attempt(attempt_id: str) -> bool:
    with get_connection() as connection:
        result = connection.execute(
            "UPDATE quiz_attempts SET status='cancelled' WHERE id=? AND status='in_progress'", (attempt_id,)
        )
    return result.rowcount == 1


def get_attempt(attempt_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """SELECT a.*, q.title, q.source_document_id, q.source_filename, q.difficulty,
                      q.created_at AS quiz_created_at
               FROM quiz_attempts AS a JOIN quizzes AS q ON q.id = a.quiz_id WHERE a.id = ?""",
            (attempt_id,),
        ).fetchone()
    return dict(row) if row else None


def get_attempt_answers(attempt_id: str) -> dict[str, str | None]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT question_id, selected_option_id FROM quiz_answers WHERE attempt_id = ?", (attempt_id,)
        ).fetchall()
    return {row["question_id"]: row["selected_option_id"] for row in rows}


def get_attempt_questions(attempt_id: str, *, for_review: bool = False) -> list[dict[str, Any]]:
    """Fetch active-safe question data, or answer keys only for post-submit review."""
    key_fields = ", q.correct_option_id, q.explanation" if for_review else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT q.id AS question_id, aq.position, q.question_text, q.options_json,
                       q.topic, q.source_document_id, q.source_pages_json{key_fields}
                FROM quiz_attempt_questions AS aq JOIN quiz_questions AS q ON q.id=aq.question_id
                WHERE aq.attempt_id=? ORDER BY aq.position""",
            (attempt_id,),
        ).fetchall()
        answers = {
            row["question_id"]: row
            for row in connection.execute(
                "SELECT question_id, selected_option_id, is_correct, awarded_marks FROM quiz_answers WHERE attempt_id=?",
                (attempt_id,),
            ).fetchall()
        }
    questions = []
    for row in rows:
        question = dict(row)
        question["options"] = _decode(question.pop("options_json"), [])
        question["source_pages"] = _decode(question.pop("source_pages_json"), [])
        answer = dict(answers[question["question_id"]]) if question["question_id"] in answers else {}
        question["selected_option_id"] = answer.get("selected_option_id")
        question["is_correct"] = answer.get("is_correct")
        question["awarded_marks"] = answer.get("awarded_marks", 0)
        questions.append(question)
    return questions


def get_attempt_review(attempt_id: str) -> dict[str, Any] | None:
    attempt = get_attempt(attempt_id)
    if attempt is None or attempt["status"] not in {"completed", "timed_out"}:
        return None
    attempt["questions"] = get_attempt_questions(attempt_id, for_review=True)
    return attempt


def list_attempt_history(
    *,
    query: str = "",
    source_document_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = """SELECT a.id AS attempt_id, a.quiz_id, a.original_attempt_id, a.started_at,
                    a.submitted_at, a.status, a.correct_answers, a.incorrect_answers,
                    a.unanswered_questions, a.total_questions, a.percentage, a.duration_seconds,
                    q.title, q.source_filename, q.source_document_id, q.difficulty
             FROM quiz_attempts AS a JOIN quizzes AS q ON q.id=a.quiz_id WHERE 1=1"""
    params: list[Any] = []
    if query.strip():
        sql += " AND (q.title LIKE ? OR q.source_filename LIKE ?)"
        match = f"%{query.strip()}%"
        params.extend((match, match))
    if source_document_id:
        sql += " AND q.source_document_id = ?"
        params.append(source_document_id)
    sql += " ORDER BY a.started_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def list_in_progress_attempts() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT a.id AS attempt_id, a.quiz_id, a.started_at, a.timer_duration_seconds,
                      a.total_questions, q.title, q.source_filename
               FROM quiz_attempts AS a JOIN quizzes AS q ON q.id=a.quiz_id
               WHERE a.status='in_progress' ORDER BY a.started_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_progress_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with get_connection() as connection:
        attempts = [dict(row) for row in connection.execute(
            """SELECT a.id AS attempt_id, a.submitted_at, a.status, a.total_questions,
                      a.correct_answers, a.incorrect_answers, a.unanswered_questions,
                      a.percentage, a.duration_seconds, q.title, q.source_filename, q.difficulty
               FROM quiz_attempts AS a JOIN quizzes AS q ON q.id=a.quiz_id
               WHERE a.status IN ('completed', 'timed_out') ORDER BY a.submitted_at"""
        ).fetchall()]
        answers = [dict(row) for row in connection.execute(
            """SELECT a.selected_option_id, a.is_correct, q.topic
               FROM quiz_answers AS a JOIN quiz_questions AS q ON q.id=a.question_id
               JOIN quiz_attempts AS t ON t.id=a.attempt_id
               WHERE t.status IN ('completed', 'timed_out')"""
        ).fetchall()]
    return attempts, answers
