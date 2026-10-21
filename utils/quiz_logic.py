"""Validation, deterministic scoring, and transparent quiz analytics."""

import json
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence


MIN_QUIZ_QUESTIONS = 5
MAX_QUIZ_QUESTIONS = 30
DIFFICULTIES = ("Easy", "Medium", "Hard", "Mixed")
MIN_TOPIC_SAMPLE = 3
WEAK_TOPIC_THRESHOLD = 60.0


class QuizValidationError(ValueError):
    """Raised when generated questions cannot safely form a quiz."""


def validate_quiz_question(
    question: Any,
    *,
    source_document_id: str | None = None,
    allowed_source_pages: set[int] | None = None,
) -> dict[str, Any]:
    """Convert one untrusted generated MCQ into the app's stable representation."""
    if not isinstance(question, Mapping):
        raise QuizValidationError("A generated question was not an object.")
    text = question.get("question")
    choices = question.get("choices")
    correct_answer = question.get("correct_answer")
    explanation = question.get("explanation")
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2_000:
        raise QuizValidationError("A generated question had missing or invalid question text.")
    if (
        not isinstance(choices, list)
        or len(choices) != 4
        or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
    ):
        raise QuizValidationError("Each generated question must have exactly four non-empty options.")
    normalized_choices = [choice.strip() for choice in choices]
    if len({choice.casefold() for choice in normalized_choices}) != 4:
        raise QuizValidationError("Generated option identifiers must be unique; duplicate options were found.")
    if not isinstance(correct_answer, str) or not correct_answer.strip():
        raise QuizValidationError("A generated question did not specify exactly one correct answer.")
    matching_options = [
        index for index, choice in enumerate(normalized_choices)
        if choice.casefold() == correct_answer.strip().casefold()
    ]
    if len(matching_options) != 1:
        raise QuizValidationError("The generated correct answer did not match one of the four options.")
    if not isinstance(explanation, str) or not explanation.strip() or len(explanation.strip()) > 2_000:
        raise QuizValidationError("A generated question had missing or invalid answer explanation.")

    topic_value = question.get("topic")
    topic = topic_value.strip() if isinstance(topic_value, str) else ""
    if not topic or topic.casefold() in {"unknown", "n/a", "not available"} or len(topic) > 120:
        topic = None

    allowed_pages = allowed_source_pages or set()
    raw_pages = question.get("source_pages", [])
    source_pages = sorted(
        {
            page for page in raw_pages
            if isinstance(page, int) and not isinstance(page, bool) and page > 0 and page in allowed_pages
        }
        if isinstance(raw_pages, list)
        else set()
    )
    option_ids = ("A", "B", "C", "D")
    return {
        "question_id": uuid.uuid4().hex,
        "question": text.strip(),
        "options": [
            {"id": option_id, "text": choice}
            for option_id, choice in zip(option_ids, normalized_choices)
        ],
        "correct_option_id": option_ids[matching_options[0]],
        "explanation": explanation.strip(),
        "topic": topic,
        "source_document_id": source_document_id,
        "source_pages": source_pages,
    }


