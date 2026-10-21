"""Local application settings page."""

import streamlit as st

from utils.config import EMBEDDING_MODEL_NAME, INDEX_DIR
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
    st.write(f"**Local embedding model:** `{EMBEDDING_MODEL_NAME}`")
    st.write(f"**FAISS cache:** `{INDEX_DIR}` (rebuildable; vectors are stored in SQLite)")
    st.caption("The dashboard, local PDF library, and semantic search work without an API key. Document chat sends only retrieved excerpts to Google Gemini; general chat sends no document text.")
    st.caption("Set GEMINI_API_KEY_1 through GEMINI_API_KEY_4 and optionally GEMINI_MODEL in the environment or Streamlit secrets. The legacy GEMINI_API_KEY remains supported. Keys are never shown on this page.")
