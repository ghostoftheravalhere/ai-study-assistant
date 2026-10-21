"""Entry point for the AI Study Assistant Streamlit application."""

from pathlib import Path

import streamlit as st

from components.dashboard import render_dashboard
from components.sidebar import NAVIGATION, render_sidebar
from study_pages.ai_chat import render_ai_chat
from study_pages.documents import render_documents
from study_pages.document_search import render_document_search
from study_pages.progress import render_progress
from study_pages.flashcards import render_flashcards
from study_pages.planner import render_planner
from study_pages.data_management import render_data_management
from study_pages.question_generator import render_question_generator
from study_pages.quiz import render_quiz
from study_pages.settings import render_settings
from study_pages.system_health import render_system_health
from study_pages.summarizer import render_summarizer
from utils.database import initialize_database


st.set_page_config(
    page_title="AI Study Assistant",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)


PAGE_RENDERERS = {
    "Dashboard": render_dashboard,
    "AI Chat": render_ai_chat,
    "My Documents": render_documents,
    "Semantic Search": render_document_search,
    "AI Summarizer": render_summarizer,
    "Question Generator": render_question_generator,
    "Practice Quiz": render_quiz,
    "Flashcards": render_flashcards,
    "Study Planner": render_planner,
    "Data Management": render_data_management,
    "Study Progress": render_progress,
    "Settings": render_settings,
    "System Health": render_system_health,
}


def get_page_renderer(page_name: str):
    """Return the registered renderer, or None for an unknown route."""
    return PAGE_RENDERERS.get(page_name)


def load_stylesheet() -> None:
    """Load the app's custom CSS when available."""
    stylesheet = Path(__file__).parent / "assets" / "style.css"
    try:
        st.markdown(f"<style>{stylesheet.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    except OSError:
        st.warning("The custom stylesheet could not be loaded. The app will use Streamlit's default styles.")


def main() -> None:
    """Initialize the local database and render the selected page."""
    try:
        initialize_database()
    except RuntimeError as error:
        from utils.safe_logging import log_failure
        log_failure("database_startup", error)
        st.error("StudyMate could not open or update the local database. It may be locked, damaged, or incompatible with this version. Close other database users. Do not delete the database; preserve the file and follow the Deployment, Diagnostics, and Recovery instructions in README.md to restore a verified backup.")
        st.stop()
    load_stylesheet()
    selected_page = render_sidebar()

    renderer = get_page_renderer(selected_page)
    if renderer is None:
        st.error("This page is unavailable. Choose a destination from the StudyMate sidebar.")
        return
    renderer()


if __name__ == "__main__":
    main()