def parse_generated_quiz_questions(
    raw_response: str,
    *,
    requested_count: int,
    source_document_id: str | None = None,
    allowed_source_pages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Parse structured model output, reject invalid entries, and remove duplicates."""
    if not MIN_QUIZ_QUESTIONS <= requested_count <= MAX_QUIZ_QUESTIONS:
        raise QuizValidationError(f"Choose between {MIN_QUIZ_QUESTIONS} and {MAX_QUIZ_QUESTIONS} questions.")
    candidate = raw_response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as error:
        raise QuizValidationError("Gemini returned an unreadable quiz. Try generating it again.") from error
    raw_questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(raw_questions, list):
        raise QuizValidationError("Gemini did not return a valid question set. Try generating it again.")

    normalized: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for raw_question in raw_questions:
        try:
            question = validate_quiz_question(
                raw_question,
                source_document_id=source_document_id,
                allowed_source_pages=allowed_source_pages,
            )
        except QuizValidationError:
            continue
        normalized_text = " ".join(question["question"].casefold().split())
        if normalized_text in seen_questions:
            continue
        seen_questions.add(normalized_text)
        normalized.append(question)
        if len(normalized) == requested_count:
            break

    if len(normalized) < MIN_QUIZ_QUESTIONS:
        raise QuizValidationError(
            f"Only {len(normalized)} valid, distinct questions were generated. At least {MIN_QUIZ_QUESTIONS} are needed; try again with a smaller request or another document."
        )
    return normalized


def score_quiz_answers(
    questions: Sequence[Mapping[str, Any]],
    selected_answers: Mapping[str, str | None],
) -> dict[str, Any]:
    """Score one mark per correct answer, no negative marks, blanks score zero."""
    if not questions:
        raise ValueError("A quiz attempt must contain at least one validated question.")
    correct = incorrect = unanswered = 0
    evaluated: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question["question_id"])
        options = question["options"]
        option_ids = {option["id"] for option in options}
        selected = selected_answers.get(question_id)
        if selected is not None and selected not in option_ids:
            raise ValueError("A submitted answer is not one of this question's options.")
        is_correct = selected is not None and selected == question["correct_option_id"]
        if selected is None:
            unanswered += 1
        elif is_correct:
            correct += 1
        else:
            incorrect += 1
        evaluated.append({
            "question_id": question_id,
            "selected_option_id": selected,
            "is_correct": bool(is_correct) if selected is not None else None,
            "awarded_marks": 1 if is_correct else 0,
        })
    total = len(questions)
    percentage = float((Decimal(correct * 100) / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {
        "correct_answers": correct,
        "incorrect_answers": incorrect,
        "unanswered_questions": unanswered,
        "total_questions": total,
        "total_possible_marks": total,
        "percentage": percentage,
        "evaluated_answers": evaluated,
    }


def calculate_progress_analytics(
    attempts: Sequence[Mapping[str, Any]],
    answer_rows: Sequence[Mapping[str, Any]],
    *,
    minimum_topic_sample: int = MIN_TOPIC_SAMPLE,
) -> dict[str, Any]:
    """Calculate attempt-weighted scores, response accuracy, and provisional topics."""
    submitted = [
        item for item in attempts
        if item.get("status") in {"completed", "timed_out"} and item.get("percentage") is not None
    ]
    scores = [Decimal(str(item["percentage"])) for item in submitted]
    average_score = float((sum(scores) / len(scores)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if scores else None
    best_score = float(max(scores)) if scores else None
    sorted_attempts = sorted(submitted, key=lambda item: str(item.get("submitted_at") or ""))
    most_recent_score = float(sorted_attempts[-1]["percentage"]) if sorted_attempts else None

    answered = [item for item in answer_rows if item.get("selected_option_id") is not None]
    correct_count = sum(item.get("is_correct") == 1 or item.get("is_correct") is True for item in answered)
    overall_accuracy = float((Decimal(correct_count * 100) / Decimal(len(answered))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if answered else None
    topics: dict[str, dict[str, Any]] = {}
    for item in answered:
        topic = item.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            continue
        normalized = topic.strip()
        record = topics.setdefault(normalized, {"topic": normalized, "answered": 0, "correct": 0, "incorrect": 0})
        record["answered"] += 1
        if item.get("is_correct") == 1 or item.get("is_correct") is True:
            record["correct"] += 1
        else:
            record["incorrect"] += 1

    topic_results = []
    for record in topics.values():
        record["accuracy_percentage"] = round(record["correct"] * 100 / record["answered"], 2)
        if record["answered"] < minimum_topic_sample:
            record["insight"] = f"Provisional · {record['answered']} of {minimum_topic_sample} responses"
        elif record["accuracy_percentage"] < WEAK_TOPIC_THRESHOLD:
            record["insight"] = "Needs review"
        else:
            record["insight"] = "On track"
        topic_results.append(record)
    topic_results.sort(key=lambda item: (item["accuracy_percentage"], item["topic"].casefold()))

    return {
        "completed_attempts": len(submitted),
        "average_score": average_score,
        "best_score": best_score,
        "most_recent_score": most_recent_score,
        "total_questions_answered": len(answered),
        "correct_answers": correct_count,
        "overall_accuracy": overall_accuracy,
        "trend": sorted_attempts,
        "topics": topic_results,
        "weak_topics": [item for item in topic_results if item["insight"] == "Needs review"],
    }
