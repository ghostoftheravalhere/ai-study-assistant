"""Shared presentation for features planned after Phase 1."""

import streamlit as st


def render_placeholder(title: str, subtitle: str, icon: str, bullets: list[str]) -> None:
    st.markdown('<div class="eyebrow">COMING IN A LATER PHASE</div>', unsafe_allow_html=True)
    st.markdown(f'<h1 class="page-title">{icon} {title}</h1>', unsafe_allow_html=True)
    st.markdown(f'<p class="page-subtitle">{subtitle}</p>', unsafe_allow_html=True)
    st.markdown('<div class="placeholder-panel">', unsafe_allow_html=True)
    st.info("This feature is planned for a later phase. No AI service is connected in Phase 1.")
    st.markdown("**Planned capabilities**")
    for bullet in bullets:
        st.markdown(f"- {bullet}")
    st.markdown("</div>", unsafe_allow_html=True)
