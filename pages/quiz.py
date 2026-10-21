"""Interactive PDF quizzes with local, reproducible scoring and review."""

from datetime import datetime, timezone

import streamlit as st

from components.document_picker import render_document_picker
from utils.gemini_service import (
    GeminiServiceError,
    get_gemini_service_for_session,
    is_gemini_available_for_session,
    load_gemini_model,
)
from utils.quiz_logic import DIFFICULTIES, MAX_QUIZ_QUESTIONS, MIN_QUIZ_QUESTIONS, QuizValidationError
from utils.quiz_repository import (
    QuizRepositoryError,
    cancel_attempt,
    create_new_attempt,
    create_quiz_and_attempt,
    get_attempt,
    get_attempt_answers,
    get_attempt_questions,
    get_attempt_review,
    list_attempt_history,
    list_in_progress_attempts,
    save_attempt_answer,
    submit_attempt,
)
from utils.quiz_service import generate_document_quiz


def _activate(attempt_id: str) -> None:
    st.session_state["quiz_active_attempt_id"] = attempt_id
    st.session_state.pop("quiz_review_attempt_id", None)
    st.rerun()


def _render_timer(attempt: dict) -> None:
    duration = attempt.get("timer_duration_seconds")
    if not duration:
        return
    try:
        started = datetime.fromisoformat(attempt["started_at"]).astimezone(timezone.utc)
    except (ValueError, TypeError):
        st.caption("Timed quiz")
        return
    deadline = started.timestamp() + duration

    def render_countdown() -> None:
        remaining = max(0, int(deadline - datetime.now(timezone.utc).timestamp()))
        st.metric("Time remaining", f"{remaining // 60:02d}:{remaining % 60:02d}")
        if remaining == 0:
            submit_attempt(attempt["id"], timed_out=True)
            st.session_state.pop("quiz_active_attempt_id", None)
            st.session_state["quiz_review_attempt_id"] = attempt["id"]
            st.rerun()

    fragment = getattr(st, "fragment", None)
    if fragment:
        fragment(run_every="1s")(render_countdown)()
    else:
        render_countdown()


def _render_setup() -> None:
    st.markdown('<div class="eyebrow">PRACTICE WHAT YOU LEARNED</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Practice Quiz</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Build a multiple-choice quiz from a text-ready PDF, then review your answers and progress.</p>', unsafe_allow_html=True)
    document = render_document_picker("Quiz source PDF", key="quiz")
    if document is None:
        _render_history()
        return
    if document["extraction_status"] != "ready" or not document["extracted_text"].strip():
        st.warning("This PDF has no selectable text for quiz generation. OCR is not supported.")
        _render_history()
        return

    title_default = document["original_filename"].rsplit(".", 1)[0][:110]
    with st.form("quiz_setup"):
        title = st.text_input("Quiz title (optional)", value="", placeholder=f"{title_default} practice")
        count = st.slider("Number of questions", MIN_QUIZ_QUESTIONS, MAX_QUIZ_QUESTIONS, 10)
        difficulty = st.selectbox("Difficulty", DIFFICULTIES)
        timed = st.toggle("Set a time limit", value=False)
        minutes = st.select_slider("Time limit (minutes)", options=list(range(5, 121, 5)), value=20, disabled=not timed)
        start = st.form_submit_button("Generate quiz", type="primary", disabled=not is_gemini_available_for_session())
    st.caption(f"Model: {load_gemini_model()} · The selected PDF text or local retrieved passages are sent to Google Gemini.")
    if not is_gemini_available_for_session():
        st.info("Activate a personal Gemini key or configure an application key in Settings to generate quizzes.")
    if start:
        try:
            with st.spinner("Creating questions from your study material…"):
                generated = generate_document_quiz(
                    document, count=count, difficulty=difficulty,
                    gemini_service=get_gemini_service_for_session(),
                )
                quiz_id, attempt_id = create_quiz_and_attempt(
                    title=title.strip() or f"{title_default} practice", source_document_id=document["id"],
                    source_filename=document["original_filename"], difficulty=difficulty,
                    requested_question_count=count, questions=generated.questions,
                    timer_duration_seconds=minutes * 60 if timed else None,
                )
            if len(generated.questions) < count:
                st.session_state["quiz_generation_notice"] = (
                    f"Gemini produced {len(generated.questions)} valid questions out of {count} requested; "
                    "this attempt contains only the questions that passed validation."
                )
            st.session_state["quiz_active_attempt_id"] = attempt_id
            st.rerun()
        except (GeminiServiceError, QuizValidationError, QuizRepositoryError, ValueError) as error:
            st.error(str(error))
    _render_history()


