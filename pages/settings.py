"""Local application settings page."""

import streamlit as st

from utils.gemini_service import is_gemini_configured, load_gemini_model


def render_settings() -> None:
    st.markdown('<div class="eyebrow">PREFERENCES</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Settings</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">A few details about this StudyMate workspace.</p>', unsafe_allow_html=True)
    st.markdown("### Application")
    st.write("**Name:** AI Study Assistant")
    st.write("**Storage:** Local SQLite database (`study_assistant.db`)")
    st.write(f"**Gemini model:** `{load_gemini_model()}`")
    st.write(f"**Gemini API key:** {'Configured' if is_gemini_configured() else 'Not configured'}")
    st.caption("The dashboard and local PDF library work without an API key. AI requests send your prompt and selected document text to Google Gemini.")
    st.caption("Set GEMINI_API_KEY and optionally GEMINI_MODEL in the environment or Streamlit secrets. The key is never shown in this page.")
