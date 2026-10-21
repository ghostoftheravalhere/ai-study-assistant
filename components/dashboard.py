"""Dashboard layout and data-backed summary cards."""

import html

import streamlit as st

from utils.database import get_dashboard_stats, get_recent_activity
from utils.database import get_connection
from utils.indexing_service import get_document_index_statuses
from utils.gemini_service import is_gemini_configured


FEATURES = [
    ("✦", "AI Chat", "Ask questions and explore ideas with your study companion.", "AI Chat", "feature-blue"),
    ("▤", "Study from PDFs", "Keep your learning materials organized in one place.", "My Documents", "feature-violet"),
    ("≋", "Smart Summaries", "Turn lengthy notes into clear, focused summaries.", "AI Summarizer", "feature-teal"),
    ("?", "Question Generator", "Create practice questions from your study material.", "Question Generator", "feature-amber"),
    ("✓", "Practice Quiz", "Check your understanding with interactive quizzes.", "Practice Quiz", "feature-rose"),
]


def _navigate(page: str) -> None:
    st.session_state.active_page = page
    st.rerun()


def _has_saved_workspace_data() -> bool:
    with get_connection() as connection:
        for table in (
            "documents", "chat_conversations", "quizzes", "quiz_attempts",
            "flashcard_decks", "flashcards", "planner_subjects", "planner_exams",
            "planner_tasks", "planner_sessions", "study_activity",
        ):
            if connection.execute(f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1)").fetchone()[0]:
                return True
    return False


def render_dashboard() -> None:
    """Render the welcome view, statistics, and feature shortcuts."""
    st.markdown('<div class="eyebrow">YOUR LEARNING SPACE</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Good to see you <span>👋</span></h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Your personal AI-powered learning companion. Build better study habits, one session at a time.</p>',
        unsafe_allow_html=True,
    )

    stats = get_dashboard_stats()
    if not st.session_state.get("hide_getting_started") and not _has_saved_workspace_data():
        with st.container(border=True):
            st.markdown("### Getting started")
            st.write("Your workspace is ready. Add a PDF to study locally; configure Gemini only when you want AI-generated study material.")
            first = st.columns(3)
            for column, label, page in (
                (first[0], "1 · Upload a PDF", "My Documents"),
                (first[1], "2 · Create flashcards", "Flashcards"),
                (first[2], "3 · Create a quiz", "Practice Quiz"),
            ):
                if column.button(label, key=f"onboarding_{page}", use_container_width=True):
                    _navigate(page)
            second = st.columns(3)
            for column, label, page in (
                (second[0], "4 · Make a study plan", "Study Planner"),
                (second[1], "Set up Gemini", "Settings"),
                (second[2], "Check optional setup", "System Health"),
            ):
                if column.button(label, key=f"onboarding_{page}", use_container_width=True):
                    _navigate(page)
            if not is_gemini_configured():
                st.caption("No Gemini key is configured. Dashboard, PDF storage, manual flashcards, quiz scoring, planner, backup, and exports remain local. Scanned-page OCR needs optional Tesseract; first-time semantic indexing may download its embedding model.")
            if st.button("Dismiss getting started", key="dismiss_getting_started"):
                st.session_state["hide_getting_started"] = True
                st.rerun()
    cards = [
        ("Documents Uploaded", stats["documents"], "▤", "stat-blue"),
        ("Questions Practiced", stats["questions"], "?", "stat-violet"),
        ("Quizzes Completed", stats["quizzes"], "✓", "stat-teal"),
        ("Average Quiz Score", f"{stats['average_score']}%" if stats["quizzes"] else "—", "↗", "stat-amber"),
    ]
    columns = st.columns(4, gap="medium")
    for column, (label, value, icon, color) in zip(columns, cards):
        with column:
            st.markdown(
                f'<div class="stat-card"><div class="stat-icon {color}">{icon}</div>'
                f'<div class="stat-label">{label}</div><div class="stat-value">{value}</div></div>',
                unsafe_allow_html=True,
            )

    index_statuses = get_document_index_statuses()
    indexed_count = sum(item["status"] == "indexed" for item in index_statuses)
    pending_count = sum(item["status"] in {"not_indexed", "out_of_date", "failed"} for item in index_statuses)
    st.caption(
        f"Semantic search: {indexed_count} indexed document{'s' if indexed_count != 1 else ''}"
        + (f" · {pending_count} ready to index" if pending_count else "")
    )

    st.markdown('<div class="section-heading"><div><h2>Quick actions</h2><p>Pick up where your curiosity takes you.</p></div></div>', unsafe_allow_html=True)
    action_cols = st.columns(3, gap="medium")
    actions = [("▦", "Plan study time", "Study Planner"), ("＋", "Add a document", "My Documents"), ("✓", "Take a practice quiz", "Practice Quiz")]
    for column, (icon, label, page) in zip(action_cols, actions):
        with column:
            if st.button(f"{icon}  {label}  →", key=f"quick_{page}", use_container_width=True):
                _navigate(page)

    st.markdown('<div class="section-heading feature-heading"><div><h2>Explore your study tools</h2><p>Everything you need to make learning feel a little easier.</p></div></div>', unsafe_allow_html=True)
    feature_cols = st.columns(3, gap="medium")
    for index, (icon, title, description, page, color) in enumerate(FEATURES):
        with feature_cols[index % 3]:
            st.markdown(
                f'<div class="feature-card"><div class="feature-icon {color}">{icon}</div>'
                f'<div class="feature-title">{title}</div><div class="feature-description">{description}</div></div>',
                unsafe_allow_html=True,
            )
            if st.button(f"Open {title} →", key=f"feature_{page}", use_container_width=True):
                _navigate(page)

    st.markdown('<div class="section-heading activity-heading"><div><h2>Recent activity</h2><p>Your latest study sessions will show up here.</p></div></div>', unsafe_allow_html=True)
    activity = get_recent_activity()
    if not activity:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">◷</div><strong>A fresh start</strong>'
            '<p>There’s no activity yet. Your completed study sessions will appear here.</p></div>',
            unsafe_allow_html=True,
        )
    else:
        for item in activity:
            score = f" · {item['score']}%" if item["score"] is not None else ""
            description = html.escape(str(item["description"]), quote=True)
            created_at = html.escape(str(item["created_at"]), quote=True)
            st.markdown(
                f"<div class='activity-row'><span class='activity-dot'></span><span>{description}{score}</span>"
                f"<span class='activity-time'>{created_at}</span></div>",
                unsafe_allow_html=True,
            )