def _render_active(attempt_id: str) -> None:
    attempt = get_attempt(attempt_id)
    if not attempt or attempt["status"] != "in_progress":
        st.session_state.pop("quiz_active_attempt_id", None)
        st.warning("This quiz attempt is no longer active. Open it from quiz history to review your result.")
        return
    timer_duration = attempt.get("timer_duration_seconds")
    if timer_duration:
        started = datetime.fromisoformat(attempt["started_at"]).astimezone(timezone.utc)
        if datetime.now(timezone.utc).timestamp() >= started.timestamp() + timer_duration:
            submit_attempt(attempt_id, timed_out=True)
            st.session_state.pop("quiz_active_attempt_id", None)
            st.session_state["quiz_review_attempt_id"] = attempt_id
            st.rerun()
    questions = get_attempt_questions(attempt_id)
    if not questions:
        st.error("This quiz attempt has no questions. It cannot be continued.")
        return
    answers = get_attempt_answers(attempt_id)
    st.markdown('<div class="eyebrow">QUIZ IN PROGRESS</div>', unsafe_allow_html=True)
    st.title(attempt["title"])
    st.caption(f"{attempt['source_filename']} · {attempt['difficulty']} · Answers are saved on this device as you go.")
    notice = st.session_state.pop("quiz_generation_notice", None)
    if notice:
        st.warning(notice)
    _render_timer(attempt)
    position_key = f"quiz_position_{attempt_id}"
    pos = max(0, min(int(st.session_state.get(position_key, 0)), len(questions) - 1))
    st.progress((pos + 1) / len(questions), text=f"Question {pos + 1} of {len(questions)}")
    question = questions[pos]
    question_id = question["question_id"]
    st.subheader(question["question_text"])
    widget_key = f"quiz_answer_{attempt_id}_{question_id}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = answers.get(question_id)
    option_by_id = {option["id"]: option["text"] for option in question["options"]}
    selected = st.radio(
        "Choose one answer", options=list(option_by_id),
        format_func=lambda option_id: f"{option_id}. {option_by_id[option_id]}",
        key=widget_key, index=None, label_visibility="collapsed",
    )
    try:
        save_attempt_answer(attempt_id, question_id, selected)
    except QuizRepositoryError as error:
        st.error(str(error))
        return
    saved = get_attempt_answers(attempt_id)
    st.caption(f"{sum(value is not None for value in saved.values())} of {len(questions)} answered")
    left, right, _ = st.columns([1, 1, 4])
    if left.button("← Previous", disabled=pos == 0, key=f"quiz_prev_{attempt_id}"):
        st.session_state[position_key] = pos - 1
        st.rerun()
    if right.button("Next →", disabled=pos >= len(questions) - 1, key=f"quiz_next_{attempt_id}"):
        st.session_state[position_key] = pos + 1
        st.rerun()
    with st.expander("Question navigator"):
        cols = st.columns(min(6, len(questions)))
        for index, item in enumerate(questions):
            with cols[index % len(cols)]:
                label = f"{index + 1}{' ✓' if saved.get(item['question_id']) else ''}"
                if st.button(label, key=f"quiz_jump_{attempt_id}_{index}"):
                    st.session_state[position_key] = index
                    st.rerun()
    st.divider()
    if st.button("Submit quiz", type="primary", key=f"quiz_submit_open_{attempt_id}"):
        st.session_state[f"quiz_confirm_submit_{attempt_id}"] = True
    if st.session_state.get(f"quiz_confirm_submit_{attempt_id}"):
        st.warning("Submit now? You can’t change answers after submission.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm submission", type="primary", key=f"quiz_submit_yes_{attempt_id}"):
            result = submit_attempt(attempt_id)
            st.session_state.pop("quiz_active_attempt_id", None)
            st.session_state.pop(f"quiz_confirm_submit_{attempt_id}", None)
            st.session_state["quiz_review_attempt_id"] = attempt_id
            if result is None:
                st.info("This attempt was already submitted.")
            st.rerun()
        if cancel_col.button("Keep working", key=f"quiz_submit_no_{attempt_id}"):
            st.session_state.pop(f"quiz_confirm_submit_{attempt_id}", None)
            st.rerun()
    if st.button("Cancel this attempt", key=f"quiz_cancel_open_{attempt_id}"):
        st.session_state[f"quiz_confirm_cancel_{attempt_id}"] = True
    if st.session_state.get(f"quiz_confirm_cancel_{attempt_id}"):
        st.warning("Cancel this attempt? It will be retained as cancelled and excluded from scores.")
        yes, no = st.columns(2)
        if yes.button("Confirm cancellation", key=f"quiz_cancel_yes_{attempt_id}"):
            cancel_attempt(attempt_id)
            st.session_state.pop("quiz_active_attempt_id", None)
            st.session_state.pop(f"quiz_confirm_cancel_{attempt_id}", None)
            st.rerun()
        if no.button("Continue quiz", key=f"quiz_cancel_no_{attempt_id}"):
            st.session_state.pop(f"quiz_confirm_cancel_{attempt_id}", None)
            st.rerun()


