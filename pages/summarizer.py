"""Gemini-powered summaries grounded in one selected local PDF."""

import sqlite3

import streamlit as st

from components.document_picker import render_document_picker
from utils.ai_prompts import SUMMARY_FORMATS
from utils.config import MAX_DOCUMENT_CONTEXT_CHARS
from utils.database import record_ai_activity
from utils.gemini_service import GeminiService, GeminiServiceError, is_gemini_configured, load_gemini_model


def render_summarizer() -> None:
    st.markdown('<div class="eyebrow">MAKE YOUR NOTES CLEARER</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">AI Summarizer</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Create a study summary from the extracted text of one PDF in your library.</p>',
        unsafe_allow_html=True,
    )
    document = render_document_picker("Document to summarize", key="summary")
    if document is None:
        return
    if document["extraction_status"] != "ready" or not document["extracted_text"].strip():
        st.warning("This document has no selectable text to summarize. OCR is not supported yet.")
        return

    st.write(f"**Selected document:** {document['original_filename']}")
    summary_format = st.radio("Summary format", tuple(SUMMARY_FORMATS), horizontal=True, key="summary_format")
    result_key = "summary_result"
    previous = st.session_state.get(result_key)
    matches_previous = bool(
        previous
        and previous["document_id"] == document["id"]
        and previous["format"] == summary_format
    )
    st.caption(
        f"Model: {load_gemini_model()} · The selected PDF's extracted text is sent to Google Gemini."
    )
    if not is_gemini_configured():
        st.info("Add GEMINI_API_KEY_1 (or the legacy GEMINI_API_KEY) to your environment or Streamlit secrets to generate a summary. No key is needed to browse PDFs.")

    if st.button(
        "Regenerate summary" if matches_previous else "Generate summary",
        type="primary",
        disabled=not is_gemini_configured(),
        key="generate_summary",
    ):
        try:
            with st.spinner("Reading the selected study material…"):
                result = GeminiService().summarize_document(document["extracted_text"], summary_format)
            st.session_state[result_key] = {
                "document_id": document["id"],
                "filename": document["original_filename"],
                "format": summary_format,
                "text": result.text,
                "context_truncated": result.context_truncated,
                "original_context_length": result.original_context_length,
            }
            try:
                record_ai_activity("summary_generated", "Generated a study summary")
            except (OSError, sqlite3.Error):
                st.warning("The summary was generated, but its activity could not be recorded locally.")
        except GeminiServiceError as error:
            st.error(str(error))

    current = st.session_state.get(result_key)
    if current and current["document_id"] == document["id"] and current["format"] == summary_format:
        st.markdown("### Summary")
        st.caption(f"{current['filename']} · {current['format']} summary")
        if current["context_truncated"]:
            supplied_chars = min(current["original_context_length"], MAX_DOCUMENT_CONTEXT_CHARS)
            st.warning(
                f"This summary uses the first {supplied_chars:,} of {current['original_context_length']:,} "
                "extracted characters because of the context limit. It does not cover the entire document."
            )
        st.markdown(current["text"])
        with st.expander("Copy summary"):
            st.code(current["text"], language=None)
