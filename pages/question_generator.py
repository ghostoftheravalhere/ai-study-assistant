"""Generate and validate study questions from one selected PDF."""

import sqlite3

import streamlit as st

from components.document_picker import render_document_picker
from utils.config import MAX_DOCUMENT_CONTEXT_CHARS
from utils.database import record_ai_activity
from utils.gemini_service import GeminiService, GeminiServiceError, is_gemini_configured, load_gemini_model


def render_question_generator() -> None:
    st.markdown('<div class="eyebrow">PRACTICE WHAT YOU LEARN</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Question Generator</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Generate source-grounded questions from one PDF. Interactive quizzes come in a later phase.</p>',
        unsafe_allow_html=True,
    )
    document = render_document_picker("Question source document", key="questions")
    if document is None:
        return
    if document["extraction_status"] != "ready" or not document["extracted_text"].strip():
        st.warning("This document has no selectable text for question generation. OCR is not supported yet.")
        return

    st.write(f"**Selected document:** {document['original_filename']}")
    count_col, difficulty_col, type_col = st.columns(3)
    with count_col:
        question_count = st.number_input("Number of questions", min_value=1, max_value=15, value=5, step=1)
    with difficulty_col:
        difficulty = st.selectbox("Difficulty", ("Easy", "Medium", "Hard"))
    with type_col:
        question_type = st.selectbox("Question type", ("Multiple choice", "Short answer", "Descriptive"))
    answer_col, explanation_col = st.columns(2)
    with answer_col:
        include_answers = st.checkbox("Include expected answers", value=True)
    with explanation_col:
        include_explanations = st.checkbox("Include explanations", value=True)

    st.caption(
        f"Model: {load_gemini_model()} · Only this PDF's extracted text is sent to Google Gemini."
    )
    if not is_gemini_configured():
        st.info("Add GEMINI_API_KEY to your environment or Streamlit secrets to generate questions.")

    configuration = {
        "document_id": document["id"],
        "count": int(question_count),
        "difficulty": difficulty,
        "question_type": question_type,
        "include_answers": include_answers,
        "include_explanations": include_explanations,
    }
    button_label = "Regenerate questions" if st.session_state.get("question_result", {}).get("configuration") == configuration else "Generate questions"
    if st.button(
        button_label,
        type="primary",
        disabled=not is_gemini_configured(),
        key="generate_questions",
    ):
        try:
            with st.spinner("Building questions from the selected study material…"):
                result = GeminiService().generate_questions(
                    document["extracted_text"],
                    question_type=question_type,
                    difficulty=difficulty,
                    count=int(question_count),
                    include_answers=include_answers,
                    include_explanations=include_explanations,
                )
            st.session_state["question_result"] = {
                "configuration": configuration,
                "filename": document["original_filename"],
                "questions": result.questions,
                "context_truncated": result.context_truncated,
                "original_context_length": result.original_context_length,
            }
            try:
                record_ai_activity("questions_generated", "Generated study questions")
            except (OSError, sqlite3.Error):
                st.warning("The questions were generated, but this activity could not be recorded locally.")
        except GeminiServiceError as error:
            st.error(str(error))

    current = st.session_state.get("question_result")
    if current and current["configuration"] == configuration:
        st.markdown("### Generated questions")
        st.caption(f"{current['filename']} · {difficulty} · {question_type}")
        if current["context_truncated"]:
            supplied_chars = min(current["original_context_length"], MAX_DOCUMENT_CONTEXT_CHARS)
            st.warning(
                f"These questions use the first {supplied_chars:,} of {current['original_context_length']:,} "
                "extracted characters. They do not cover the entire document."
            )
        for index, question in enumerate(current["questions"], start=1):
            st.markdown(f"**{index}. {question['question']}**")
            for choice_index, choice in enumerate(question.get("choices", [])):
                st.markdown(f"- {chr(65 + choice_index)}. {choice}")
            if "expected_answer" in question:
                st.markdown(f"**Expected answer:** {question['expected_answer']}")
            if "explanation" in question:
                st.markdown(f"*Explanation:* {question['explanation']}")
            if index < len(current["questions"]):
                st.divider()
