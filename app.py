"""Entry point for the AI Study Assistant Streamlit application."""

from pathlib import Path

import streamlit as st

from components.dashboard import render_dashboard
from components.sidebar import NAVIGATION, render_sidebar
from pages.ai_chat import render_ai_chat
from pages.documents import render_documents
from pages.progress import render_progress
from pages.question_generator import render_question_generator
from pages.quiz import render_quiz
from pages.settings import render_settings
from pages.summarizer import render_summarizer
from utils.database import initialize_database


st.set_page_config(
    page_title="AI Study Assistant",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_stylesheet() -> None:
    """Load the app's custom CSS when available."""
    stylesheet = Path(__file__).parent / "assets" / "style.css"
    try:
        st.markdown(f"<style>{stylesheet.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    except OSError:
        st.warning("The custom stylesheet could not be loaded. The app will use Streamlit's default styles.")


def main() -> None:
    """Initialize the local database and render the selected page."""
    initialize_database()
    load_stylesheet()
    selected_page = render_sidebar()

    page_renderers = {
        "Dashboard": render_dashboard,
        "AI Chat": render_ai_chat,
        "My Documents": render_documents,
        "AI Summarizer": render_summarizer,
        "Question Generator": render_question_generator,
        "Practice Quiz": render_quiz,
        "Study Progress": render_progress,
        "Settings": render_settings,
    }
    page_renderers.get(selected_page, render_dashboard)()


if __name__ == "__main__":
    main()
