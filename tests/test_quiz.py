"""Phase 6 validation, scoring, persistence, and local analytics tests."""

import json
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import database
from utils.gemini_service import GeminiService
from utils.quiz_logic import (
    QuizValidationError,
    calculate_progress_analytics,
    parse_generated_quiz_questions,
    score_quiz_answers,
)
from utils.quiz_repository import (
    QuizRepositoryError,
    create_new_attempt,
    create_quiz_and_attempt,
    get_attempt,
    get_attempt_questions,
    get_attempt_review,
    get_progress_records,
    list_attempt_history,
    save_attempt_answer,
    submit_attempt,
)


@pytest.fixture
def quiz_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "phase6.sqlite3")
    database.initialize_database()
    document_id = "source-document"
    with database.get_connection() as connection:
        connection.execute(
            """INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (document_id, "study.pdf", "stored.pdf", 100, 2, "2026-01-01T00:00", "ready", "--- Page 1 --- content", "hash"),
        )
    return document_id, str(database.DATABASE_PATH)


def raw_question(index: int = 1, **overrides):
    question = {
        "question": f"What is concept {index}?",
        "choices": [f"Choice {index}A", f"Choice {index}B", f"Choice {index}C", f"Choice {index}D"],
        "correct_answer": f"Choice {index}A",
        "explanation": "Because the source states this clearly.",
        "topic": "Cell biology",
        "source_pages": [1, 2, 99, "3"],
    }
    question.update(overrides)
    return question


def generated_questions(count: int = 5):
    payload = {"questions": [raw_question(index) for index in range(count)]}
    return parse_generated_quiz_questions(json.dumps(payload), requested_count=count, source_document_id="source-document", allowed_source_pages={1, 2})


def make_quiz(quiz_db, questions=None):
    return create_quiz_and_attempt(
        title="Cell biology practice", source_document_id="source-document", source_filename="study.pdf",
        difficulty="Mixed", requested_question_count=5, questions=questions or generated_questions(),
        timer_duration_seconds=None,
    )


def test_question_validation_normalizes_pages_and_discards_duplicates():
    payload = {"questions": [raw_question(1), raw_question(1), *(raw_question(i, source_pages=[4, 0, True]) for i in range(2, 6))]}
    questions = parse_generated_quiz_questions(json.dumps(payload), requested_count=5, allowed_source_pages={1, 2})
    assert len(questions) == 5
    assert questions[0]["correct_option_id"] == "A"
    assert questions[0]["source_pages"] == [1, 2]
    assert questions[1]["source_pages"] == []
    assert questions[0]["topic"] == "Cell biology"


def test_gemini_quiz_generation_uses_the_shared_injected_service_and_schema():
    payload = json.dumps({"questions": [raw_question(i) for i in range(1, 6)]})
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": payload})()

    class Client:
        models = Models()

        def close(self):
            pass

    service = GeminiService(api_key="quiz-test-key", client_factory=lambda **kwargs: Client())
    result = service.generate_quiz_questions(
        "--- Page 1 --- A source passage", count=5, difficulty="Mixed",
        source_document_id="source-document", allowed_source_pages={1},
    )
    assert len(result) == 5
    assert result[0]["source_pages"] == [1]
    assert calls[0]["config"].response_mime_type == "application/json"
    assert calls[0]["config"].response_json_schema["required"] == ["questions"]


@pytest.mark.parametrize("broken", [
    raw_question(question=""),
    raw_question(choices=["same", "same", "third", "fourth"]),
    raw_question(choices=["one", "two", "three"]),
    raw_question(correct_answer="missing choice"),
    raw_question(explanation=""),
])
def test_parser_drops_invalid_generated_questions_and_requires_five(broken):
    payload = {"questions": [broken, *(raw_question(i) for i in range(2, 6))]}
    with pytest.raises(QuizValidationError):
        parse_generated_quiz_questions(json.dumps(payload), requested_count=10)


def test_parser_handles_fenced_json_and_bad_json():
    payload = json.dumps({"questions": [raw_question(i) for i in range(1, 6)]})
    assert len(parse_generated_quiz_questions(f"```json\n{payload}\n```", requested_count=5)) == 5
    with pytest.raises(QuizValidationError, match="unreadable"):
        parse_generated_quiz_questions("not json", requested_count=5)


@pytest.mark.parametrize(("selected", "expected"), [
    ({"q": "A"}, (1, 0, 0, 100.0)),
    ({"q": "B"}, (0, 1, 0, 0.0)),
    ({"q": None}, (0, 0, 1, 0.0)),
])
def test_deterministic_single_question_scoring(selected, expected):
    result = score_quiz_answers([{"question_id": "q", "options": [{"id": x} for x in "ABCD"], "correct_option_id": "A"}], selected)
    assert (result["correct_answers"], result["incorrect_answers"], result["unanswered_questions"], result["percentage"]) == expected


def test_scoring_mixed_answers_and_empty_or_invalid_answers():
    questions = [{"question_id": str(i), "options": [{"id": x} for x in "ABCD"], "correct_option_id": "A"} for i in range(4)]
    result = score_quiz_answers(questions, {"0": "A", "1": "C", "2": None})
    assert (result["correct_answers"], result["incorrect_answers"], result["unanswered_questions"], result["percentage"]) == (1, 1, 2, 25.0)
    with pytest.raises(ValueError):
        score_quiz_answers([], {})
    with pytest.raises(ValueError):
        score_quiz_answers(questions, {"0": "Z"})


def test_attempt_answers_persist_submission_scores_once_and_reviews_keys_only_after_submit(quiz_db):
    quiz_id, attempt_id = make_quiz(quiz_db)
    active_questions = get_attempt_questions(attempt_id)
    assert all("correct_option_id" not in question and "explanation" not in question for question in active_questions)
    first = active_questions[0]
    save_attempt_answer(attempt_id, first["question_id"], "A")
    result = submit_attempt(attempt_id)
    assert result["correct_answers"] == 1
    assert result["unanswered_questions"] == 4
    assert submit_attempt(attempt_id) is None
    review = get_attempt_review(attempt_id)
    assert review["quiz_id"] == quiz_id
    assert review["questions"][0]["explanation"].startswith("Because")
    with pytest.raises(QuizRepositoryError):
        save_attempt_answer(attempt_id, first["question_id"], "B")
    attempts, answers = get_progress_records()
    assert len(attempts) == 1 and len(answers) == 5


def test_retry_includes_missed_questions_and_does_not_change_original(quiz_db):
    quiz_id, original_id = make_quiz(quiz_db)
    questions = get_attempt_questions(original_id)
    for question in questions:
        save_attempt_answer(original_id, question["question_id"], "A" if question["position"] == 1 else "B")
    submit_attempt(original_id)
    retry_id = create_new_attempt(quiz_id, original_attempt_id=original_id, question_ids=[q["question_id"] for q in questions[1:]], use_quiz_timer=False)
    assert get_attempt(retry_id)["total_questions"] == 4
    assert get_attempt(original_id)["correct_answers"] == 1
    assert get_attempt(original_id)["status"] == "completed"


def test_timeout_history_and_activity_are_local_and_sorted(quiz_db):
    quiz_id, attempt_id = make_quiz(quiz_db)
    questions = get_attempt_questions(attempt_id)
    save_attempt_answer(attempt_id, questions[0]["question_id"], "A")
    # Explicit timeout is deterministic for tests and uses the same local scorer.
    result = submit_attempt(attempt_id, timed_out=True)
    assert result["status"] == "timed_out"
    assert get_attempt_review(attempt_id)["status"] == "timed_out"
    history = list_attempt_history()
    assert history[0]["quiz_id"] == quiz_id
    with database.get_connection() as connection:
        stats = database.get_dashboard_stats()
        assert stats["quizzes"] == 1
        assert stats["questions"] == 1


def test_failed_create_rolls_back_and_deleted_source_retains_snapshot(quiz_db):
    doc_id, db_path = quiz_db
    questions = generated_questions()
    bad = [*questions[:-1], {**questions[-1], "question_id": questions[0]["question_id"]}]
    with pytest.raises(sqlite3.IntegrityError):
        create_quiz_and_attempt(
            title="Rollback test", source_document_id=doc_id, source_filename="study.pdf", difficulty="Easy",
            requested_question_count=5, questions=bad, timer_duration_seconds=None,
        )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0] == 0
    quiz_id, attempt_id = make_quiz(quiz_db)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    attempt = get_attempt(attempt_id)
    assert attempt["source_document_id"] is None
    assert attempt["source_filename"] == "study.pdf"
    assert attempt["quiz_id"] == quiz_id


def test_local_analytics_distinguish_attempt_scores_from_answer_accuracy_and_minimum_topic_sample():
    attempts = [
        {"status": "completed", "percentage": 50, "submitted_at": "2026-01-01"},
        {"status": "timed_out", "percentage": 100, "submitted_at": "2026-01-02"},
        {"status": "in_progress", "percentage": None, "submitted_at": None},
    ]
    answers = [
        {"selected_option_id": "A", "is_correct": 1, "topic": "Cells"},
        {"selected_option_id": "B", "is_correct": 0, "topic": "Cells"},
        {"selected_option_id": "C", "is_correct": 0, "topic": "Cells"},
        {"selected_option_id": None, "is_correct": None, "topic": "Cells"},
        {"selected_option_id": "A", "is_correct": 1, "topic": "Energy"},
    ]
    result = calculate_progress_analytics(attempts, answers)
    assert result["average_score"] == 75.0
    assert result["best_score"] == 100.0
    assert result["most_recent_score"] == 100.0
    assert result["overall_accuracy"] == 50.0  # 2/4 answered; blank is excluded
    assert result["weak_topics"][0]["topic"] == "Cells"
    assert next(item for item in result["topics"] if item["topic"] == "Energy")["insight"].startswith("Provisional")
    assert calculate_progress_analytics([], [])["average_score"] is None


@pytest.mark.parametrize("page", ["Practice Quiz", "Study Progress"])
def test_phase6_pages_render_without_gemini_credentials_or_saved_pdfs(quiz_db, monkeypatch, page):
    monkeypatch.setenv("STUDY_ASSISTANT_EMBEDDING_MODEL", "tests/no-download")
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=10)
    app.session_state["active_page"] = page
    app.run()
    assert not app.exception
