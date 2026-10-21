"""Study progress derived from submitted quiz attempts and answers."""

import streamlit as st

from utils.quiz_logic import MIN_TOPIC_SAMPLE, calculate_progress_analytics
from utils.quiz_repository import get_progress_records
from utils.flashcard_repository import get_flashcard_stats


def _render_flashcard_progress() -> None:
    st.divider()
    st.subheader("Flashcard practice · self-rated recall")
    try:
        flashcards = get_flashcard_stats()
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Active flashcards", flashcards["total_cards"])
        f2.metric("Due today", flashcards["due_today"])
        f3.metric("Reviews recorded", flashcards["total_reviews"])
        rate = flashcards["recall_success_rate"]
        f4.metric("Self-rated successful recalls", f"{rate:.1f}%" if rate is not None else "—")
        st.caption("A recall success means the student rated a card Hard, Good, or Easy. This is separate from quiz scores and is not an objective measure of mastery.")
    except Exception as error:
        st.warning(f"Flashcard statistics could not be loaded: {error}")


def render_progress() -> None:
    st.markdown('<div class="eyebrow">YOUR LEARNING JOURNEY</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Study Progress</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Scores and answer accuracy from quiz attempts saved on this device.</p>', unsafe_allow_html=True)
    try:
        attempts, answers = get_progress_records()
    except Exception as error:
        st.error(f"Study progress could not be loaded from the local database: {error}")
        return
    analytics = calculate_progress_analytics(attempts, answers)
    if not analytics["completed_attempts"]:
        st.markdown('<div class="empty-state"><div class="empty-icon">↗</div><strong>Your progress starts here</strong><p>Submit a quiz to see your scores, answer accuracy, and topic insights. In-progress and cancelled attempts are not included.</p></div>', unsafe_allow_html=True)
        _render_flashcard_progress()
        return

    latest = analytics["trend"][-1]
    left, mid1, mid2, right = st.columns(4)
    left.metric("Quizzes submitted", analytics["completed_attempts"])
    mid1.metric("Average score", f"{analytics['average_score']:.1f}%")
    mid2.metric("Best score", f"{analytics['best_score']:.1f}%")
    right.metric("Most recent", f"{analytics['most_recent_score']:.1f}%", help=latest["title"])

    answered_accuracy = analytics["overall_accuracy"]
    col_accuracy, col_answered = st.columns(2)
    col_accuracy.metric(
        "Accuracy on answered questions",
        f"{answered_accuracy:.1f}%" if answered_accuracy is not None else "—",
        help="Correct answers divided by attempted questions. Unanswered questions are excluded. This differs from quiz score, which includes all questions.",
    )
    col_answered.metric("Questions answered", analytics["total_questions_answered"])

    if len(analytics["trend"]) >= 2:
        st.subheader("Score trend")
        st.line_chart({"Score (%)": [item["percentage"] for item in analytics["trend"]]})
    st.subheader("Topic insights")
    if not analytics["topics"]:
        st.caption("Topic insights appear when generated quiz questions include a topic label.")
    else:
        for item in analytics["topics"]:
            detail = f"{item['correct']}/{item['answered']} correct · {item['accuracy_percentage']:.1f}%"
            if item["answered"] < MIN_TOPIC_SAMPLE:
                st.info(f"{item['topic']}: {detail}. Provisional; insights need at least {MIN_TOPIC_SAMPLE} answered responses.")
            elif item["insight"] == "Needs review":
                st.warning(f"{item['topic']}: {detail}. Consider reviewing this topic.")
            else:
                st.success(f"{item['topic']}: {detail}. On track.")

    st.subheader("Submitted attempt history")
    for item in reversed(analytics["trend"]):
        result_state = "Timed out" if item["status"] == "timed_out" else "Submitted"
        st.write(
            f"**{item['title']}** · {result_state} · {item['correct_answers']}/{item['total_questions']} correct · "
            f"{item['percentage']:.1f}% · {item['submitted_at']}"
        )

    _render_flashcard_progress()
