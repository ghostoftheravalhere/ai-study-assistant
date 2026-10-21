"""AI chat placeholder."""

from pages._placeholder import render_placeholder


def render_ai_chat() -> None:
    render_placeholder("AI Chat", "A focused space to ask questions about what you’re learning.", "✦", ["Ask questions about your materials", "Keep related conversations together", "Connect a language model in a later phase"])
