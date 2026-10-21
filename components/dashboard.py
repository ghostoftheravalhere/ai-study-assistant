"""Dashboard layout and data-backed summary cards."""

import streamlit as st

from utils.database import get_dashboard_stats, get_recent_activity


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


def render_dashboard() -> None:
    """Render the welcome view, statistics, and feature shortcuts."""
    st.markdown('<div class="eyebrow">YOUR LEARNING SPACE</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Good to see you <span>👋</span></h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Your personal AI-powered learning companion. Build better study habits, one session at a time.</p>',
        unsafe_allow_html=True,
    )

    stats = get_dashboard_stats()
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

    st.markdown('<div class="section-heading"><div><h2>Quick actions</h2><p>Pick up where your curiosity takes you.</p></div></div>', unsafe_allow_html=True)
    action_cols = st.columns(3, gap="medium")
    actions = [("＋", "Add a document", "My Documents"), ("✦", "Start an AI chat", "AI Chat"), ("✓", "Take a practice quiz", "Practice Quiz")]
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
            st.markdown(
                f"<div class='activity-row'><span class='activity-dot'></span><span>{item['description']}{score}</span>"
                f"<span class='activity-time'>{item['created_at']}</span></div>",
                unsafe_allow_html=True,
            )