def _render_review(attempt_id: str) -> None:
    review = get_attempt_review(attempt_id)
    if not review:
        st.warning("This result is no longer available.")
        return
    st.markdown('<div class="eyebrow">RESULT AND ANSWER REVIEW</div>', unsafe_allow_html=True)
    st.title(review["title"])
    status = "Timed out" if review["status"] == "timed_out" else "Submitted"
    st.caption(f"{status} · {review['source_filename']} · {review['difficulty']}")
    a, b, c, d, e = st.columns(5)
    a.metric("Score", f"{review['correct_answers']}/{review['total_questions']}")
    b.metric("Result", f"{review['percentage']:.1f}%")
    c.metric("Incorrect", review["incorrect_answers"])
    d.metric("Unanswered", review["unanswered_questions"])
    duration = review.get("duration_seconds")
    e.metric("Time taken", f"{duration // 60}:{duration % 60:02d}" if duration is not None else "—")
    st.progress(review["percentage"] / 100)
    incorrect_ids = []
    for index, question in enumerate(review["questions"], start=1):
        is_correct = question["is_correct"] == 1 or question["is_correct"] is True
        if not is_correct:
            incorrect_ids.append(question["question_id"])
        selected_id = question["selected_option_id"]
        correct_id = question["correct_option_id"]
        selected_text = next((item["text"] for item in question["options"] if item["id"] == selected_id), None)
        correct_text = next((item["text"] for item in question["options"] if item["id"] == correct_id), "Unavailable")
        st.write(f"**Your answer:** {selected_id + '. ' + selected_text if selected_text else 'No answer'}")
        st.write(f"**Correct answer:** {correct_id}. {correct_text}")
        with st.expander(f"{index}. {question['question_text']} · {'Correct' if is_correct else ('Unanswered' if selected_id is None else 'Incorrect')}", expanded=False):
            for option in question["options"]:
                marker = "✓ Correct answer" if option["id"] == correct_id else ""
                if option["id"] == selected_id and option["id"] != correct_id:
                    marker = "Your answer"
                st.write(f"**{option['id']}.** {option['text']} {('· ' + marker) if marker else ''}")
            st.write("**Explanation:** " + question["explanation"])
            st.caption(f"Topic: {question['topic'] or 'Not classified'} · PDF page(s): {', '.join(map(str, question['source_pages'])) or 'Not available'}")
    if review["source_document_id"] is None:
        st.info("The source PDF has been removed from the document library. This saved result remains available.")
    col1, col2 = st.columns(2)
    if col1.button("Retry full quiz", type="primary", key=f"quiz_retry_all_{attempt_id}"):
        try:
            _activate(create_new_attempt(review["quiz_id"], original_attempt_id=attempt_id))
        except QuizRepositoryError as error:
            st.error(str(error))
    if incorrect_ids and col2.button(f"Retry {len(incorrect_ids)} missed questions", key=f"quiz_retry_missed_{attempt_id}"):
        try:
            _activate(create_new_attempt(review["quiz_id"], original_attempt_id=attempt_id, question_ids=incorrect_ids, use_quiz_timer=False))
        except QuizRepositoryError as error:
            st.error(str(error))
    if st.button("Back to quiz setup and history", key=f"quiz_back_{attempt_id}"):
        st.session_state.pop("quiz_review_attempt_id", None)
        st.rerun()


def _render_history() -> None:
    st.divider()
    st.subheader("Quiz history")
    try:
        in_progress = list_in_progress_attempts()
        history = list_attempt_history()
    except Exception as error:
        st.error(f"Quiz history could not be loaded: {error}")
        return
    for item in in_progress:
        with st.container(border=True):
            st.write(f"**Continue:** {item['title']} · {item['source_filename']} · {item['total_questions']} questions")
            if st.button("Resume quiz", key=f"quiz_resume_{item['attempt_id']}"):
                _activate(item["attempt_id"])
    if not history:
        st.caption("Submitted quiz attempts will appear here.")
        return
    query = st.text_input("Filter history by quiz or PDF", key="quiz_history_filter").strip().casefold()
    filtered = [item for item in history if not query or query in item["title"].casefold() or query in item["source_filename"].casefold()]
    for item in filtered:
        status = {"completed": "Submitted", "timed_out": "Timed out", "cancelled": "Cancelled", "in_progress": "In progress"}.get(item["status"], item["status"])
        label = f"{item['title']} · {status} · {item['source_filename']}"
        with st.expander(label):
            if item["percentage"] is not None:
                st.write(f"Score: {item['correct_answers']}/{item['total_questions']} · {item['percentage']:.1f}% · {item['submitted_at']}")
                if item["status"] in {"completed", "timed_out"} and st.button("Review answers", key=f"quiz_review_{item['attempt_id']}"):
                    st.session_state["quiz_review_attempt_id"] = item["attempt_id"]
                    st.session_state.pop("quiz_active_attempt_id", None)
                    st.rerun()
            else:
                st.write(f"{item['total_questions']} questions · Started {item['started_at']}")
                if item["status"] == "in_progress" and st.button("Resume", key=f"quiz_history_resume_{item['attempt_id']}"):
                    _activate(item["attempt_id"])


def render_quiz() -> None:
    review_id = st.session_state.get("quiz_review_attempt_id")
    active_id = st.session_state.get("quiz_active_attempt_id")
    if review_id:
        _render_review(review_id)
    elif active_id:
        _render_active(active_id)
    else:
        _render_setup()
