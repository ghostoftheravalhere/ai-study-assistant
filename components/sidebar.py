"""Sidebar navigation shared by all application pages."""

import streamlit as st


NAVIGATION = [
    ("Dashboard", "⌂"),
    ("AI Chat", "✦"),
    ("My Documents", "▤"),
    ("Semantic Search", "⌕"),
    ("AI Summarizer", "≋"),
    ("Question Generator", "?"),
    ("Practice Quiz", "✓"),
    ("Flashcards", "▧"),
    ("Study Planner", "▦"),
    ("Data Management", "⇩"),
    ("System Health", "◉"),
    ("Study Progress", "↗"),
    ("Settings", "⚙"),
]


def render_sidebar() -> str:
    """Render navigation and return the active page name."""
    if "active_page" not in st.session_state:
        st.session_state.active_page = "Dashboard"

    with st.sidebar:
        st.markdown(
            '<div class="brand-lockup"><div class="brand-mark">✦</div>'
            '<div><div class="brand-name">StudyMate</div><div class="brand-caption">AI STUDY ASSISTANT</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sidebar-label">WORKSPACE</div>', unsafe_allow_html=True)
        for label, icon in NAVIGATION:
            if st.button(
                f"{icon}    {label}",
                key=f"nav_{label}",
                use_container_width=True,
                type="primary" if st.session_state.active_page == label else "secondary",
            ):
                st.session_state.active_page = label
        st.markdown(
            '<div class="sidebar-note"><span class="status-dot"></span> Phase 11 · Reliability</div>',
            unsafe_allow_html=True,
        )
    return st.session_state.active_page
