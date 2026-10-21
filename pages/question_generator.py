"""Question generation placeholder."""

from pages._placeholder import render_placeholder


def render_question_generator() -> None:
    render_placeholder("Question Generator", "Create useful practice prompts from the topics you study.", "?", ["Generate questions from notes or a document", "Choose a question style and difficulty", "Save questions for later practice"])
