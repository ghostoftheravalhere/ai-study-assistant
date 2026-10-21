"""Local application settings page."""

import streamlit as st


def render_settings() -> None:
    st.markdown('<div class="eyebrow">PREFERENCES</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Settings</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">A few details about this Phase 1 foundation.</p>', unsafe_allow_html=True)
    st.markdown("### Application")
    st.write("**Name:** AI Study Assistant")
    st.write("**Storage:** Local SQLite database (`study_assistant.db`)")
    st.write("**AI connection:** Not configured in Phase 1")
    st.caption("API credentials are not needed to run the dashboard. Gemini integration is planned for a later phase.")
