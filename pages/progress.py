"""Study progress page backed by local activity records."""

import streamlit as st

from pages._placeholder import render_placeholder
from utils.database import get_dashboard_stats, get_recent_activity


def render_progress() -> None:
    stats = get_dashboard_stats()
    activity = get_recent_activity(limit=20)
    st.markdown('<div class="eyebrow">YOUR LEARNING JOURNEY</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Study Progress</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">A clear picture of the activity recorded on this device.</p>', unsafe_allow_html=True)
    if not any((stats["documents"], stats["questions"], stats["quizzes"])):
        st.markdown('<div class="empty-state"><div class="empty-icon">↗</div><strong>Your progress starts here</strong><p>There are no study records yet. As you use completed features, progress will be tracked here.</p></div>', unsafe_allow_html=True)
        render_placeholder("Progress insights", "Progress charts and study trends will follow when learning activities are available.", "↗", ["Review quiz score trends", "See recent study activity", "Understand which topics need review"])
        return
    st.metric("Documents uploaded", stats["documents"])
    st.metric("Questions practiced", stats["questions"])
    st.metric("Quizzes completed", stats["quizzes"])
    if activity:
        st.subheader("Recent recorded activity")
        for item in activity:
            st.write(f"{item['created_at']} · {item['description']}")
